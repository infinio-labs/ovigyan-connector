import pytest

from ovigyan_connector import telemetry
from ovigyan_connector.telemetry import capture, enabled, init, scrub

DSN = "https://key@bugs.example.test/1"


@pytest.fixture(autouse=True)
def _reset():
    import sentry_sdk

    yield
    sentry_sdk.get_client().close()
    sentry_sdk.init()  # back to an inert client


class Sink:
    def __init__(self):
        self.events = []

    def __call__(self, event):
        self.events.append(event)


def started(monkeypatch=None, server="https://school.example.edu/some/page"):
    import sentry_sdk
    from sentry_sdk.transport import Transport

    sink = Sink()

    class Capture(Transport):
        def capture_envelope(self, envelope):
            for item in envelope.items:
                if item.headers.get("type") == "event":
                    sink(item.payload.json)

    assert init(server, DSN, transport=Capture())
    return sink, sentry_sdk


class TestSwitch:
    def test_nothing_is_sent_without_an_address(self):
        assert init(dsn="") is False and capture(RuntimeError("x")) is None

    @pytest.mark.parametrize("value", ["off", "OFF", "false", "0", " no "])
    def test_the_owner_can_switch_it_off(self, monkeypatch, value):
        monkeypatch.setenv("OVIGYAN_TELEMETRY", value)
        assert enabled(DSN) is False and init(dsn=DSN) is False

    def test_on_by_default_when_an_address_is_built_in(self, monkeypatch):
        monkeypatch.delenv("OVIGYAN_TELEMETRY", raising=False)
        assert enabled(DSN) is True

    def test_a_broken_address_never_raises(self):
        assert init(dsn="not a dsn") is False


class TestWhatLeaves:
    def test_the_report_has_the_error_version_and_school_but_no_secrets_ips_or_hostname(self):
        sink, sentry = started()

        def poll_terminal(password=123456, credential="ovigyan_conn_secretvalue"):
            raise ConnectionError("Can't reach 192.168.1.50:4370")

        try:
            poll_terminal()
        except ConnectionError as error:
            capture(error)
        sentry.flush()
        (event,) = sink.events
        text = str(event)
        assert event["release"].startswith("ovigyan-connector@") and event["tags"]["site"] == "school.example.edu"
        assert "ConnectionError" in text and "<ip>" in text
        for leaked in ("192.168.1.50", "ovigyan_conn_secretvalue", "123456"):
            assert leaked not in text, leaked
        assert not event.get("server_name") and "user" not in event and "request" not in event
        assert all("vars" not in frame for value in event["exception"]["values"] for frame in value["stacktrace"]["frames"])

    def test_a_handled_problem_can_be_reported_as_a_message(self):
        sink, sentry = started()
        capture("update failed: could not reach 10.0.0.2")
        sentry.flush()
        assert sink.events[0]["level"] == "warning" and "10.0.0.2" not in str(sink.events[0])


class TestScrub:
    def test_removes_identity_and_context_lines(self):
        event = {
            "server_name": "FRONT-OFFICE-PC", "user": {"ip_address": "1.2.3.4"}, "request": {"url": "x"}, "breadcrumbs": [1],
            "message": "failed at 10.1.1.1:4370",
            "exception": {"values": [{"value": "bad 10.1.1.1", "stacktrace": {"frames": [{"vars": {"p": 1}, "context_line": "pw = 5", "lineno": 3}]}}]},
        }
        out = scrub(event)
        assert out["message"] == "failed at <ip>" and out["exception"]["values"][0]["value"] == "bad <ip>"
        assert out["exception"]["values"][0]["stacktrace"]["frames"] == [{"lineno": 3}]
        assert not ({"server_name", "user", "request", "breadcrumbs"} & set(out))


class TestSourceForFrozenBuilds:
    def frame(self, module="ovigyan_connector.state", lineno=3):
        return {"module": module, "lineno": lineno, "vars": {"x": 1}}

    def test_our_frames_get_the_surrounding_code_and_others_get_none(self):
        ours, theirs = self.frame(), self.frame("requests.api")
        theirs["context_line"] = "secret = 1"
        scrub({"exception": {"values": [{"stacktrace": {"frames": [ours, theirs]}}]}})
        assert ours["context_line"] == telemetry._lines("ovigyan_connector.state")[2] and "vars" not in ours
        assert len(ours["pre_context"]) == 2 and len(ours["post_context"]) == 5
        assert not ({"context_line", "pre_context", "vars"} & set(theirs))

    def test_the_package_itself_and_out_of_range_lines_are_handled(self):
        init, far = self.frame("ovigyan_connector", 1), self.frame(lineno=10**6)
        scrub({"exception": {"values": [{"stacktrace": {"frames": [init, far]}}]}})
        assert "context_line" in init and "context_line" not in far

    def test_a_packaged_build_reads_the_source_it_carries(self, tmp_path, monkeypatch):
        (tmp_path / "ovigyan_connector").mkdir()
        (tmp_path / "ovigyan_connector" / "state.py").write_text("one\ntwo\nthree\nfour\n")
        monkeypatch.setattr("sys.frozen", True, raising=False)
        monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)
        telemetry._lines.cache_clear()
        frame = self.frame(lineno=2)
        scrub({"exception": {"values": [{"stacktrace": {"frames": [frame]}}]}})
        assert (frame["pre_context"], frame["context_line"], frame["post_context"]) == (["one"], "two", ["three", "four"])
        telemetry._lines.cache_clear()
