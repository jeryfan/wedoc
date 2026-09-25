"""Space AI integration config normalization — ports ai-integration-config.ts.

The create/update integration responses echo `config` as a JSON *string*, so its
byte layout must match the reference. Upstream the string is produced by:
zod parse (reorders keys to schema order, applies `models` default) →
normalizeSpaceAIIntegrationConfig (fills provider names) → encrypt → decrypt.
wedoc stores plaintext, so we reproduce the schema key order + name-fill directly
and serialize compactly (JS `JSON.stringify` layout: no spaces, unicode kept).
"""

import json
import secrets
from typing import Any

from ...compat import upstream_brand
from ...core.errors import ApiError, HttpErrorCode

# llmProviderSchema field declaration order (packages/openapi/src/admin/setting/update.ts).
_PROVIDER_KEY_ORDER = [
    "type",
    "name",
    "displayName",
    "apiKey",
    "baseUrl",
    "models",
    "isInstance",
    "modelConfigs",
]

# aiConfigSchema field declaration order (llmProviders first via .default([])).
_AI_CONFIG_KEY_ORDER = [
    "llmProviders",
    "embeddingModel",
    "translationModel",
    "chatModel",
    "gatewayModels",
    "capabilities",
    "aiGatewayApiKey",
    "aiGatewayBaseUrl",
    "attachmentTest",
    "attachmentTransferMode",
    "aiGatewayApiKeys",
    "vertexByokCredential",
    "concurrencyGroups",
    "concurrencyPerKey",
    "modelMappings",
    "realtimeTranscription",
]

_BYOK_PREFIX = "byok-"
_BYOK_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
_MAX_NAME_ATTEMPTS = 20


def _is_reserved_provider_name(name: str | None) -> bool:
    return (name or "").strip().lower() == upstream_brand()


def _generate_byok_name(existing: set[str]) -> str:
    for _ in range(_MAX_NAME_ATTEMPTS):
        name = _BYOK_PREFIX + "".join(secrets.choice(_BYOK_ALPHABET) for _ in range(4))
        if name.lower() not in existing:
            return name
    raise ApiError("Unable to generate unique BYOK provider name", HttpErrorCode.VALIDATION_ERROR)


def _canonical_provider(provider: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in _PROVIDER_KEY_ORDER:
        if key in provider:
            out[key] = provider[key]
    if "models" not in out:
        out["models"] = ""
    return out


def normalize_space_ai_integration_config(config: dict[str, Any]) -> dict[str, Any]:
    """Port of normalizeSpaceAIIntegrationConfig: fill/validate provider names,
    then emit the config with schema-ordered keys for byte-stable serialization."""
    providers = config.get("llmProviders") or []
    existing = {(p.get("name") or "").strip().lower() for p in providers if p.get("name")}
    existing.discard("")
    used: set[str] = set()
    normalized_providers: list[dict[str, Any]] = []
    for provider in providers:
        raw_name = (provider.get("name") or "").strip()
        name = raw_name or _generate_byok_name(existing)
        normalized_name = name.strip().lower()
        if _is_reserved_provider_name(name):
            raise ApiError("AI provider name is reserved", HttpErrorCode.VALIDATION_ERROR)
        if normalized_name in used:
            raise ApiError(
                "AI provider name must be unique within the space", HttpErrorCode.VALIDATION_ERROR
            )
        used.add(normalized_name)
        existing.add(normalized_name)
        merged = {**provider, "name": name}
        normalized_providers.append(_canonical_provider(merged))

    out: dict[str, Any] = {}
    source = {**config, "llmProviders": normalized_providers}
    for key in _AI_CONFIG_KEY_ORDER:
        if key == "llmProviders" or key in source:
            out[key] = normalized_providers if key == "llmProviders" else source[key]
    return out


def dumps_config(config: dict[str, Any]) -> str:
    """Compact JSON matching JS JSON.stringify (no spaces, unicode preserved)."""
    return json.dumps(config, separators=(",", ":"), ensure_ascii=False)
