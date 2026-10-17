"""The `ovigyan-connector://pair?server=...&key=...` link Ovigyan shows next to a connection code.

Windows hands the link to this program (the installer registers the address). It only ever opens the connector's own
page with the site and code filled in: a link on a web page must not be able to connect this PC to a site on its own,
so the person still presses Connect.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse

from .cloud import CloudError, normalize_server_url

SCHEME = "ovigyan-connector"
KEY = re.compile(r"^OVG(-[A-Z0-9]{5}){4}$")


class LinkError(ValueError):
    """A link that is not a valid pairing link, described for the person who clicked it."""


def is_link(argument: str) -> bool:
    return argument.strip().lower().startswith(f"{SCHEME}:")


def parse_pair_link(link: str) -> tuple[str, str]:
    """(the site's address, the connection code), both checked. Raises LinkError."""
    parsed = urlparse(link.strip())
    if parsed.scheme.lower() != SCHEME or parsed.netloc.lower() != "pair":
        raise LinkError("That is not an Ovigyan Connector pairing link.")
    query = parse_qs(parsed.query)
    try:
        server = normalize_server_url((query.get("server") or [""])[0])
    except CloudError as error:
        raise LinkError(error.message) from None
    key = (query.get("key") or [""])[0].strip().upper()
    if not KEY.match(key):
        raise LinkError("The connection code in that link is not valid. Get a new one in Ovigyan.")
    return server, key


def fragment(server: str, key: str) -> str:
    """What the page reads (and then removes from the address bar) to fill in its form. It never leaves this PC."""
    return "#" + urlencode({"server": server, "key": key})
