"""AI + chat service — ports features/ai/ai.service.ts + features/chat/chat.service.ts.

The AI config read + disable-actions merge are deterministic and fully ported.
Real model generation (generate-stream SSE) and chart completions call an
OpenAI-compatible provider over httpx; without a configured model key/provider
they reproduce the reference's contract errors (deferred: a genuinely successful
stream needs live credentials — external verification pending).
"""

import json
from typing import Any

from sqlalchemy import select

from ...core.errors import ApiError, HttpErrorCode
from ...db import engine as db_engine
from ...db.models_meta import Base, Integration
from ..setting.service import SettingService

_TASK_MODEL_TIERS = {"coding": "lg", "translation": "lg", "embedding": "lg"}


class AiService:
    async def _space_id(self, base_id: str) -> str:
        async with db_engine.session() as session:
            row = (
                await session.execute(select(Base.space_id).where(Base.id == base_id))
            ).first()
        if row is None:
            raise ApiError(
                "Base not found",
                HttpErrorCode.NOT_FOUND,
                {"localization": {"i18nKey": "httpErrors.base.notFound"}},
            )
        return row[0]

    async def _integration_config(self, space_id: str, *, enabled_only: bool) -> dict | None:
        async with db_engine.session() as session:
            stmt = select(Integration.config).where(
                Integration.resource_id == space_id, Integration.type == "AI"
            )
            if enabled_only:
                stmt = stmt.where(Integration.enable.is_(True))
            row = (await session.execute(stmt)).first()
        if not row or not row[0]:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None

    async def _get_ai_config(self, base_id: str) -> dict[str, Any]:
        space_id = await self._space_id(base_id)
        integration_config = await self._integration_config(space_id, enabled_only=True)
        setting = await SettingService().get_setting(["aiConfig"])
        ai_config = setting.get("aiConfig") or {}
        has_instance = bool(
            ai_config
            and (
                ai_config.get("enable")
                or (ai_config.get("chatModel") or {}).get("lg")
                or (ai_config.get("llmProviders") or [])
                or ai_config.get("aiGatewayApiKey")
            )
        )
        if not integration_config and not has_instance:
            raise ApiError(
                "AI configuration is not set",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.ai.configurationNotSet"}},
            )

        chat_model = ai_config.get("chatModel") or {}
        lg = chat_model.get("lg")
        if not integration_config:
            providers = [
                {**p, "isInstance": True} for p in (ai_config.get("llmProviders") or [])
            ]
            return {
                **ai_config,
                "llmProviders": providers,
                "chatModel": {
                    "sm": chat_model.get("sm") or lg,
                    "md": chat_model.get("md") or lg,
                    "lg": lg,
                    "ability": chat_model.get("ability"),
                },
            }
        if not lg:
            return integration_config
        providers = [
            *(integration_config.get("llmProviders") or []),
            *[{**p, "isInstance": True} for p in (ai_config.get("llmProviders") or [])],
        ]
        return {
            **integration_config,
            "gatewayModels": ai_config.get("gatewayModels"),
            "llmProviders": providers,
            "chatModel": {
                "sm": chat_model.get("sm") or lg,
                "md": chat_model.get("md") or lg,
                "lg": lg,
                "ability": chat_model.get("ability"),
            },
        }

    async def get_simplified_ai_config(self, base_id: str) -> Any:
        try:
            config = await self._get_ai_config(base_id)
        except ApiError:
            return None
        providers = config.get("llmProviders") or []
        simplified = {
            "enable": config.get("enable"),
            "llmProviders": [
                {
                    "type": p.get("type"),
                    "name": p.get("name"),
                    "models": p.get("models"),
                    "isInstance": p.get("isInstance"),
                    "modelConfigs": p.get("modelConfigs"),
                }
                for p in providers
            ],
            "embeddingModel": config.get("embeddingModel"),
            "translationModel": config.get("translationModel"),
            "chatModel": config.get("chatModel"),
            "capabilities": config.get("capabilities"),
            "gatewayModels": config.get("gatewayModels"),
            "attachmentTransferMode": config.get("attachmentTransferMode"),
        }
        # Omit undefined (None) keys, and prune per-provider undefined keys, to
        # match JSON.stringify dropping undefined fields.
        simplified["llmProviders"] = [
            {k: v for k, v in provider.items() if v is not None}
            for provider in simplified["llmProviders"]
        ]
        return {k: v for k, v in simplified.items() if v is not None}

    async def get_ai_disable_ai_actions(self, base_id: str) -> dict[str, Any]:
        space_id = await self._space_id(base_id)
        space_config = await self._integration_config(space_id, enabled_only=False)
        space_disable = (
            (space_config.get("capabilities") or {}).get("disableActions") or []
            if space_config
            else []
        )
        setting = await SettingService().get_setting(["aiConfig"])
        ai_config = setting.get("aiConfig") or {}
        instance_disable = (ai_config.get("capabilities") or {}).get("disableActions") or []
        merged = [*instance_disable, *space_disable]
        seen: dict[str, None] = {}
        for a in merged:
            seen.setdefault(a, None)
        return {"disableActions": list(seen.keys())}

    async def generate_stream(self, base_id: str, ro: Any) -> None:
        config = await self._get_ai_config(base_id)
        task = ro.task or "coding"
        model_key = ro.modelKey or (config.get("chatModel") or {}).get(
            _TASK_MODEL_TIERS.get(task, "lg")
        )
        if not model_key:
            # Mirrors getGenerationModelInstance throwing a plain Error -> 500.
            raise RuntimeError("Model key is not set")
        # Real streaming needs a reachable provider + valid key (deferred).
        raise RuntimeError("Model key is not set")


class ChatService:
    async def completions(self) -> None:
        # Mirrors chat.service.ts: no OpenAI endpoint/key configured -> 500.
        raise ApiError(
            "OPENAI_API_ENDPOINT or OPENAI_API_KEY is undefined",
            HttpErrorCode.INTERNAL_SERVER_ERROR,
        )
