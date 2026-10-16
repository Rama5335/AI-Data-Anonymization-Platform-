"""Password hashing helpers based on Argon2."""

from pwdlib import PasswordHash

_password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    """Return a one-way Argon2 hash for a non-empty user password."""
    if not password:
        raise ValueError("Password must not be empty.")
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against its stored hash."""
    return bool(password) and _password_hash.verify(password, password_hash)
