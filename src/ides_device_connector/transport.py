from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.request
from typing import Any


class EventTransport:
    def __init__(self, url: str, secret: str, machine_id: str):
        self.url = url
        self.secret = secret.encode()
        self.machine_id = machine_id

    def send(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        body = json.dumps({"events": events}, separators=(",", ":"), sort_keys=True).encode()
        timestamp = str(int(time.time()))
        signature = hmac.new(self.secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        request = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "content-type": "application/json",
                "x-attendance-timestamp": timestamp,
                "x-attendance-signature": f"sha256={signature}",
                "x-device-machine-id": self.machine_id,
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
