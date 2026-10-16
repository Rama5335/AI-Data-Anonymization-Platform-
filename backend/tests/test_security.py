from cryptography.fernet import Fernet

from app.security.credentials import CredentialCipher
from app.security.passwords import hash_password, verify_password
from app.security.tokens import create_access_token, verify_access_token


def test_credential_cipher_round_trip() -> None:
    cipher = CredentialCipher(Fernet.generate_key().decode("utf-8"))
    assert (
        cipher.decrypt(cipher.encrypt("Server=local;Password=secret"))
        == "Server=local;Password=secret"
    )


def test_password_and_token_primitives() -> None:
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed)
    secret = "test-secret-with-at-least-thirty-two-bytes"
    token = create_access_token("user-id", secret, 5)
    assert verify_access_token(token, secret) == "user-id"
