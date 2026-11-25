from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

password_hash = PasswordHasher()
DUMMY_PASSWORD_HASH = password_hash.hash("constant-time-dummy-password")


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return password_hash.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
