"""Offline authentication-format regression for the disposable PostgreSQL fixture."""

import hashlib
import hmac
from base64 import b64decode, b64encode

from tests.infrastructure.postgresql_service import _scram_verifier


def test_scram_verifier_matches_rfc7677_exchange(monkeypatch):
    # RFC 7677 section 3's published SCRAM-SHA-256 exchange.
    monkeypatch.setattr(
        "tests.infrastructure.postgresql_service.secrets.token_bytes",
        lambda size: b64decode("W22ZaJ0SNY7soEsUEjb6gQ=="),
    )
    verifier = _scram_verifier("pencil")
    mechanism, parameters, keys = verifier.split("$")
    assert mechanism == "SCRAM-SHA-256"
    assert parameters == "4096:W22ZaJ0SNY7soEsUEjb6gQ=="
    stored_key, server_key = (b64decode(value) for value in keys.split(":"))
    nonce = "rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0"
    auth_message = (
        "n=user,r=rOprNGfwEbeRWgbNEkqO,"
        f"r={nonce},s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096,"
        f"c=biws,r={nonce}"
    ).encode("ascii")
    signature = hmac.digest(server_key, auth_message, "sha256")
    assert b64encode(signature).decode("ascii") == "6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="
    proof = b64decode("dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ=")
    client_signature = hmac.digest(stored_key, auth_message, "sha256")
    client_key = bytes(a ^ b for a, b in zip(proof, client_signature, strict=True))
    assert hashlib.sha256(client_key).digest() == stored_key
