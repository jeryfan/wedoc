"""Avatar imaging: minidenticon default avatars and square-crop uploads.

- ``minidenticon``: port of packages/core utils/minidenticon.ts (same hash,
  same 5x5 grid semantics); rasterized with Pillow instead of sharp — the
  bytes differ from upstream but the contract (stored PNG, 410x410) holds.
- ``crop_square_avatar``: utils/avatar.ts cropSquareAvatarImage — EXIF
  auto-orient, center crop, resize 128x128, webp q85.
"""

import colorsys
import io

from PIL import Image, ImageOps

from .errors import ApiError, HttpErrorCode

AVATAR_ALLOWED_MIMETYPES = ("image/jpeg", "image/png", "image/webp", "image/jpg")
AVATAR_MAX_FILE_SIZE = 3 * 1024 * 1024
AVATAR_SIZE = 128
AVATAR_OUTPUT_MIMETYPE = "image/webp"

_DEFAULT_AVATAR_SIZE = 410
_COLORS_NB = 9
_MAGIC = 5


def _simple_hash(seed: str) -> int:
    # JS: (hash ^ code) * -5 in float64 with int32 coercion on ^, then >>> 2
    h = _MAGIC
    for ch in seed:
        h32 = h % (1 << 32)
        if h32 >= 1 << 31:
            h32 -= 1 << 32
        h = (h32 ^ ord(ch)) * -_MAGIC
    return (h % (1 << 32)) >> 2


def minidenticon_cells(seed: str) -> tuple[int, list[tuple[int, int]]]:
    """(hue, lit (x, y) cells) of the 5x5 identicon grid."""
    hash_ = _simple_hash(seed)
    hue = (hash_ % _COLORS_NB) * (360 // _COLORS_NB)
    cells = []
    for i in range(25):
        if hash_ & (1 << (i % 15)):
            x = 7 - i // 5 if i > 14 else i // 5
            cells.append((x, i % 5))
    return hue, cells


def render_default_avatar(seed: str, size: int = _DEFAULT_AVATAR_SIZE) -> bytes:
    """PNG bytes of the user's default avatar (minidenticon on #f0f0f0)."""
    hue, cells = minidenticon_cells(seed)
    rgb = tuple(round(c * 255) for c in colorsys.hls_to_rgb(hue / 360, 0.45, 0.95))
    img = Image.new("RGB", (size, size), "#f0f0f0")
    # viewBox="-1.5 -1.5 8 8": cell (x, y) spans [(x+1.5)/8, (x+2.5)/8) of the edge
    unit = size / 8
    px = img.load()
    for x, y in cells:
        x0, y0 = round((x + 1.5) * unit), round((y + 1.5) * unit)
        x1, y1 = round((x + 2.5) * unit), round((y + 2.5) * unit)
        for xi in range(x0, x1):
            for yi in range(y0, y1):
                if 0 <= xi < size and 0 <= yi < size:
                    px[xi, yi] = rgb
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def crop_square_avatar(data: bytes, size: int = AVATAR_SIZE) -> bytes:
    """Center-crop to a square and resize; webp output like the upstream sharp chain."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        image = ImageOps.exif_transpose(image)
    except Exception as exc:
        raise ApiError(
            "Unsupported file type",
            HttpErrorCode.VALIDATION_ERROR,
            {"localization": {"i18nKey": "httpErrors.attachment.invalidImage"}},
        ) from exc
    width, height = image.size
    if not width or not height:
        raise ApiError(
            "Unsupported file type",
            HttpErrorCode.VALIDATION_ERROR,
            {"localization": {"i18nKey": "httpErrors.attachment.invalidImage"}},
        )
    crop = min(width, height)
    left = (width - crop) // 2
    top = (height - crop) // 2
    image = image.crop((left, top, left + crop, top + crop)).resize(
        (size, size), Image.LANCZOS
    )
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="WEBP", quality=85)
    return buf.getvalue()
