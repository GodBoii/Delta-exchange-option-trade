"""Print a new VAPID key pair for backend/.env (VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY).

Run once per deployment. Rotating the pair invalidates every stored browser
subscription, so each device has to turn notifications on again.
"""

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def main() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    print(f"VAPID_PUBLIC_KEY={_b64url(public)}")
    print(f"VAPID_PRIVATE_KEY={_b64url(private)}")


if __name__ == "__main__":
    main()
