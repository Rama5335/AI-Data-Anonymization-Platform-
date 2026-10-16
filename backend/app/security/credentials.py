"""Fernet encryption for stored database connection credentials."""

from cryptography.fernet import Fernet, InvalidToken


class CredentialCipher:
    """Encrypt and decrypt text using a validated environment-provided Fernet key."""

    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Credential encryption key is not a valid Fernet key.") from exc

    def encrypt(self, plaintext: str) -> str:
        """Encrypt non-empty plaintext for storage."""
        if not plaintext:
            raise ValueError("Credential plaintext must not be empty.")
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt stored ciphertext without exposing it in logs."""
        try:
            return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Credential ciphertext cannot be decrypted.") from exc
