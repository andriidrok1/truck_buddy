"""Secrets at rest (a fleet's Motive key): Fernet with CREDENTIALS_KEY, a urlsafe base64 32-byte key
(`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
Without the key nothing can be stored, and the API says so instead of writing plaintext."""
import os

from cryptography.fernet import Fernet, InvalidToken


class NoCredentialsKey(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = os.environ.get("CREDENTIALS_KEY", "").strip()
    if not key:
        raise NoCredentialsKey("CREDENTIALS_KEY is not set")
    return Fernet(key.encode())


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise NoCredentialsKey("CREDENTIALS_KEY does not open this secret") from e
