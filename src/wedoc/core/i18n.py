"""Request language resolution, mirroring the upstream nestjs-i18n wiring.

Resolver order (global.module.ts): ``?lang=`` query, ``NEXT_LOCALE`` cookie,
``Accept-Language`` header, ``x-lang`` header; the first candidate naming a
supported locale wins, otherwise the fallback ``en``. Consumed by signup
(stored on ``users.lang``) today; mail rendering is English-only upstream too.
"""

from starlette.requests import Request

from .security.session import parse_cookie_header

SUPPORTED_LANGUAGES = ("ar", "de", "en", "es", "fr", "he", "it", "ja", "ru", "tr", "uk", "zh")
FALLBACK_LANGUAGE = "en"


def _pick(candidates: list[str | None]) -> str | None:
    for candidate in candidates:
        if not candidate:
            continue
        lang = candidate.strip().lower().replace("_", "-").split("-")[0]
        if lang in SUPPORTED_LANGUAGES:
            return lang
    return None


def _accept_language(header: str | None) -> str | None:
    if not header:
        return None
    return _pick([part.split(";")[0] for part in header.split(",")])


def resolve_lang(request: Request) -> str:
    lang = _pick(
        [
            request.query_params.get("lang"),
            parse_cookie_header(request.headers.get("cookie")).get("NEXT_LOCALE"),
            _accept_language(request.headers.get("accept-language")),
            request.headers.get("x-lang"),
        ]
    )
    return lang or FALLBACK_LANGUAGE
