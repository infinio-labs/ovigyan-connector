import pytest

from fake_cloud import CREDENTIAL, PAIR_KEY, FakeCloud
from ovigyan_connector.cloud import CloudClient, CloudError, normalize_server_url


@pytest.mark.parametrize(
    "typed, expected",
    [
        ("school.example.com", "https://school.example.com"),
        ("https://school.example.com/", "https://school.example.com"),
        ("  https://school.example.com/login?x=1#top ", "https://school.example.com"),
        ("HTTPS://School.Example.com:8443/app", "https://school.example.com:8443"),
        ("http://localhost:3200/anything", "http://localhost:3200"),
    ],
)
def test_server_addresses_are_reduced_to_the_site_origin(typed, expected):
    assert normalize_server_url(typed) == expected


@pytest.mark.parametrize("typed", ["", "   ", "http://school.example.com", "https://user:pw@school.example.com", "https://", "ftp://x.test", "https://x.test:notaport"])
def test_bad_addresses_get_a_plain_message(typed):
    with pytest.raises(CloudError) as caught:
        normalize_server_url(typed)
    assert caught.value.kind == "bad_address"


def test_plain_http_is_only_for_localhost_and_only_when_allowed(monkeypatch):
    monkeypatch.delenv("OVIGYAN_ALLOW_INSECURE_HTTP")
    with pytest.raises(CloudError):
        normalize_server_url("http://localhost:3200")


def test_pairing_returns_the_credential():
    with FakeCloud() as cloud:
        result = CloudClient(cloud.url).pair(PAIR_KEY)
        assert (result.connector_id, result.name, result.credential, result.poll_seconds) == ("c1", "Front office PC", CREDENTIAL, 60)
        body = cloud.requests[0]["body"]
        assert body["key"] == PAIR_KEY and body["os"] and body["hostname"] and body["version"]
        assert "authorization" not in {k.lower() for k in cloud.requests[0]["headers"]}


@pytest.mark.parametrize("key, kind", [("WRONG", "invalid_key"), ("EXPIRED", "expired")])
def test_pairing_failures_carry_the_servers_words(key, kind):
    with FakeCloud() as cloud:
        with pytest.raises(CloudError) as caught:
            CloudClient(cloud.url).pair(key)
        assert caught.value.kind == kind and caught.value.message


def test_a_used_key_is_refused():
    with FakeCloud() as cloud:
        client = CloudClient(cloud.url)
        client.pair(PAIR_KEY)
        with pytest.raises(CloudError) as caught:
            client.pair(PAIR_KEY)
        assert caught.value.kind == "invalid_key"


def test_rate_limiting_and_missing_endpoints_are_explained():
    with FakeCloud() as cloud:
        cloud.force_status, cloud.force_error = 429, "Too many attempts. Wait a minute."
        with pytest.raises(CloudError) as caught:
            CloudClient(cloud.url, max_retries=0).pair(PAIR_KEY)
        assert (caught.value.kind, caught.value.message) == ("rate_limited", "Too many attempts. Wait a minute.")
        cloud.force_status = 404
        with pytest.raises(CloudError) as caught:
            CloudClient(cloud.url, max_retries=0).pair(PAIR_KEY)
        assert caught.value.kind == "bad_address"


def test_an_unreachable_server_is_reported_after_retries():
    client = CloudClient("http://127.0.0.1:1", timeout=1, max_retries=1, retry_backoff_seconds=0)
    with pytest.raises(CloudError) as caught:
        client.pair(PAIR_KEY)
    assert caught.value.kind == "unreachable" and "127.0.0.1" in caught.value.message


def test_server_errors_are_retried_then_reported():
    with FakeCloud() as cloud:
        cloud.force_status, cloud.force_error = 503, "maintenance"
        with pytest.raises(CloudError) as caught:
            CloudClient(cloud.url, CREDENTIAL, max_retries=2, retry_backoff_seconds=0).config()
        assert caught.value.kind == "server" and len(cloud.requests) == 3


def test_authenticated_calls_send_the_credential_and_connector_details():
    with FakeCloud() as cloud:
        CloudClient(cloud.url, CREDENTIAL).config()
        headers = {k.lower(): v for k, v in cloud.requests[0]["headers"].items()}
        assert headers["authorization"] == f"Bearer {CREDENTIAL}"
        assert headers["x-connector-version"] and headers["x-connector-os"] and headers["x-connector-hostname"]


def test_a_connector_without_a_credential_cannot_call_authenticated_endpoints():
    with FakeCloud() as cloud:
        with pytest.raises(CloudError) as caught:
            CloudClient(cloud.url).config()
        assert caught.value.kind == "unauthorized" and cloud.requests == []


def test_a_revoked_credential_is_unauthorized_and_not_retried():
    with FakeCloud() as cloud:
        cloud.revoked = True
        with pytest.raises(CloudError) as caught:
            CloudClient(cloud.url, CREDENTIAL, max_retries=3, retry_backoff_seconds=0).config()
        assert caught.value.kind == "unauthorized" and len(cloud.requests) == 1


def test_config_is_parsed_with_safe_defaults():
    with FakeCloud() as cloud:
        cloud.poll_seconds = 5  # below the floor
        cloud.approve("SN1", ignore_before="2026-10-06T00:00:00.000Z")
        cloud.reported["SN2"] = {"serialNumber": "SN2"}
        cloud.rejected.add("SN3")
        config = CloudClient(cloud.url, CREDENTIAL).config()
        assert config.poll_seconds == 10
        assert config.devices["SN1"].ignore_before.isoformat() == "2026-10-06T00:00:00+00:00"
        assert (config.devices["SN1"].enabled, config.devices["SN1"].timezone) == (True, "Asia/Kolkata")
        assert config.pending == {"SN2"} and config.rejected == {"SN3"}


def test_report_devices_returns_each_serials_state():
    with FakeCloud() as cloud:
        cloud.approve("SN1")
        states = CloudClient(cloud.url, CREDENTIAL).report_devices([{"serialNumber": "SN1"}, {"serialNumber": "SN2"}])
        assert states == {"SN1": "approved", "SN2": "pending"}
