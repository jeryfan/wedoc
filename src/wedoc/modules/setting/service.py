"""Setting-open-api service — ports setting-open-api.service.ts + setting.service.ts.

Deterministic instance-setting CRUD is fully ported. The LLM / API-key / public
access test surface requires live AI providers and outbound network, so those
paths keep the upstream contract + error shape but defer the real vendor calls
(documented in the parity ledger).
"""

import os
from typing import Any

from ...compat import upstream_brand
from ...config import get_settings
from ...core.errors import ApiError, HttpErrorCode
from ...core.storage import get_public_full_storage_url, get_storage
from . import repository
from .encryption import decrypt_ai_config_secrets, encrypt_ai_config_secrets
from .schemas import (
    SetMailTransportConfigRo,
    TestApiKeyRo,
    TestLLMRo,
    UpdateAiConfigRo,
    UpdateAppConfigRo,
)

_DEFAULT_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"
_DEFAULT_TRANSCRIPTION_MAX_SESSION_DURATION_SEC = 120
_BRAND_LOGO = "brandLogo"
_INSTANCE_ID = "instanceId"
_AI_CONFIG = "aiConfig"
_APP_CONFIG = "appConfig"

_BUILD_VERSION_ENV_KEYS = ("BUILD_VERSION", "NEXT_PUBLIC_BUILD_VERSION", "APP_VERSION")


def _resolve_build_version() -> str:
    for key in _BUILD_VERSION_ENV_KEYS:
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return ""

_PUBLIC_KEYS = [
    _INSTANCE_ID,
    "brandName",
    _BRAND_LOGO,
    "disallowSignUp",
    "disallowSpaceCreation",
    "disallowSpaceInvitation",
    "disallowDashboard",
    "enableEmailVerification",
    "enableWaitlist",
    "enableCreditReward",
    _AI_CONFIG,
    _APP_CONFIG,
]


class SettingService:
    async def get_setting(self, names: list[str] | None = None) -> dict[str, Any]:
        rows = await repository.get_setting_rows(names)
        res: dict[str, Any] = {"instanceId": ""}
        name_set = {r["name"] for r in rows} if names is None else set(names)
        for row in rows:
            name = row["name"]
            if name not in name_set:
                continue
            if name == _BRAND_LOGO:
                content = row["content"]
                res[name] = get_public_full_storage_url(content) if content else content
            else:
                res[name] = row["content"]
            if name == _INSTANCE_ID:
                res["createdTime"] = row["createdTime"]
        if res.get(_AI_CONFIG):
            res[_AI_CONFIG] = decrypt_ai_config_secrets(res[_AI_CONFIG], "setting.aiConfig")
        return res

    async def update_setting(self, patch: dict[str, Any]) -> dict[str, Any]:
        if patch.get(_AI_CONFIG):
            self._normalize_instance_provider_names(patch[_AI_CONFIG])
        res: dict[str, Any] = {}
        for name, value in patch.items():
            to_store = encrypt_ai_config_secrets(value) if name == _AI_CONFIG else value
            stored = await repository.upsert_setting(name, to_store)
            res[name] = (
                decrypt_ai_config_secrets(stored, "setting.aiConfig")
                if name == _AI_CONFIG
                else stored
            )
        return res

    async def get_public_setting(self) -> dict[str, Any]:
        setting = await self.get_setting(_PUBLIC_KEYS)
        settings = get_settings()
        ai_config = setting.get(_AI_CONFIG) or {}
        app_config = setting.get(_APP_CONFIG) or {}

        rest = {
            k: v
            for k, v in setting.items()
            if k not in (_AI_CONFIG, _APP_CONFIG, "enableCreditReward")
        }
        public_ai = self._public_ai_config(ai_config)
        result: dict[str, Any] = {**rest}
        if setting.get("enableCreditReward") is not None:
            result["enableCreditReward"] = setting["enableCreditReward"]
        result[_AI_CONFIG] = public_ai
        result["appGenerationEnabled"] = bool(app_config.get("vercelToken"))
        result["availableIntegrationProviders"] = self._available_integration_providers()
        # Enterprise public-setting fields: wedoc ships none of these optional
        # integrations, so each reports the unconfigured default the reference
        # returns for an instance without them (socialAuthProviders/buildVersion
        # stay env-driven).
        result["githubAppConfigured"] = False
        result["scrapeEnabled"] = False
        result["connectorEventEnabled"] = False
        result["mobileAuthExchange"] = True
        result["socialAuthProviders"] = settings.social_providers
        result["emailCodeSigninEnabled"] = False
        result["buildVersion"] = _resolve_build_version()
        result["turnstileSiteKey"] = getattr(settings, "backend_turnstile_site_key", None)
        result["changeEmailSendCodeMailRate"] = settings.backend_change_email_send_code_mail_rate
        result["resetPasswordSendMailRate"] = settings.backend_reset_password_send_mail_rate
        result["signupVerificationSendCodeMailRate"] = (
            settings.signup_verification_send_code_mail_rate
        )
        return result

    def _public_ai_config(self, ai_config: dict[str, Any]) -> dict[str, Any]:
        chat_model = ai_config.get("chatModel")
        realtime = ai_config.get("realtimeTranscription") or {}
        providers = ai_config.get("llmProviders") or []
        out: dict[str, Any] = {
            "enable": bool(chat_model and chat_model.get("lg")),
            "llmProviders": [
                {
                    "type": p.get("type"),
                    "name": p.get("name"),
                    "models": p.get("models"),
                    "isInstance": True,
                    "modelConfigs": p.get("modelConfigs"),
                }
                for p in providers
            ],
        }
        if chat_model is not None:
            out["chatModel"] = chat_model
        if ai_config.get("capabilities") is not None:
            out["capabilities"] = ai_config["capabilities"]
        if ai_config.get("gatewayModels") is not None:
            out["gatewayModels"] = ai_config["gatewayModels"]
        model = realtime.get("model")
        if model == "gpt-realtime-whisper":
            model = _DEFAULT_TRANSCRIPTION_MODEL
        out["voiceInput"] = {
            "enabled": bool(
                realtime.get("enabled", True) and realtime.get("apiKey")
            ),
            "model": model or _DEFAULT_TRANSCRIPTION_MODEL,
            "maxSessionDurationSec": realtime.get(
                "maxSessionDurationSec", _DEFAULT_TRANSCRIPTION_MAX_SESSION_DURATION_SEC
            ),
        }
        return out

    def _available_integration_providers(self) -> list[str]:
        settings = get_settings()
        providers: list[str] = []
        if getattr(settings, "gmail_client_id", None):
            providers.append("gmail")
        if getattr(settings, "outlook_client_id", None):
            providers.append("outlook")
        if getattr(settings, "airtable_client_id", None):
            providers.append("airtable")
        if getattr(settings, "google_sheet_picker_api_key", None):
            providers.append("googleSheet")
        return providers

    def _normalize_instance_provider_names(self, ai_config: dict[str, Any]) -> None:
        name = upstream_brand()
        for provider in ai_config.get("llmProviders") or []:
            if isinstance(provider, dict):
                provider["name"] = name
        chat_model = ai_config.get("chatModel")
        if isinstance(chat_model, dict):
            for tier in ("lg", "md", "sm"):
                key = chat_model.get(tier)
                if key and "@" in key:
                    parts = key.split("@")
                    parts[-1] = name
                    chat_model[tier] = "@".join(parts)

    async def update_ai_config(self, ro: UpdateAiConfigRo) -> dict[str, Any]:
        current = (await self.get_setting([_AI_CONFIG])).get(_AI_CONFIG) or {}
        patch = self._clear_undefined(ro.patch)
        next_config = {**current, **patch}
        self._normalize_instance_provider_names(next_config)
        await self.update_setting({_AI_CONFIG: next_config})
        return {_AI_CONFIG: {k: next_config.get(k) for k in patch}}

    async def update_app_config(self, ro: UpdateAppConfigRo) -> dict[str, Any]:
        current = (await self.get_setting([_APP_CONFIG])).get(_APP_CONFIG) or {}
        patch = self._clear_undefined(ro.patch)
        next_config = {**current, **patch}
        await self.update_setting({_APP_CONFIG: next_config})
        return {_APP_CONFIG: {k: next_config.get(k) for k in patch}}

    @staticmethod
    def _clear_undefined(patch: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in patch.items() if v is not None}

    async def batch_test_llm(self, providers: list[dict[str, Any]] | None) -> dict[str, Any]:
        if not providers:
            setting = await self.get_setting()
            providers = (setting.get(_AI_CONFIG) or {}).get("llmProviders") or []
        model_tests = 0
        for provider in providers:
            if not (provider.get("apiKey") and provider.get("baseUrl") and provider.get("models")):
                continue
            models = [m.strip() for m in provider["models"].split(",") if m.strip()]
            model_tests += len(models)
        # Real per-model connectivity tests require live AI providers (deferred);
        # with no testable models the deterministic empty summary is returned.
        return {
            "totalModels": model_tests,
            "testedModels": 0,
            "successCount": 0,
            "failedCount": 0,
            "results": [],
        }

    async def test_llm(self, ro: TestLLMRo) -> dict[str, Any]:
        from ..ai.llm import call_test_llm

        try:
            return await call_test_llm(ro)
        except ValueError as error:
            raise ApiError(
                "LLM test failed with error: " + str(error),
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.ai.testLLMFailed"}},
            ) from error

    async def test_api_key(self, ro: TestApiKeyRo) -> dict[str, Any]:
        # Live vendor key verification deferred; return the failure envelope shape.
        return {
            "success": False,
            "error": {"code": "network_error", "message": "API key verification is not configured"},
        }

    async def test_public_access(self) -> dict[str, Any]:
        settings = get_settings()
        public_origin = settings.public_origin
        if not public_origin:
            return {"success": False, "error": "PUBLIC_ORIGIN not set"}
        # The external access-checker call is deferred (outbound network).
        return {
            "success": False,
            "publicOrigin": public_origin,
            "error": "Public access check is not configured",
        }

    async def set_mail_transport_config(self, ro: SetMailTransportConfigRo) -> dict[str, Any]:
        # verifyTransport (SMTP handshake) deferred; persist and echo with pass masked.
        transport = ro.transportConfig.model_dump(exclude_none=True)
        await self.update_setting({ro.name: transport})
        auth = dict(transport.get("auth") or {})
        return {
            "name": ro.name,
            "transportConfig": {
                **transport,
                "auth": {"user": auth.get("user"), "pass": ""},
            },
        }

    async def upload_logo(self, data: bytes, content_type: str) -> dict[str, Any]:
        from ...core import cls
        from ..user.repository import upsert_attachment_by_token

        path = "logo/brand"
        token = "brand"
        result = get_storage().upload_file("public", path, data)
        await upsert_attachment_by_token(
            {
                "token": token,
                "hash": result["hash"],
                "size": len(data),
                "mimetype": content_type,
                "path": path,
                "created_by": cls.get("user.id"),
            }
        )
        await self.update_setting({_BRAND_LOGO: path})
        return {"url": get_public_full_storage_url(path)}
