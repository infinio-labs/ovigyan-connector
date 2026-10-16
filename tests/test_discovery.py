import ipaddress
import socket
import threading

import pytest

from fake_cloud import PAIR_KEY, FakeCloud
from fake_zk_device import FakeZkDevice
from ovigyan_connector.discovery import local_networks, open_hosts
from ovigyan_connector.service import ConnectorService
from ovigyan_connector.state import StateStore

LOCAL = [ipaddress.ip_network("127.0.0.0/29")]  # 127.0.0.1 .. .6: the fake terminals live on 127.0.0.1


def test_only_private_networks_this_pc_is_on_are_swept_once_each():
    networks = local_networks(["192.168.1.20", "192.168.1.77", "10.0.5.9", "8.8.8.8", "127.0.0.1", "169.254.3.4", "fe80::1", "junk"])
    assert networks == [ipaddress.ip_network("192.168.1.0/24"), ipaddress.ip_network("10.0.5.0/24")]


def test_a_pc_on_too_many_networks_is_not_swept_everywhere():
    assert len(local_networks([f"10.0.{n}.5" for n in range(20)])) == 4


def test_open_hosts_finds_what_answers_on_the_port_and_nothing_else():
    with FakeZkDevice([], serial="A") as zk:
        assert open_hosts(LOCAL, zk.port, timeout=0.3) == ["127.0.0.1"]
    assert open_hosts(LOCAL, 1, timeout=0.2) == []


@pytest.fixture
def cloud():
    with FakeCloud() as fake:
        yield fake


def service(tmp_path, cloud=None):
    svc = ConnectorService(StateStore(tmp_path), http_retries=0, retry_backoff_seconds=0, probe_timeout=2)
    if cloud:
        svc.pair(cloud.url, PAIR_KEY)
    return svc


def test_a_terminal_on_the_network_is_found_with_its_serial_and_model(tmp_path):
    with FakeZkDevice([], serial="SN42", model="X2008") as zk:
        found = service(tmp_path).discover(LOCAL, zk.port)
    assert found == [{"host": "127.0.0.1", "port": zk.port, "serial": "SN42", "model": "X2008", "known": False, "needsPassword": False}]


def test_a_terminal_already_added_is_marked_so_and_not_probed_again(tmp_path, cloud):
    with FakeZkDevice([], serial="SN42") as zk:
        svc = service(tmp_path, cloud)
        svc.add_device("127.0.0.1", zk.port)
        svc.prober = lambda *a, **k: pytest.fail("a known terminal must not be probed again")
        found = svc.discover(LOCAL, zk.port)
    assert found[0]["known"] is True and found[0]["serial"] == "SN42"


def test_a_terminal_that_wants_a_password_is_listed_without_a_serial(tmp_path):
    with FakeZkDevice([], serial="SN7", require_password=True) as zk:
        found = service(tmp_path).discover(LOCAL, zk.port)
    assert found == [{"host": "127.0.0.1", "port": zk.port, "serial": None, "model": None, "known": False, "needsPassword": True}]


def test_something_else_listening_on_the_port_is_ignored(tmp_path):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    def accept_a_few():
        try:
            for _ in range(3):
                listener.accept()
        except OSError:
            pass  # the listener was closed by the test

    threading.Thread(target=accept_a_few, daemon=True).start()
    try:
        assert service(tmp_path).discover(LOCAL, listener.getsockname()[1]) == []
    finally:
        listener.close()
