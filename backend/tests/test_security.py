from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_hash_password_and_verify():
    hashed = hash_password("correct-horse-battery-staple")

    assert hashed != "correct-horse-battery-staple"
    assert verify_password("correct-horse-battery-staple", hashed) is True
    assert verify_password("wrong-password", hashed) is False


def test_create_and_decode_access_token_roundtrip():
    token = create_access_token({"sub": "user@example.com"})
    payload = decode_access_token(token)

    assert payload is not None
    assert payload["sub"] == "user@example.com"
    assert "exp" in payload


def test_decode_access_token_rejects_invalid_token():
    assert decode_access_token("not-a-valid-jwt-token") is None
