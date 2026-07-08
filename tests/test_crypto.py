from api.crypto import decrypt, encrypt, redact


def test_encrypt_decrypt_roundtrip():
    secret = "AEB-very-long-espn-s2-value-%2Fwith%3Dencoding"
    token = encrypt(secret)
    assert token != secret  # actually encrypted
    assert secret not in token
    assert decrypt(token) == secret


def test_redact_never_leaks_value():
    assert "supersecret" not in redact("supersecret")
    assert redact("") == "<empty>"
    assert redact(None) == "<empty>"
