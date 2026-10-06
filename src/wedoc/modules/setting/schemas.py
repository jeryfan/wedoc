"""Setting/admin request schemas — ports packages/openapi/src/admin/setting.

The AI/app-config patch shapes upstream are deep discriminated unions; here the
section discriminator is validated and the patch is accepted as a passthrough
object (deep field validation of provider/pricing/model shapes is deferred —
those paths need live AI providers to exercise meaningfully).
"""

from typing import Annotated, Any

from pydantic import Field, StrictBool, field_validator

from ...core.validation import ZodEmailStr, ZodEnumStr, ZodModel, ZodNonOptional, ZodNullable

_NullableMailConfig = Annotated[dict[str, Any] | None, ZodNullable()]

_AI_SECTIONS = [
    "llmApi",
    "modelPool",
    "defaultModels",
    "capabilities",
    "concurrency",
    "vertexCredential",
    "modelMappings",
    "modelConfigs",
    "realtimeTranscription",
]
_APP_SECTIONS = ["engine", "customDomain", "appAuth", "branding"]
_SEVERITIES = ["critical", "warning", "info"]


class UpdateSettingRo(ZodModel):
    disallowSignUp: StrictBool | None = None
    bannedEmailDomains: list[str] | None = None
    disallowSpaceCreation: StrictBool | None = None
    disallowSpaceInvitation: StrictBool | None = None
    enableEmailVerification: StrictBool | None = None
    enableCreditReward: StrictBool | None = None
    aiConfig: dict[str, Any] | None = None
    enableWaitlist: StrictBool | None = None
    appConfig: dict[str, Any] | None = None
    brandName: str | None = None
    canaryConfig: dict[str, Any] | None = None
    notifyMailTransportConfig: _NullableMailConfig = None
    automationMailTransportConfig: _NullableMailConfig = None
    imConfig: _NullableMailConfig = None


class UpdateAiConfigRo(ZodModel):
    section: ZodEnumStr(_AI_SECTIONS)
    patch: dict[str, Any]


class UpdateAppConfigRo(ZodModel):
    section: ZodEnumStr(_APP_SECTIONS)
    patch: dict[str, Any]


class MailTransportConfig(ZodModel):
    senderName: str | None = None
    sender: str
    host: str
    port: int
    secure: StrictBool | None = None
    auth: dict[str, Any]


class SetMailTransportConfigRo(ZodModel):
    name: ZodEnumStr(["notifyMailTransportConfig", "automationMailTransportConfig"])
    transportConfig: MailTransportConfig


_LLM_PROVIDER_TYPES = [
    "openai",
    "anthropic",
    "google",
    "azure",
    "cohere",
    "mistral",
    "deepseek",
    "qwen",
    "zhipu",
    "lingyiwanwu",
    "xai",
    "togetherai",
    "ollama",
    "amazonBedrock",
    "openRouter",
    "openaiCompatible",
    "aiGateway",
]


class TestLLMRo(ZodModel):
    # testLLMRoSchema = llmProviderSchema.omit({isInstance,modelConfigs,displayName})
    #   .required().extend({modelKey?, ability?, testImageGeneration?, testImageToImage?})
    # `.required()` turns the originally-optional apiKey/baseUrl into non-optional,
    # so a missing one reports "expected nonoptional" (vs "expected string" for name).
    type: ZodEnumStr(_LLM_PROVIDER_TYPES)
    name: str
    apiKey: Annotated[str, ZodNonOptional()]
    baseUrl: Annotated[str, ZodNonOptional()]
    models: str = ""
    modelKey: str | None = None
    ability: list[str] | None = None
    testImageGeneration: StrictBool | None = None
    testImageToImage: StrictBool | None = None


class _BatchTestLLMProvider(ZodModel):
    # batchTestLLMRoSchema providers use llmProviderSchema.omit({modelConfigs,
    # displayName}).required(): apiKey/baseUrl/isInstance become non-optional.
    type: ZodEnumStr(_LLM_PROVIDER_TYPES)
    name: str
    apiKey: Annotated[str, ZodNonOptional()]
    baseUrl: Annotated[str, ZodNonOptional()]
    models: str = ""
    isInstance: Annotated[StrictBool, ZodNonOptional()]


class BatchTestLLMRo(ZodModel):
    providers: list[_BatchTestLLMProvider] | None = None


class TestApiKeyRo(ZodModel):
    type: ZodEnumStr(["aiGateway", "vercel", "realtimeTranscription"])
    apiKey: str
    baseUrl: str | None = None
    testAttachment: StrictBool | None = None


class AdminSendNotificationRo(ZodModel):
    message: str
    severity: ZodEnumStr(_SEVERITIES) = "info"
    userIds: list[str] | None = Field(default=None, max_length=500)
    emails: list[ZodEmailStr] | None = Field(default=None, max_length=500)

    @field_validator("message")
    @classmethod
    def _message(cls, value: str) -> str:
        if len(value) < 1:
            raise ValueError("Too small: expected string to have >=1 characters")
        if len(value) > 5000:
            raise ValueError("Too big: expected string to have <=5000 characters")
        return value
