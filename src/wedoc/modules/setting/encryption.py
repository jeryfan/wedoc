"""AES-256-GCM codec for AI-config secrets — ports utils/ai-config-encryption.ts.

Provider API keys inside ``setting.aiConfig`` are stored encrypted at rest under
a cipher-prefixed envelope and decrypted on read, so a database written by the
reference backend and by wedoc stay interchangeable. The envelope is
``prefix + base64(iv || tag || ciphertext)`` with a 12-byte IV and a 16-byte GCM
tag; the AES-256 key is HKDF-SHA256 of the resolved root secret.
"""

import base64
import os
from collections.abc import Callable
from functools import cache
from typing import Any

import structlog
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ...compat import ai_config_cipher_prefix, ai_config_hkdf_info
from ...config import get_settings

logger = structlog.get_logger(__name__)

_IV_BYTES = 12
_TAG_BYTES = 16
_KEY_BYTES = 32
_LEGACY_DEFAULT_ROOT = "defaultSecretKey"

_SecretMapper = Callable[[str], str]


def is_encrypted_ai_config_value(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(ai_config_cipher_prefix())


@cache
def _derive_key(root: str) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_BYTES,
        salt=b"",
        info=ai_config_hkdf_info().encode(),
    ).derive(root.encode())


def _resolve_roots() -> list[str]:
    dedicated = os.environ.get("BACKEND_AI_CONFIG_ENCRYPTION_SECRET")
    primary = dedicated or get_settings().secret_key or _LEGACY_DEFAULT_ROOT
    old = os.environ.get("BACKEND_AI_CONFIG_ENCRYPTION_SECRET_OLD")
    roots: list[str] = []
    for root in (primary, old):
        if root and root not in roots:
            roots.append(root)
    return roots


class AiConfigValueCodec:
    """keys[0] encrypts; every key participates in decryption, in order."""

    def __init__(self, roots: list[str]) -> None:
        self._keys = [_derive_key(root) for root in roots]

    def encrypt_value(self, plaintext: str) -> str:
        if is_encrypted_ai_config_value(plaintext):
            return plaintext
        iv = os.urandom(_IV_BYTES)
        encryptor = Cipher(algorithms.AES(self._keys[0]), modes.GCM(iv)).encryptor()
        enc = encryptor.update(plaintext.encode()) + encryptor.finalize()
        envelope = iv + encryptor.tag + enc
        return ai_config_cipher_prefix() + base64.b64encode(envelope).decode()

    def decrypt_value_strict(self, value: str) -> str:
        if not is_encrypted_ai_config_value(value):
            return value
        for key in self._keys:
            try:
                return self._decrypt_with(key, value)
            except Exception:
                continue
        raise ValueError("AI config secret decryption failed")

    def _decrypt_with(self, key: bytes, value: str) -> str:
        buf = base64.b64decode(value[len(ai_config_cipher_prefix()) :])
        iv = buf[:_IV_BYTES]
        tag = buf[_IV_BYTES : _IV_BYTES + _TAG_BYTES]
        enc = buf[_IV_BYTES + _TAG_BYTES :]
        decryptor = Cipher(algorithms.AES(key), modes.GCM(iv, tag)).decryptor()
        return (decryptor.update(enc) + decryptor.finalize()).decode()


def get_ai_config_value_codec() -> AiConfigValueCodec:
    return AiConfigValueCodec(_resolve_roots())


def _map_field(obj: dict[str, Any], key: str, fn: _SecretMapper) -> dict[str, Any]:
    value = obj.get(key)
    if isinstance(value, str) and value != "":
        obj[key] = fn(value)
    return obj


def _map_concurrency_group(group: Any, fn: _SecretMapper) -> Any:
    if not isinstance(group, dict) or not isinstance(group.get("keys"), list):
        return group
    return {
        **group,
        "keys": [
            _map_field(dict(entry), "apiKey", fn) if isinstance(entry, dict) else entry
            for entry in group["keys"]
        ],
    }


def map_ai_config_secrets(config: Any, fn: _SecretMapper) -> Any:
    if not isinstance(config, dict):
        return config
    nxt: dict[str, Any] = dict(config)

    providers = nxt.get("llmProviders")
    if isinstance(providers, list):
        nxt["llmProviders"] = [
            _map_field(dict(provider), "apiKey", fn) if isinstance(provider, dict) else provider
            for provider in providers
        ]

    _map_field(nxt, "aiGatewayApiKey", fn)

    gateway_keys = nxt.get("aiGatewayApiKeys")
    if isinstance(gateway_keys, list):
        nxt["aiGatewayApiKeys"] = [
            fn(key) if isinstance(key, str) and key != "" else key for key in gateway_keys
        ]

    groups = nxt.get("concurrencyGroups")
    if isinstance(groups, list):
        nxt["concurrencyGroups"] = [_map_concurrency_group(group, fn) for group in groups]

    vertex = nxt.get("vertexByokCredential")
    if isinstance(vertex, dict):
        credential = dict(vertex)
        google = credential.get("googleCredentials")
        if isinstance(google, dict):
            credential["googleCredentials"] = _map_field(dict(google), "privateKey", fn)
        nxt["vertexByokCredential"] = credential

    realtime = nxt.get("realtimeTranscription")
    if isinstance(realtime, dict):
        nxt["realtimeTranscription"] = _map_field(dict(realtime), "apiKey", fn)

    return nxt


def encrypt_ai_config_secrets(config: Any) -> Any:
    codec = get_ai_config_value_codec()
    return map_ai_config_secrets(config, codec.encrypt_value)


def decrypt_ai_config_secrets(config: Any, source: str = "unknown") -> Any:
    codec = get_ai_config_value_codec()

    def _decrypt(value: str) -> str:
        try:
            return codec.decrypt_value_strict(value)
        except Exception:
            logger.error("ai_config_secret_decrypt_failed", source=source)
            return value

    return map_ai_config_secrets(config, _decrypt)
