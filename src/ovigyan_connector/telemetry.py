"""Crash and error reports to Bugsink (Sentry-compatible), so a failure at a school reaches us without a phone call.

What is sent: the exception, its stack, the connector version, the operating system and the school's Ovigyan
address (to know whose connector it is). What is not: local variables (they hold terminal passwords and the
credential), the PC's name, any user, request bodies, attendance records, or IP addresses (scrubbed from every
message and stack frame). Off when no address is built in or set, and `OVIGYAN_TELEMETRY=off` switches it off.
"""
from __future__ import annotations

import os
import platform
import re
from urllib.parse import urlparse

from . import __version__
from .telemetry_dsn import DSN

_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")
_OFF = {"off", "false", "0", "no", "disabled"}


def enabled(dsn: str) -> bool:
    return bool(dsn) and os.environ.get("OVIGYAN_TELEMETRY", "").strip().lower() not in _OFF


def _clean(text: object) -> object:
    return _IPV4.sub("<ip>", text) if isinstance(text, str) else text


def scrub(event: dict, _hint: object = None) -> dict | None:
    """Last line of defence before anything leaves the PC."""
    for key in ("server_name", "user", "request"):
        event.pop(key, None)
    event.pop("breadcrumbs", None)
    if "message" in event:
        event["message"] = _clean(event["message"])
    if isinstance(event.get("logentry"), dict):
        for key in ("message", "formatted"):
            if key in event["logentry"]:
                event["logentry"][key] = _clean(event["logentry"][key])
    for exception in (event.get("exception") or {}).get("values", []) or []:
        exception["value"] = _clean(exception.get("value"))
        for frame in (exception.get("stacktrace") or {}).get("frames", []) or []:
            for key in ("vars", "pre_context", "context_line", "post_context"):
                frame.pop(key, None)
    return event


def init(server: str | None = None, dsn: str | None = None, transport=None) -> bool:
    """Start reporting when allowed. Never raises: reporting must not be able to stop the connector."""
    dsn = DSN if dsn is None else dsn
    if not enabled(dsn):
        return False
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=dsn,
            release=f"ovigyan-connector@{__version__}",
            environment="production",
            send_default_pii=False,
            include_local_variables=False,
            server_name="",
            max_breadcrumbs=0,
            traces_sample_rate=0,
            before_send=scrub,
            transport=transport,
        )
        sentry_sdk.set_tag("os", platform.system().lower() or "unknown")
        set_site(server)
        return True
    except Exception:  # noqa: BLE001
        return False


def set_site(server: str | None) -> None:
    """Tag reports with the school's Ovigyan address (also called after pairing)."""
    try:
        import sentry_sdk

        if server and sentry_sdk.is_initialized():
            sentry_sdk.set_tag("site", urlparse(server).hostname or "unknown")
    except Exception:  # noqa: BLE001
        pass


def capture(error: BaseException | str) -> None:
    """Report a handled problem. A no-op until `init` has run."""
    try:
        import sentry_sdk

        if not sentry_sdk.is_initialized():
            return
        if isinstance(error, str):
            sentry_sdk.capture_message(error, level="warning")
        else:
            sentry_sdk.capture_exception(error)
    except Exception:  # noqa: BLE001
        pass
