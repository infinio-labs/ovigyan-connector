"""The school's look: its name, logo and colour, as sent by its Ovigyan site.

Everything here is untrusted text from the network, so it is cleaned before it is stored or shown: the name is
plain text, the colour must be #rrggbb, and the logo must be a small PNG (never SVG, which can carry scripts).
Without branding the connector keeps its Ovigyan look.
"""
from __future__ import annotations

import base64
import re
import struct
import urllib.request

DEFAULT_NAME = "Ovigyan Connector"
MAX_LOGO_BYTES = 256 * 1024
MAX_LOGO_SIDE = 1024
_HEX = re.compile(r"^#[0-9a-f]{6}$")
_PNG = b"\x89PNG\r\n\x1a\n"


def parse_branding(raw: object) -> dict:
    """{name, color, logo_url} from the server's `branding` block; unusable fields are dropped."""
    if not isinstance(raw, dict):
        return {}
    name = raw.get("name")
    color = raw.get("color")
    url = raw.get("logoUrl")
    clean: dict = {}
    if isinstance(name, str) and name.strip():
        clean["name"] = " ".join(name.split())[:80]
    if isinstance(color, str) and _HEX.match(color.strip().lower()):
        clean["color"] = color.strip().lower()
    if isinstance(url, str) and url.startswith(("https://", "http://")) and len(url) <= 2048:
        clean["logo_url"] = url
    return clean


def contrast_text(color: str) -> str:
    """Black or white, whichever reads better on `color` (WCAG relative luminance)."""
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return "#000000" if 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.179 else "#ffffff"


def is_small_png(data: bytes) -> bool:
    if len(data) < 24 or len(data) > MAX_LOGO_BYTES or not data.startswith(_PNG) or data[12:16] != b"IHDR":
        return False
    width, height = struct.unpack(">II", data[16:24])
    return 0 < width <= MAX_LOGO_SIDE and 0 < height <= MAX_LOGO_SIDE


def fetch_logo(url: str, timeout: float = 10) -> str | None:
    """The logo as base64, or None when it can't be fetched or isn't an acceptable PNG."""
    try:
        request = urllib.request.Request(url, headers={"accept": "image/png"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(MAX_LOGO_BYTES + 1)
    except (OSError, ValueError):
        return None
    return base64.b64encode(data).decode() if is_small_png(data) else None
