"""
crypto_utils.py
~~~~~~~~~~~~~~~

Helper functions around cryptography.fernet for encrypting / decrypting the
passwords of electronic key files.

Environment variable
--------------------
FERNET_SECRET_KEY   – 32-byte URL-safe base64 string generated once and kept
                      on the machine (or in CI secret storage).

CLI usage (examples)
--------------------
$ python -m utils.crypto_utils --key                  # generate new key
$ python -m utils.crypto_utils --encrypt "myPass123"  # -> token
$ python -m utils.crypto_utils --decrypt "<token>"    # -> clear-text
"""
from __future__ import annotations

import os
import sys
from argparse import ArgumentParser
from typing import Final

from cryptography.fernet import Fernet, InvalidToken

FERNET_ENV: Final[str] = "FERNET_SECRET_KEY"


# --------------------------------------------------------------------------- #
# Core helpers
# --------------------------------------------------------------------------- #
def _get_key() -> bytes:
    key = os.getenv(FERNET_ENV)
    if not key:
        raise RuntimeError(
            f"Environment variable {FERNET_ENV} is not set. "
            "Generate it via `python -m utils.crypto_utils --key` "
            "and export before running the bot."
        )
    return key.encode()


def generate_key() -> str:
    """Return fresh Fernet key (URL-safe base64)."""
    return Fernet.generate_key().decode()


def encrypt(plaintext: str) -> str:
    """Encrypt *plaintext* using key from env → Fernet token."""
    f = Fernet(_get_key())
    return f.encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt Fernet *token* → clear-text."""
    f = Fernet(_get_key())
    try:
        return f.decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Invalid Fernet token or wrong key") from exc


# --------------------------------------------------------------------------- #
# CLI helper (python -m utils.crypto_utils ...)
# --------------------------------------------------------------------------- #
def _cli(argv: list[str] | None = None) -> None:  # pragma: no cover
    p = ArgumentParser("Fernet helper for e-consul bot")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--encrypt", metavar="PLAINTEXT", help="Encrypt given string")
    g.add_argument("--decrypt", metavar="TOKEN", help="Decrypt given token")
    g.add_argument("--key", action="store_true", help="Generate new key and exit")
    args = p.parse_args(argv)

    if args.key:
        print(generate_key())
        return

    if args.encrypt:
        print(encrypt(args.encrypt))
    elif args.decrypt:
        print(decrypt(args.decrypt))


if __name__ == "__main__":  # pragma: no cover
    _cli()
