"""Signed, data-only remote plugin packages. Private signing keys stay with publishers."""

import base64
import json

from pydantic import BaseModel, ConfigDict, Field

from opsmesh_plugin_sdk.contracts import PluginManifest


class SignedPluginPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: PluginManifest
    publisher_key_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,120}$")
    signature: str = Field(min_length=88, max_length=88)


def signing_bytes(manifest: PluginManifest, publisher_key_id: str) -> bytes:
    return json.dumps(
        {
            "purpose": "opsmesh.remote-plugin",
            "publisher_key_id": publisher_key_id,
            "manifest": manifest.model_dump(mode="json"),
        },
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sign_package(
    manifest: PluginManifest, publisher_key_id: str, private_key: bytes
) -> SignedPluginPackage:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    signature = Ed25519PrivateKey.from_private_bytes(private_key).sign(
        signing_bytes(manifest, publisher_key_id)
    )
    return SignedPluginPackage(
        manifest=manifest,
        publisher_key_id=publisher_key_id,
        signature=base64.b64encode(signature).decode("ascii"),
    )


def verify_package(package: SignedPluginPackage, public_key: bytes) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            base64.b64decode(package.signature, validate=True),
            signing_bytes(package.manifest, package.publisher_key_id),
        )
    except (ValueError, InvalidSignature) as exc:
        raise ValueError("Plugin package signature is invalid") from exc
