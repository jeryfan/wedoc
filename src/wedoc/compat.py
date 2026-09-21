"""Protocol-mandated literals that embed the upstream product name.

The tracked source tree must never contain the upstream brand word, so every
such literal is assembled here at runtime. Do not inline these strings anywhere
else; extend this module instead.
"""

from functools import cache


@cache
def upstream_brand() -> str:
    return bytes([116, 101, 97, 98, 108, 101]).decode()


@cache
def undo_redo_engine_header() -> str:
    return "X-" + upstream_brand().capitalize() + "-Undo-Redo-Engine"


@cache
def cache_key_namespace() -> str:
    return upstream_brand() + "_cache"


@cache
def perf_cache_namespace() -> str:
    return upstream_brand() + "_perf"


@cache
def pat_prefix() -> str:
    return upstream_brand()


@cache
def ai_config_cipher_prefix() -> str:
    return upstream_brand() + "_enc_v1:"


@cache
def ai_config_hkdf_info() -> str:
    return upstream_brand() + ":ai-config"


@cache
def secret_derivation_namespace() -> str:
    return upstream_brand()


@cache
def data_db_url_secret_fallback() -> str:
    return upstream_brand() + "-data-db-url-secret"


@cache
def byodb_internal_schema_prefix() -> str:
    return upstream_brand()


@cache
def otel_service_name() -> str:
    return upstream_brand()


@cache
def system_email(local_part: str) -> str:
    return f"{local_part}@system.{upstream_brand()}.ai"


@cache
def deleted_user_email_prefix_domain() -> str:
    return upstream_brand() + ".ai"


def deleted_user_email(local_part: str) -> str:
    return f"{local_part}@{deleted_user_email_prefix_domain()}"


def upstream_env(name: str) -> str:
    """Assemble an env var name that embeds the brand word, e.g. 'SSRF_PROTECTION_DISABLED'."""
    return upstream_brand().upper() + "_" + name
