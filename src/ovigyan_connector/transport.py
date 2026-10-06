from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import time
import urllib.error
import urllib.request
from typing import Any


class EventTransport:
    def __init__(
        self,
        url: str,
        secret: str,
        machine_id: str,
        token: str = "",
        credential: str = "",
        timeout: int = 30,
        max_retries: int = 3,
        retry_backoff_seconds: float = 2.0,
        heartbeat_url: str | None = None,
    ):
        self.url = url
        self.secret = secret.encode()
        self.machine_id = machine_id
        self.token = token
        self.credential = credential
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self.heartbeat_url = heartbeat_url or url.removesuffix('/device-events') + '/heartbeat'

    def _send_request(self, url: str, body: bytes) -> dict[str, Any]:
        timestamp = str(int(time.time()))
        headers = {
            "content-type": "application/json",
            "x-attendance-timestamp": timestamp,
            "x-device-machine-id": self.machine_id,
        }
        if self.credential:
            # Paired connector: its own credential, accepted for terminals approved under it.
            headers["authorization"] = f"Bearer {self.credential}"
        elif self.token:
            headers["x-device-token"] = self.token
        else:
            signature = hmac.new(self.secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
            headers["x-attendance-signature"] = f"sha256={signature}"
        request = urllib.request.Request(url, data=body, method="POST", headers=headers)
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as error:
                if error.code < 500 and error.code not in {408, 429}:
                    raise
                if attempt >= self.max_retries:
                    raise
            except (urllib.error.URLError, TimeoutError, http.client.RemoteDisconnected):
                if attempt >= self.max_retries:
                    raise
            time.sleep(self.retry_backoff_seconds * (2**attempt))
        raise RuntimeError("unreachable")

    def send(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        body = json.dumps({"events": events}, separators=(",", ":"), sort_keys=True).encode()
        return self._send_request(self.url, body)

    def heartbeat(self) -> dict[str, Any]:
        return self._send_request(self.heartbeat_url, b"")
