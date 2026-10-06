"""One-time setup for signed auto-updates. Run it yourself, on a machine you trust:

    python scripts/make-update-key.py

It writes the PUBLIC key into src/ovigyan_connector/update_key.py (commit that) and the PRIVATE key to
update-signing-key.txt (owner-only). Store the private key as the GitHub Actions secret
CONNECTOR_UPDATE_SIGNING_KEY, then delete the file:

    gh secret set CONNECTOR_UPDATE_SIGNING_KEY --repo infinio-labs/ovigyan-connector < update-signing-key.txt
    shred -u update-signing-key.txt        # or: rm update-signing-key.txt

Anyone holding the private key can push code to every school's connector, so never commit or share it.
"""
import base64
import os
import re
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

root = Path(__file__).resolve().parent.parent
target = root / "src" / "ovigyan_connector" / "update_key.py"
if re.search(r'PUBLIC_KEY = "[^"]+"', target.read_text()) and "--force" not in sys.argv:
    sys.exit("A public key is already set. Rotating it strands every installed connector; pass --force only if you mean it.")

key = Ed25519PrivateKey.generate()
raw = serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
private = base64.b64encode(key.private_bytes(*raw)).decode()
public = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
out = root / "update-signing-key.txt"
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as stream:
    stream.write(private + "\n")
target.write_text(re.sub(r'PUBLIC_KEY = ".*"', f'PUBLIC_KEY = "{public}"', target.read_text()))
print(f"Public key written to {target.relative_to(root)}. Private key in {out.name}: store it as the secret, then delete it.")
