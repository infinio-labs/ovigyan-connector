"""Finding attendance terminals on this PC's own network, so staff pick one from a list instead of typing an IP address.

Only the private networks this PC is already on are looked at (a /24 around each of its addresses), one TCP port, and
nothing is sent to a host that does not answer on it. Terminals normally listen on 4370.
"""
from __future__ import annotations

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor

DEFAULT_PORT = 4370
MAX_NETWORKS = 4  # a PC on more private networks than this is not a school PC we should be sweeping


def _own_addresses() -> list[str]:
    found: set[str] = set()
    try:
        found.update(item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    try:  # the address used to reach the outside world; connecting a UDP socket sends nothing
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            found.add(probe.getsockname()[0])
    except OSError:
        pass
    return sorted(found)


def local_networks(addresses: list[str] | None = None) -> list[ipaddress.IPv4Network]:
    """The /24 around each private address of this PC (not loopback, not link-local), without repeats."""
    networks: list[ipaddress.IPv4Network] = []
    for text in addresses if addresses is not None else _own_addresses():
        try:
            address = ipaddress.ip_address(text)
        except ValueError:
            continue
        if address.version != 4 or not address.is_private or address.is_loopback or address.is_link_local:
            continue
        network = ipaddress.ip_network(f"{address}/24", strict=False)
        if network not in networks:
            networks.append(network)
    return networks[:MAX_NETWORKS]


def open_hosts(networks: list, port: int = DEFAULT_PORT, timeout: float = 0.5, workers: int = 64) -> list[str]:
    """Hosts in these networks that accept a connection on `port`, in address order."""
    hosts = [str(host) for network in networks for host in network.hosts()]

    def check(host: str) -> str | None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(timeout)
            return host if probe.connect_ex((host, port)) == 0 else None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        answered = [host for host in pool.map(check, hosts) if host]
    return sorted(answered, key=ipaddress.ip_address)
