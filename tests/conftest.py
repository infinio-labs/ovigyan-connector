import pytest


@pytest.fixture(autouse=True)
def _allow_local_http(monkeypatch):
    # The fake cloud speaks plain HTTP on localhost; real sites must be HTTPS.
    monkeypatch.setenv("OVIGYAN_ALLOW_INSECURE_HTTP", "true")


@pytest.fixture(autouse=True)
def _quick_terminal_checks(monkeypatch):
    # A closed port takes several seconds to give up on Windows; the tests only need it to fail.
    monkeypatch.setenv("OVIGYAN_PROBE_TIMEOUT", "2")
