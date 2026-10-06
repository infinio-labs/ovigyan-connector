import socket

import pytest

from fake_zk_device import FakeZkDevice
from ovigyan_connector.devices import ProbeError, probe, validate_address


def test_probe_reads_serial_model_and_firmware():
    with FakeZkDevice([], serial="NFZ824090078", model="X2008", firmware="Ver 6.60") as device:
        info = probe("127.0.0.1", device.port, 0, timeout=5)
    assert (info.serial, info.model, info.firmware) == ("NFZ824090078", "X2008", "Ver 6.60")


def test_probe_reports_an_unreachable_terminal_in_plain_words():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(ProbeError) as caught:
        probe("127.0.0.1", port, 0, timeout=2)
    assert caught.value.kind == "unreachable"
    assert f"127.0.0.1:{port}" in caught.value.message and "same network" in caught.value.message


def test_probe_explains_a_wrong_communication_password():
    with FakeZkDevice([], require_password=True) as device:
        with pytest.raises(ProbeError) as caught:
            probe("127.0.0.1", device.port, 99, timeout=5)
    assert caught.value.kind == "wrong_password" and "Comm Key" in caught.value.message


def test_a_terminal_that_gives_no_serial_is_not_supported():
    with FakeZkDevice([], serial="") as device:
        with pytest.raises(ProbeError) as caught:
            probe("127.0.0.1", device.port, 0, timeout=5)
    assert caught.value.kind == "unsupported"


@pytest.mark.parametrize("host", ["192.168.1.50", "terminal-1", "term.school.local", "::1", " 10.0.0.1 "])
def test_valid_addresses_pass(host):
    assert validate_address(host, 4370)[1] == 4370


@pytest.mark.parametrize("host", ["", "   ", "http://10.0.0.1", "10.0.0.1/24", "bad host", "-bad", "a" * 300, None])
def test_invalid_addresses_are_refused(host):
    with pytest.raises(ProbeError) as caught:
        validate_address(host, 4370)
    assert caught.value.kind == "bad_address"


@pytest.mark.parametrize("port", [0, -1, 65536, "abc", None])
def test_invalid_ports_are_refused(port):
    with pytest.raises(ProbeError) as caught:
        validate_address("10.0.0.1", port)
    assert caught.value.kind == "bad_address"


def test_a_port_given_as_text_is_accepted():
    assert validate_address("10.0.0.1", "4370") == ("10.0.0.1", 4370)
