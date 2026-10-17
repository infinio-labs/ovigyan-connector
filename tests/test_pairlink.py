import socket
import threading
from urllib.parse import parse_qs, urlparse

import pytest

from ovigyan_connector import cli, commands
from ovigyan_connector.pairlink import LinkError, fragment, is_link, parse_pair_link

KEY = "OVG-AAAAA-BBBBB-CCCCC-DDDDD"
LINK = f"ovigyan-connector://pair?server=https%3A%2F%2Fschool.example.com&key={KEY}"


def test_a_good_link_gives_the_site_and_the_code():
    assert parse_pair_link(LINK) == ("https://school.example.com", KEY)
    # The site may be given with a path (it is cut to its address), and the code in lower case.
    assert parse_pair_link(f"ovigyan-connector://pair?server=https://school.example.com/login&key={KEY.lower()}") == (
        "https://school.example.com",
        KEY,
    )


@pytest.mark.parametrize(
    "link, reason",
    [
        ("https://school.example.com/pair?key=" + KEY, "not an Ovigyan Connector pairing link"),
        ("ovigyan-connector://unpair?server=https://x.example&key=" + KEY, "not an Ovigyan Connector pairing link"),
        ("ovigyan-connector://pair?server=http://school.example.com&key=" + KEY, "https://"),
        ("ovigyan-connector://pair?server=https://user:pw@school.example.com&key=" + KEY, "web address"),
        ("ovigyan-connector://pair?server=&key=" + KEY, "web address"),
        ("ovigyan-connector://pair?server=https://school.example.com&key=OVG-1", "not valid"),
        ("ovigyan-connector://pair?server=https://school.example.com", "not valid"),
    ],
)
def test_anything_else_is_refused_with_a_plain_reason(link, reason):
    with pytest.raises(LinkError, match=reason):
        parse_pair_link(link)


def test_links_are_recognised_by_their_address_only():
    assert is_link(LINK) and is_link("  OVIGYAN-CONNECTOR://pair") and not is_link("run") and not is_link("--once")


def test_the_page_fragment_carries_both_values_encoded():
    parsed = parse_qs(fragment("https://school.example.com", KEY)[1:])
    assert parsed == {"server": ["https://school.example.com"], "key": [KEY]}


def test_windows_handing_over_a_link_opens_the_dashboard_and_waits_for_the_service(monkeypatch):
    seen = []
    monkeypatch.setattr(commands, "run_command", lambda argv: seen.append(argv) or 0)
    assert cli.main([LINK]) == 0
    assert seen == [["open", "--link", LINK, "--wait", "30"]]


def test_a_link_with_extra_words_is_refused_and_never_read_as_commands(monkeypatch, capsys):
    monkeypatch.setattr(commands, "run_command", lambda argv: pytest.fail("must not run anything"))
    assert cli.main([LINK, "run", "--home", "C:\\evil"]) == 2
    assert "extra arguments" in capsys.readouterr().err


def test_open_with_a_link_prints_the_page_address_with_the_values_after_the_hash(tmp_path, capsys):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    listener = socket.socket()
    listener.bind(("127.0.0.1", port))
    listener.listen()
    def accept_two():
        try:
            for _ in range(2):
                listener.accept()
        except OSError:
            pass  # the listener was closed by the test

    threading.Thread(target=accept_two, daemon=True).start()
    try:
        code = cli.main(["--home", str(tmp_path), "open", "--ui-port", str(port), "--no-browser", "--link", LINK])
    finally:
        listener.close()
    out = capsys.readouterr().out.strip()
    assert code == 0
    url = urlparse(out)
    assert url.netloc == f"127.0.0.1:{port}" and "t=" in url.query
    assert parse_qs(url.fragment) == {"server": ["https://school.example.com"], "key": [KEY]}


def test_open_with_a_bad_link_says_why_and_opens_nothing(tmp_path, capsys):
    code = cli.main(["--home", str(tmp_path), "open", "--no-browser", "--link", "ovigyan-connector://pair?server=http://x.example&key=" + KEY])
    assert code == 1 and "https://" in capsys.readouterr().err


def test_the_page_fills_in_the_form_from_a_link_and_clears_the_address():
    from ovigyan_connector.ui_page import PAGE

    for needle in ("location.hash", "history.replaceState", "applyLink", "already connected"):
        assert needle in PAGE
