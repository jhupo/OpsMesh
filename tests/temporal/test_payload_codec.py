"""S2 codec tests: encrypted payload round-trip and plaintext rejection."""

from __future__ import annotations

import asyncio

import pytest
from temporalio.api.common.v1 import Payload

from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.temporal.codec import EncryptedPayloadCodec

TEST_KEY = "JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0="


def codec() -> EncryptedPayloadCodec:
    return EncryptedPayloadCodec(SecretCipher(key=TEST_KEY, key_id="primary"))


def plain_payload(text: str) -> Payload:
    return Payload(data=text.encode("utf-8"))


def test_codec_round_trip_preserves_payload() -> None:
    payloads = [plain_payload("secret-workflow-input"), plain_payload("second")]
    encoded = asyncio.run(codec().encode(payloads))
    assert all(item.metadata["encoding"] == b"binary/opsmesh-encrypted" for item in encoded)
    decoded = asyncio.run(codec().decode(encoded))
    assert [item.data for item in decoded] == [item.data for item in payloads]


def test_codec_rejects_unencrypted_payloads() -> None:
    with pytest.raises(ValueError):
        asyncio.run(codec().decode([plain_payload("plaintext")]))


def test_codec_output_never_contains_plaintext() -> None:
    secret = "credential-shaped-secret-value"
    encoded = asyncio.run(codec().encode([plain_payload(secret)]))
    assert secret.encode("utf-8") not in encoded[0].data


def test_codec_bounds_encrypted_size_including_encoding_overhead() -> None:
    payload = plain_payload("x" * 1_100_000)
    with pytest.raises(ValueError, match="payload exceeds"):
        asyncio.run(codec().encode([payload]))


def test_codec_bounds_combined_batch_of_individually_valid_payloads() -> None:
    payload = plain_payload("x" * 1_000_000)
    assert len(asyncio.run(codec().encode([payload]))) == 1
    with pytest.raises(ValueError, match="batch exceeds"):
        asyncio.run(codec().encode([payload, payload]))
