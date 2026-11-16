"""Generate an RS256 JWT key pair and a random OTP secret.

Run from the repository root:
    python scripts/gen_keys.py

Force regeneration:
    python scripts/gen_keys.py --force
"""

import argparse
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[1]
KEYS = ROOT / "keys"

PRIVATE = KEYS / "jwt_private.pem"
PUBLIC = KEYS / "jwt_public.pem"


def keys_are_valid() -> bool:
    """Check whether both keys exist, are valid RSA keys, and match."""

    if not PRIVATE.is_file() or not PUBLIC.is_file():
        return False

    try:
        private_key = serialization.load_pem_private_key(
            PRIVATE.read_bytes(),
            password=None,
        )

        public_key = serialization.load_pem_public_key(
            PUBLIC.read_bytes()
        )

        if not isinstance(private_key, rsa.RSAPrivateKey):
            return False

        if not isinstance(public_key, rsa.RSAPublicKey):
            return False

        return (
            private_key.public_key().public_numbers()
            == public_key.public_numbers()
        )

    except (ValueError, TypeError):
        return False


def generate_keys() -> None:
    """Generate and save a new RSA-2048 key pair."""

    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    PRIVATE.write_bytes(private_pem)
    PUBLIC.write_bytes(public_pem)

    print(f"Keys successfully generated in: {KEYS}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate RS256 JWT keys."
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate keys even if valid keys already exist.",
    )

    args = parser.parse_args()

    KEYS.mkdir(parents=True, exist_ok=True)

    if not args.force and keys_are_valid():
        print("Valid JWT key pair already exists. Not overwriting.")
        return

    if args.force:
        print("Force enabled. Regenerating JWT keys.")
    else:
        print("Keys missing, empty, invalid, or mismatched.")
        print("Generating a new key pair.")

    generate_keys()

    print("\nAdd this to backend/.env:")
    print(f"OTP_HMAC_SECRET={secrets.token_hex(32)}")


if __name__ == "__main__":
    main()
