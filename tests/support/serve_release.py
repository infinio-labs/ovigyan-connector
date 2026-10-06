"""A throwaway release host for the installer smoke test: signs a manifest for the given files and serves it.

    python serve_release.py --version 1.2.3 --port 8765 --file setup.exe=ovigyan-connector-setup-1.2.3.exe ...

Prints the PUBLIC key on the first line, then "ready". With --wrong-key the manifest is signed by a different key
than the one printed, to prove the connector refuses it.
"""
import argparse
import base64
import functools
import hashlib
import http.server
import json
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

parser = argparse.ArgumentParser()
parser.add_argument("--version", required=True)
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--file", action="append", default=[], help="SOURCE=PUBLISHED_NAME")
parser.add_argument("--wrong-key", action="store_true")
args = parser.parse_args()

root = Path(tempfile.mkdtemp()) / f"v{args.version}"
root.mkdir()
files = {}
for item in args.file:
    source, name = item.split("=", 1)
    data = Path(source).read_bytes()
    (root / name).write_bytes(data)
    files[name] = hashlib.sha256(data).hexdigest()
manifest = json.dumps({"version": args.version, "files": files}).encode()
(root / "manifest.json").write_bytes(manifest)
shown, signer = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
signer = signer if args.wrong_key else shown
(root / "manifest.json.sig").write_bytes(base64.b64encode(signer.sign(manifest)))
public = shown.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
print(base64.b64encode(public).decode(), flush=True)
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root.parent))
server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler)
print("ready", flush=True)
server.serve_forever()
