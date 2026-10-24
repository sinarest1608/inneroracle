"""Print fresh secrets for the daily-verse push feature (paste into the server's .env).

Run once per deployment: `python scripts/gen_vapid_keys.py`. The VAPID key pair must
never change after people subscribe (rotating it invalidates every subscription).
"""

from __future__ import annotations

import base64
import secrets

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def main() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    print(f"VAPID_PUBLIC_KEY={_b64url(public)}")
    print(f"VAPID_PRIVATE_KEY={_b64url(private)}")
    print(f"PUSH_ADMIN_TOKEN={secrets.token_urlsafe(32)}")
    print(f"PUSH_HMAC_KEY={secrets.token_urlsafe(32)}")
    print(f"PUSH_ROTATION_SEED={secrets.token_hex(8)}")


if __name__ == "__main__":
    main()
