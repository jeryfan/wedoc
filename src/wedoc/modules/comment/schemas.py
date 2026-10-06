"""Comment request/response schemas.

Ports packages/openapi/src/comment (types.ts / reaction / get-list).
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, Field

from ...core.validation import ZodModel, ZodMultiError, ZodNullableStr

# zod v4 .emoji(): ^(\p{Extended_Pictographic}|\p{Emoji_Component})+$ — Python's
# stdlib re has no \p{} support, so approximate with the common emoji codepoint
# blocks plus ZWJ / variation selectors / regional indicators / skin tones.
_EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),
    (0x2600, 0x27BF),
    (0x2B00, 0x2BFF),
    (0x1F1E6, 0x1F1FF),
    (0xFE00, 0xFE0F),
    (0x200D, 0x200D),
    (0x2190, 0x21FF),
    (0x2300, 0x23FF),
    (0x2934, 0x2935),
)


def _is_emoji(value: str) -> bool:
    if not value:
        return False
    for ch in value:
        cp = ord(ch)
        if not any(lo <= cp <= hi for lo, hi in _EMOJI_RANGES):
            return False
    return True


class CommentNodeType(StrEnum):
    TEXT = "span"
    LINK = "a"
    PARAGRAPH = "p"
    IMG = "img"
    MENTION = "mention"


class CommentPatchType(StrEnum):
    CREATE_COMMENT = "create_comment"
    UPDATE_COMMENT = "update_comment"
    DELETE_COMMENT = "delete_comment"
    CREATE_REACTION = "create_reaction"
    DELETE_REACTION = "delete_reaction"


# Ordered list mirrors SUPPORT_EMOJIS in packages/openapi comment/reaction/constant.
SUPPORT_EMOJIS = ["👌", "👍", "👎", "😄", "❤️", "🎉", "👀", "🚀", "😂"]


class TextCommentContent(ZodModel):
    type: Literal[CommentNodeType.TEXT]
    value: str


class MentionCommentContent(ZodModel):
    type: Literal[CommentNodeType.MENTION]
    value: str
    name: str | None = None
    avatar: str | None = None


class LinkCommentContent(ZodModel):
    type: Literal[CommentNodeType.LINK]
    url: str
    title: str


InlineContent = Annotated[
    TextCommentContent | MentionCommentContent | LinkCommentContent,
    Field(discriminator="type"),
]


class ParagraphCommentContent(ZodModel):
    type: Literal[CommentNodeType.PARAGRAPH]
    children: list[InlineContent]


class ImageCommentContent(ZodModel):
    type: Literal[CommentNodeType.IMG]
    path: str
    width: float | None = None
    url: str | None = None


BlockContent = Annotated[
    ParagraphCommentContent | ImageCommentContent,
    Field(discriminator="type"),
]


class CreateCommentRo(ZodModel):
    quoteId: ZodNullableStr = None
    content: list[BlockContent]


class UpdateCommentRo(ZodModel):
    content: list[BlockContent]


def _reaction_check(value: str) -> str:
    # commentReactionSymbolSchema: z.string().emoji().refine(in SUPPORT_EMOJIS).
    # zod surfaces both failing checks in order at the same path.
    messages: list[str] = []
    if not _is_emoji(value):
        messages.append("Invalid emoji")
    if value not in SUPPORT_EMOJIS:
        messages.append("Invalid input")
    if len(messages) == 1:
        raise ValueError(messages[0])
    if messages:
        raise ZodMultiError(messages)
    return value


CommentReactionSymbol = Annotated[str, AfterValidator(_reaction_check)]


class UpdateCommentReactionRo(ZodModel):
    reaction: CommentReactionSymbol
