"""AI request schemas — ports packages/openapi/src/ai."""

from ...core.validation import ZodEnumStr, ZodModel

# z.enum(Task) surfaces just the enum values in its error option list.
TaskStr = ZodEnumStr(["coding", "embedding", "translation"])
ReasoningEffortStr = ZodEnumStr(["none", "low", "medium", "high"])


class AiGenerateRo(ZodModel):
    prompt: str
    task: TaskStr | None = None
    modelKey: str | None = None
    reasoningEffort: ReasoningEffortStr | None = None
