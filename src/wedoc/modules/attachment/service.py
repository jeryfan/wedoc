"""Attachment domain service — ports attachments.service.ts + plugins/local.ts.

Local provider only: signature -> PUT/POST upload -> notify -> read, with the
AES read-token codec and If-Modified-Since / 304 conditional caching. The sharp
thumbnail crop queue is replaced by synchronous Pillow processing: notify probes
image dimensions and, for images taller than the sm/lg thumbnail heights,
generates and stores the {sm,lg} thumbnails whose paths back a cell's
smThumbnailUrl / lgThumbnailUrl on read.
"""

import hashlib
import io
import json
import math
import os
import time
from pathlib import Path
from typing import Any

from ...config import get_settings
from ...core import cls
from ...core.cache import get_cache
from ...core.duration import parse_ms
from ...core.errors import ApiError, HttpErrorCode
from ...core.ids import random_string
from ...core.storage import READ_PATH, get_storage, path_join, storage_token_encryptor
from . import repository, schemas

_IMAGE_MIME_PREFIX = "image/"

# thumbnail geometry ported from features/attachments/constant.ts; images taller
# than a bound get a height-clamped thumbnail at {path}_sm / {path}_lg.
_ATTACHMENT_SM_THUMBNAIL_HEIGHT = 56
_ATTACHMENT_LG_THUMBNAIL_HEIGHT = 525
_ATTACHMENT_THUMBNAIL_DEFAULT_MIMETYPE = "image/png"


def _crop_image_path(path: str, size: str) -> str:
    return f"{path}_{size}"


def _encode_uri_component(value: str) -> str:
    # JS encodeURIComponent: leaves A-Za-z0-9 and -_.!~*'() unescaped.
    from urllib.parse import quote

    return quote(value, safe="-_.!~*'()")


def _invalid_token() -> ApiError:
    return ApiError(
        "Invalid token",
        HttpErrorCode.VALIDATION_ERROR,
        {"localization": {"i18nKey": "httpErrors.attachment.invalidToken"}},
    )


def _invalid_path() -> ApiError:
    return ApiError(
        "Invalid path",
        HttpErrorCode.VALIDATION_ERROR,
        {"localization": {"i18nKey": "httpErrors.attachment.invalidPath"}},
    )


def _is_image(mimetype: str) -> bool:
    return bool(mimetype) and mimetype.startswith(_IMAGE_MIME_PREFIX)


def _resolve_thumbnail_mimetype(mimetype: str) -> str:
    # ports resolveThumbnailMimetype: images keep their type, everything else
    # (pdf pages...) renders to png.
    return mimetype if _is_image(mimetype) else _ATTACHMENT_THUMBNAIL_DEFAULT_MIMETYPE


def _extension_preview(content_type: str) -> str:
    # getExtensionPreview compares against bare file-extension tokens, never
    # full mimetypes; a stored mimetype (e.g. image/png) matches none of them
    # and always falls through to octet-stream.
    image_extensions = (
        "jif", "jfif", "apng", "avif", "svg", "webp", "bmp", "ico",
        "jpg", "jpe", "jpeg", "gif", "png", "heic",
    )
    text_extensions = ("pdf", "txt", "json")
    audio_extensions = ("wav", "mp3", "alac", "aiff", "dsd", "pcm")
    video_extensions = (
        "mp4", "avi", "mpg", "webm", "mov", "flv", "mkv", "wmv", "avchd", "mpeg-4",
    )
    if content_type in image_extensions:
        return content_type
    if content_type in text_extensions:
        return content_type
    if content_type in audio_extensions:
        return content_type
    if content_type in video_extensions:
        return content_type
    return "application/octet-stream"


class AttachmentService:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._storage = get_storage()

    def _token_expire_seconds(self) -> int:
        return parse_ms(self._settings.backend_storage_token_expire_in) // 1000

    def _url_expire_seconds(self) -> int:
        return parse_ms(self._settings.backend_storage_url_expire_in) // 1000

    def _read_url(self, bucket: str, path: str, resp_headers: dict[str, str] | None,
                  expires_date: int) -> str:
        payload: dict[str, Any] = {"expiresDate": expires_date}
        if resp_headers:
            payload["respHeaders"] = resp_headers
        token = storage_token_encryptor(self._settings).encrypt(payload)
        url = f"{path_join(READ_PATH, bucket, path)}?token={token}"
        disposition = (resp_headers or {}).get("Content-Disposition")
        if disposition:
            url += f"&response-content-disposition={_encode_uri_component(disposition)}"
        return url

    def _preview_url(self, bucket: str, path: str, resp_headers: dict[str, str] | None) -> str:
        expires_date = math.floor(time.time()) + self._url_expire_seconds()
        url = self._read_url(bucket, path, resp_headers, expires_date)
        origin = cls.get("origin") or {}
        prefix = self._settings.storage_url_prefix if origin.get("byApi") else ""
        if not url.startswith("/"):
            url = "/" + url
        return prefix + url

    # -- signature -------------------------------------------------------------
    async def signature(self, body: schemas.SignatureRo) -> dict[str, Any]:
        if schemas.is_backend_only(body.type):
            raise ApiError("this upload type cannot be signed", HttpErrorCode.VALIDATION_ERROR)
        max_size = self._settings_max_upload_size()
        if max_size is not None and body.contentLength > max_size:
            self._throw_size_exceeded(max_size)

        upload_type = int(body.type)
        dir_ = schemas.upload_dir(upload_type)
        bucket = schemas.upload_bucket(
            upload_type,
            self._settings.backend_storage_public_bucket,
            self._settings.backend_storage_private_bucket,
        )
        token = random_string(12)
        expires_in = body.expiresIn or self._token_expire_seconds()
        cache = get_cache()
        await cache.set(
            f"attachment:local-signature:{token}",
            {
                "expiresDate": math.floor(time.time()) + expires_in,
                "contentLength": body.contentLength,
                "contentType": body.contentType,
            },
            expires_in,
        )
        path = f"{dir_}/{token}"
        await cache.set(
            f"attachment:signature:{token}",
            {"path": path, "bucket": bucket},
            body.expiresIn or self._token_expire_seconds(),
        )
        return {
            "token": token,
            "path": path,
            "url": f"/api/attachments/upload/{token}",
            "uploadMethod": "PUT",
            "requestHeaders": {
                "Content-Type": body.contentType,
                "Content-Length": body.contentLength,
            },
        }

    def _settings_max_upload_size(self) -> int | None:
        raw = os.environ.get("MAX_ATTACHMENT_UPLOAD_SIZE")
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def _throw_size_exceeded(self, max_size: int) -> None:
        mb = f"{max_size / (1024 * 1024):.2f}"
        raise ApiError(
            f"File size exceeds the maximum limit of {mb} MB",
            HttpErrorCode.VALIDATION_ERROR,
            {
                "localization": {
                    "i18nKey": "httpErrors.attachment.fileSizeExceedsMaximumLimit",
                    "context": {"maxSize": f"{mb}MB"},
                }
            },
        )

    # -- upload ----------------------------------------------------------------
    async def upload(self, data: bytes, content_type: str | None, token: str) -> None:
        cache = get_cache()
        token_cache = await cache.get(f"attachment:signature:{token}")
        if not token_cache:
            raise _invalid_token()
        path = token_cache["path"]
        bucket = token_cache["bucket"]

        validate_meta = await cache.get(f"attachment:local-signature:{token}")
        if not validate_meta:
            raise _invalid_token()
        if math.floor(time.time()) > validate_meta["expiresDate"]:
            raise ApiError(
                "Token has expired",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.attachment.tokenExpired"}},
            )
        size = len(data)
        if validate_meta["contentLength"] and validate_meta["contentLength"] != size:
            raise ApiError(
                "Size mismatch",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.attachment.sizeMismatch"}},
            )
        expected_type = validate_meta["contentType"]
        mimetype = content_type
        if (
            mimetype
            and not self._is_body_parser_fallback(mimetype, expected_type)
            and mimetype != expected_type
        ):
            raise ApiError(
                f"Not allow upload {mimetype} file",
                HttpErrorCode.VALIDATION_ERROR,
                {
                    "localization": {
                        "i18nKey": "httpErrors.attachment.notAllowUploadFileType",
                        "context": {"mimetype": mimetype},
                    }
                },
            )
        hash_ = hashlib.sha256(data).hexdigest()
        self._storage.upload_file(bucket, path, data)
        await cache.set(
            f"attachment:upload:{token}",
            {"mimetype": mimetype, "hash": hash_, "size": size},
            self._token_expire_seconds(),
        )

    def _is_body_parser_fallback(self, mimetype: str, expected_type: str) -> bool:
        return mimetype == "application/octet-stream" and expected_type.startswith(
            "application/json"
        )

    # -- notify ----------------------------------------------------------------
    async def notify(self, token: str, filename: str | None) -> dict[str, Any]:
        cache = get_cache()
        token_cache = await cache.get(f"attachment:signature:{token}")
        if not token_cache:
            raise _invalid_token()
        user_id = cls.get("user.id")
        path = token_cache["path"]
        bucket = token_cache["bucket"]

        upload_cache = await cache.get(f"attachment:upload:{token}")
        if not upload_cache:
            raise _invalid_token()
        mimetype = upload_cache["mimetype"]
        hash_ = upload_cache["hash"]
        size = upload_cache["size"]

        width: int | None = None
        height: int | None = None
        thumbnail_path: str | None = None
        if _is_image(mimetype or ""):
            width, height = self._image_dimensions(bucket, path)
            if height is not None:
                thumbnail_path = self._generate_thumbnails(bucket, path, height)

        url = self._read_url(bucket, path, {"Content-Type": mimetype}, -1)

        await repository.create_attachment(
            token=token,
            hash_=hash_,
            size=size,
            mimetype=mimetype,
            path=path,
            width=width,
            height=height,
            created_by=user_id,
            thumbnail_path=thumbnail_path,
        )

        resp_headers = {"Content-Type": mimetype}
        if filename:
            resp_headers["Content-Disposition"] = (
                f"attachment; filename*=UTF-8''{_encode_uri_component(filename)}"
            )
        presigned = self._preview_url(bucket, path, resp_headers)

        result: dict[str, Any] = {
            "token": token,
            "size": size,
            "mimetype": mimetype,
            "path": path,
            "url": url,
            "presignedUrl": presigned,
        }
        if width is not None:
            result["width"] = width
        if height is not None:
            result["height"] = height
        return result

    def _image_dimensions(self, bucket: str, path: str) -> tuple[int | None, int | None]:
        try:
            from PIL import Image

            target = self._storage.root / bucket / path
            with Image.open(target) as img:
                return img.width, img.height
        except Exception:
            return None, None

    def _generate_thumbnails(self, bucket: str, path: str, height: int) -> str | None:
        """Crop {sm,lg} thumbnails for an image and return the persisted path map.

        Ports cropTableImage: a size is produced only when the source is taller
        than that bound, so a short image yields ``{}`` (mirroring the reference, which
        still records an empty map). Returns None when Pillow cannot open it, so
        the read path falls back to the presigned original for images.
        """
        try:
            from PIL import Image
        except Exception:
            return None
        thumb: dict[str, str] = {}
        source = self._storage.root / bucket / path
        try:
            for size, limit in (
                ("sm", _ATTACHMENT_SM_THUMBNAIL_HEIGHT),
                ("lg", _ATTACHMENT_LG_THUMBNAIL_HEIGHT),
            ):
                if height <= limit:
                    continue
                with Image.open(source) as img:
                    ratio = limit / img.height
                    new_width = max(1, round(img.width * ratio))
                    resized = img.resize((new_width, limit))
                    buffer = io.BytesIO()
                    resized.save(buffer, format="PNG")
                target_path = _crop_image_path(path, size)
                self._storage.upload_file(bucket, target_path, buffer.getvalue())
                thumb[size] = target_path
        except Exception:
            return None
        return json.dumps(thumb, separators=(",", ":"))

    # -- record read enrichment ------------------------------------------------
    def _table_bucket(self) -> str:
        return schemas.upload_bucket(
            int(schemas.UploadType.TABLE),
            self._settings.backend_storage_public_bucket,
            self._settings.backend_storage_private_bucket,
        )

    def presign_cell_item(
        self, item: dict[str, Any], thumbnail: dict[str, str] | None
    ) -> dict[str, Any]:
        """Add ephemeral presignedUrl/smThumbnailUrl/lgThumbnailUrl to a cell item.

        Ports getAttachmentPresignedCellValue: images always expose both thumbnail
        urls (falling back to the presigned original when a size was not cropped);
        non-images expose a thumbnail url only when one was actually generated.
        The signed urls are never persisted — they decorate the read response only.
        """
        path = item.get("path")
        if not isinstance(path, str):
            return item
        mimetype = item.get("mimetype") if isinstance(item.get("mimetype"), str) else ""
        name = item.get("name")
        filename = name if isinstance(name, str) else (item.get("token") or "")
        bucket = self._table_bucket()
        disposition = f"attachment; filename*=UTF-8''{_encode_uri_component(filename)}"
        resp_headers = {
            "Content-Type": mimetype,
            "Content-Disposition": disposition,
        }
        presigned = self._preview_url(bucket, path, resp_headers)
        result: dict[str, Any] = {**item, "presignedUrl": presigned}
        is_img = _is_image(mimetype)
        thumb_mimetype = _resolve_thumbnail_mimetype(mimetype)
        sm_url: str | None = None
        lg_url: str | None = None
        if thumbnail:
            sm_path = thumbnail.get("sm")
            lg_path = thumbnail.get("lg")
            if sm_path:
                sm_url = self._preview_url(bucket, sm_path, {"Content-Type": thumb_mimetype})
            if lg_path:
                lg_url = self._preview_url(bucket, lg_path, {"Content-Type": thumb_mimetype})
        if is_img:
            result["smThumbnailUrl"] = sm_url or presigned
            result["lgThumbnailUrl"] = lg_url or presigned
        else:
            if sm_url is not None:
                result["smThumbnailUrl"] = sm_url
            if lg_url is not None:
                result["lgThumbnailUrl"] = lg_url
        return result

    # -- read ------------------------------------------------------------------
    def _resolve_read(self, path: str) -> Path:
        # assertPathWithinStorage: reject '..' (substring) and absolute paths
        # before resolving, then require the resolved target to stay under
        # storage root + separator so a sibling dir whose name shares the root
        # basename as a prefix cannot be reached.
        root = self._storage.root
        if not path or ".." in path or os.path.isabs(path):
            raise _invalid_path()
        target = (root / path).resolve()
        if not str(target).startswith(str(root) + os.sep):
            raise _invalid_path()
        return target

    def last_modified_ms(self, path: str) -> float | None:
        target = self._resolve_read(path)
        if not target.is_file():
            return None
        return target.stat().st_mtime * 1000

    def conditional_caching(
        self, path: str, if_modified_since: str | None
    ) -> tuple[bool, str | None]:
        """Returns (has_cache_304, last_modified_header)."""
        last_modified = self.last_modified_ms(path)
        if last_modified is None:
            raise ApiError(
                "Could not find attachment",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.attachment.invalidPath"}},
            )
        from email.utils import formatdate, parsedate_to_datetime

        last_modified_header = formatdate(last_modified / 1000, usegmt=True)
        if if_modified_since:
            try:
                since = parsedate_to_datetime(if_modified_since).timestamp()
            except (TypeError, ValueError):
                since = None
            if since is not None and math.floor(since) >= math.floor(last_modified / 1000):
                return True, last_modified_header
        return False, last_modified_header

    async def read_local_file(
        self, path: str, token: str | None
    ) -> tuple[bytes, dict[str, str]]:
        target = self._resolve_read(path)
        parts = path.split("/")
        bucket = parts[0]
        token_in_path = parts[-1]
        resp_headers: dict[str, str] = {}
        is_public = schemas.is_public_bucket(
            bucket, self._settings.backend_storage_public_bucket
        )
        if token and not is_public:
            resp_headers = self._verify_read_token(token)
        else:
            attachment = await repository.find_attachment_by_token(token_in_path)
            if not attachment:
                raise _invalid_path()
            resp_headers = {"Content-Type": _extension_preview(attachment["mimetype"])}
        if not target.is_file():
            raise _invalid_path()
        return target.read_bytes(), resp_headers

    def _verify_read_token(self, token: str) -> dict[str, str]:
        try:
            payload = storage_token_encryptor(self._settings).decrypt(token)
        except Exception as exc:
            raise _invalid_token() from exc
        expires_date = payload.get("expiresDate", 0)
        if expires_date > 0 and math.floor(time.time()) > expires_date:
            raise ApiError(
                "Token has expired",
                HttpErrorCode.VALIDATION_ERROR,
                {"localization": {"i18nKey": "httpErrors.attachment.tokenExpired"}},
            )
        return payload.get("respHeaders") or {}
