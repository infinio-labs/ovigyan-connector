"""A notification-area icon that shows at a glance whether the connector is working, and opens its page.

It is a separate, tiny process (`ovigyan-connector tray`) so it can live in the signed-in user's session while
the connector itself runs as a background service. It reads the connector's status note (which holds nothing
secret) and never talks to the cloud or the terminals. It needs the optional `pystray` and `Pillow` packages.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from .branding import DEFAULT_NAME
from .state import StateStore

REFRESH_SECONDS = 10
STALE_AFTER_SECONDS = 180  # the connector writes a note every cycle; silence for this long means it is not running

COLOURS = {"ok": (26, 127, 55), "warn": (191, 135, 0), "bad": (207, 34, 46), "unknown": (110, 118, 129)}


def summarize(note: dict | None, now: float | None = None) -> tuple[str, str]:
    """(level, one-line text) for the icon colour and tooltip. `note` is status.json, or None when missing."""
    import time

    brand = (note or {}).get("brand") or {}
    name = brand.get("name") or DEFAULT_NAME
    if not note:
        return "unknown", f"{name}: not running"
    try:
        from datetime import datetime

        written = datetime.fromisoformat(str(note["at"]).replace("Z", "+00:00")).timestamp()
        if (now if now is not None else time.time()) - written > STALE_AFTER_SECONDS + int(note.get("poll_seconds", 60)):
            return "bad", f"{name}: stopped responding"
    except (KeyError, ValueError):
        pass
    state = note.get("connector")
    if state == "unpaired":
        return "warn", f"{name}: not connected yet. Open it to connect."
    if state == "revoked":
        return "bad", f"{name}: disconnected by the school. Open it to reconnect."
    if state == "offline":
        return "warn", f"{name}: offline, will retry"
    devices = note.get("devices") or []
    trouble = [d for d in devices if d.get("state") == "unreachable"]
    waiting = [d for d in devices if d.get("state") == "pending"]
    if trouble:
        return "bad", f"{name}: can't reach {len(trouble)} terminal{'s' if len(trouble) != 1 else ''}"
    if waiting:
        return "warn", f"{name}: {len(waiting)} terminal{'s' if len(waiting) != 1 else ''} waiting for approval"
    if not devices:
        return "warn", f"{name}: connected, no terminals added yet"
    return "ok", f"{name}: working"


def read_note(store: StateStore) -> dict | None:
    try:
        value = json.loads(store.status_path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def make_icon(level: str, size: int = 64, logo_png: str | None = None):
    """The status colour as a disc, or the school's logo with a small status dot in the corner."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    colour = COLOURS.get(level, COLOURS["unknown"]) + (255,)
    if logo_png:
        try:
            import base64
            import io

            logo = Image.open(io.BytesIO(base64.b64decode(logo_png))).convert("RGBA").resize((size, size))
            image.paste(logo, (0, 0), logo)
            dot = size * 11 // 32
            draw.ellipse((size - dot - 1, size - dot - 1, size - 1, size - 1), fill=colour, outline=(255, 255, 255, 255), width=max(2, size // 20))
            return image
        except (ValueError, OSError):
            pass  # a bad logo falls back to the plain status disc
    draw.ellipse((4, 4, size - 4, size - 4), fill=colour)
    inner = size // 4
    draw.ellipse((inner, inner, size - inner, size - inner), outline=(255, 255, 255, 255), width=max(3, size // 12))
    return image


def open_page() -> None:
    """Open the connector's page in the default browser, without a console window."""
    exe = sys.executable if getattr(sys, "frozen", False) else None
    command = [exe, "open"] if exe else [sys.executable, "-m", "ovigyan_connector.cli", "open"]
    subprocess.Popen(command, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def run_tray(store: StateStore | None = None, stop: threading.Event | None = None) -> int:
    try:
        import pystray
    except ImportError:
        print("The tray icon needs the optional 'pystray' and 'Pillow' packages (pip install ovigyan-connector[tray]).", file=sys.stderr)
        return 1
    store = store or StateStore()
    stop = stop or threading.Event()
    state = {"level": "unknown", "text": DEFAULT_NAME, "logo": None, "name": DEFAULT_NAME}

    def refresh(icon) -> None:
        note = read_note(store)
        level, text = summarize(note)
        brand = (note or {}).get("brand") or {}
        logo = brand.get("logo_png")
        if (level, text, logo) != (state["level"], state["text"], state["logo"]):
            state.update(level=level, text=text, logo=logo, name=brand.get("name") or DEFAULT_NAME)
            icon.icon, icon.title = make_icon(level, logo_png=logo), text
            icon.update_menu()

    menu = pystray.Menu(
        pystray.MenuItem(lambda _item: "Open " + state["name"], lambda *_: open_page(), default=True),
        pystray.MenuItem(lambda _item: state["text"], None, enabled=False),
        pystray.MenuItem("Hide this icon", lambda icon, *_: icon.stop()),
    )
    icon = pystray.Icon("ovigyan-connector", make_icon("unknown"), "Ovigyan Connector", menu)

    def watch(icon) -> None:
        icon.visible = True
        while not stop.is_set():
            refresh(icon)
            stop.wait(REFRESH_SECONDS)
        icon.stop()  # asked to stop from outside (a test, or the service shutting down)

    icon.run(setup=lambda i: threading.Thread(target=watch, args=(i,), daemon=True).start())
    stop.set()
    return 0
