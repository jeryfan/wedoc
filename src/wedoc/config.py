"""Runtime configuration. Env names mirror the upstream deployment contract 1:1.

Secret resolution chain (mirrors configs/secrets/): dedicated env → fallback
envs (e.g. SECRET_KEY umbrella) → public legacy default. Empty string counts
as unset everywhere.
"""

import hashlib
import os
from functools import cached_property

from pydantic_settings import BaseSettings, SettingsConfigDict

from . import compat
from .core.duration import parse_ms

_UNSET = object()


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value else None


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes", "on")


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # bootstrap
    public_origin: str
    port: int = 3000
    node_env: str = "development"
    log_level: str = "info"
    backend_skip_next_start: bool = False
    backend_trust_proxy: str | None = None
    backend_session_origin_check_enabled: bool = False
    api_doc_disenabled: bool = False
    api_doc_enabled_snippet: bool = False
    timezone: str = "UTC"

    # database: meta url resolution order meta > prisma > database
    prisma_meta_database_url: str | None = None
    prisma_database_url: str | None = None
    database_url: str | None = None
    database_pool_max: int = 20
    byodb_data_db_pool_max: int = 5

    # cache / queue (BACKEND_CACHE_REDIS_URI is unconditionally required upstream)
    backend_cache_provider: str | None = None
    backend_cache_sqlite_uri: str = "sqlite://.assets/.cache.db"
    backend_cache_redis_uri: str
    backend_queue_prefix: str = "bull"
    backend_performance_cache: str | None = None

    # umbrella secret
    secret_key: str | None = None
    backend_env_variable_secret: str | None = None
    backend_env_variable_secret_old: str | None = None

    # auth
    backend_jwt_secret: str | None = None
    backend_jwt_secret_old: str | None = None
    backend_jwt_expires_in: str = "20d"
    backend_session_secret: str | None = None
    backend_session_secret_old: str | None = None
    backend_session_expires_in: str = "7d"
    backend_session_cookie_secure: str | None = None  # 'auto' | 'true' | 'false'
    backend_email_code_expires_in: str = "30m"
    backend_reset_password_email_expires_in: str = "30m"
    backend_signup_verification_expires_in: str = "30m"
    social_auth_providers: str = ""
    password_login_disabled: bool = False
    signin_max_login_attempts: int | None = None
    signin_account_lockout_minutes: int | None = None
    turnstile_site_key: str | None = None
    turnstile_secret_key: str | None = None

    # mail send rate limits (seconds; threshold.config.ts defaults)
    backend_change_email_send_code_mail_rate: int = 30
    backend_reset_password_send_mail_rate: int = 30
    backend_signup_verification_send_code_mail_rate: int = 30
    backend_signup_verification_code_rate_limit_seconds: int | None = None

    # access token (PAT) cipher
    backend_access_token_encryption_algorithm: str = "aes-128-cbc"
    backend_access_token_encryption_key: str | None = None
    backend_access_token_encryption_iv: str | None = None
    backend_access_token_encryption_key_old: str | None = None
    backend_access_token_encryption_iv_old: str | None = None

    # mail cipher + smtp
    backend_mail_encryption_key: str | None = None
    backend_mail_encryption_iv: str | None = None
    backend_mail_host: str = "localhost"
    backend_mail_port: int = 465
    backend_mail_secure: str = "true"
    backend_mail_sender: str = "noreply.localhost"
    backend_mail_sender_name: str = "wedoc"
    backend_mail_auth_user: str | None = None
    backend_mail_auth_pass: str | None = None

    # storage cipher + provider
    backend_storage_provider: str = "local"
    backend_storage_local_path: str = ".assets/uploads"
    backend_storage_public_url: str | None = None
    backend_storage_public_bucket: str = "public"
    backend_storage_private_bucket: str = "private"
    backend_storage_upload_method: str = "put"
    backend_storage_token_expire_in: str = "6d"
    backend_storage_url_expire_in: str = "6d"
    backend_storage_encryption_algorithm: str = "aes-128-cbc"
    backend_storage_encryption_key: str | None = None
    backend_storage_encryption_iv: str | None = None
    backend_storage_encryption_key_old: str | None = None
    backend_storage_encryption_iv_old: str | None = None
    # minio / s3
    backend_storage_minio_endpoint: str | None = None
    backend_storage_minio_internal_endpoint: str | None = None
    backend_storage_minio_port: int = 9000
    backend_storage_minio_internal_port: int = 9000
    backend_storage_minio_use_ssl: bool = False
    backend_storage_minio_access_key: str | None = None
    backend_storage_minio_secret_key: str | None = None
    backend_storage_minio_region: str | None = None
    backend_storage_s3_region: str | None = None
    backend_storage_s3_endpoint: str | None = None
    backend_storage_s3_internal_endpoint: str | None = None
    backend_storage_s3_access_key: str | None = None
    backend_storage_s3_secret_key: str | None = None
    backend_storage_s3_force_path_style: bool = False

    # base misc
    storage_prefix: str | None = None
    public_database_proxy: str | None = None
    default_max_base_db_connections: int = 20
    template_space_id: str | None = None
    record_history_disabled: bool = False
    plugin_server_port: str = "3002"
    enable_email_code_console: bool = False
    chat_context_attachment_size: int = 10

    # oauth server
    backend_oauth_access_token_expire_in: str = "10m"
    backend_oauth_refresh_token_expire_in: str = "30d"
    backend_oauth_transaction_expire_in: str = "10m"
    backend_oauth_code_expire_in: str = "5m"
    backend_oauth_device_code_expire_in: str = "15m"
    backend_oauth_device_code_interval: int = 5
    backend_oauth_authorized_expire_in: str = "7d"

    # trash
    trash_retention: str = "30d"
    trash_scan_interval: str = "1h"

    # thresholds (selection)
    max_copy_cells: int = 50000
    max_reset_cells: int = 50000
    max_paste_cells: int = 50000
    max_read_rows: int = 10000
    max_delete_rows: int = 1000
    max_sync_update_cells: int = 10000
    max_group_points: int = 5000
    calc_chunk_size: int = 1000
    max_undo_stack_size: int = 200
    undo_expiration_time: int = 86400
    big_transaction_timeout: int = 600000
    search_timeout: int = 15000

    # ---- derived ----

    @cached_property
    def meta_database_dsn(self) -> str:
        dsn = (
            self.prisma_meta_database_url or self.prisma_database_url or self.database_url
        )
        if not dsn:
            raise ValueError(
                "One of `PRISMA_META_DATABASE_URL`, legacy `PRISMA_DATABASE_URL`,"
                " or `DATABASE_URL` is required"
            )
        return dsn

    @cached_property
    def sqlalchemy_dsn(self) -> str:
        dsn = self.meta_database_dsn
        if dsn.startswith("postgres://"):
            dsn = "postgresql://" + dsn[len("postgres://") :]
        if dsn.startswith("postgresql://"):
            return "postgresql+asyncpg://" + dsn[len("postgresql://") :]
        return dsn

    @cached_property
    def cache_provider(self) -> str:
        if self.backend_cache_provider:
            return self.backend_cache_provider
        return "redis" if self.backend_cache_redis_uri else "sqlite"

    @cached_property
    def storage_url_prefix(self) -> str:
        return self.storage_prefix or self.public_origin

    # ---- secrets resolution (dedicated → SECRET_KEY umbrella → public legacy) ----

    def _resolve_secret(self, dedicated: str | None, legacy: str) -> str:
        if dedicated:
            return dedicated
        if self.secret_key:
            return self.secret_key
        return legacy

    @cached_property
    def resolved_secret_key(self) -> str:
        return self.secret_key or "defaultSecretKey"

    @cached_property
    def jwt_secrets(self) -> list[str]:
        primary = self._resolve_secret(
            self.backend_jwt_secret, "533Cr3tK3yF0rH4sh1nGJ4W773k3n$"
        )
        secrets = [primary]
        if self.backend_jwt_secret_old:
            secrets.append(self.backend_jwt_secret_old)
        return secrets

    @cached_property
    def session_secrets(self) -> list[str]:
        primary = self._resolve_secret(
            self.backend_session_secret,
            "dafea6be69af1c1c3b8caf2b609342f6eb4540b554e19539f7643b75b480c932",
        )
        secrets = [primary]
        if self.backend_session_secret_old:
            secrets.append(self.backend_session_secret_old)
        return secrets

    def _derive_16(self, purpose: str) -> str:
        digest = hashlib.sha256(
            f"{self.resolved_secret_key}:{compat.secret_derivation_namespace()}:{purpose}".encode()
        ).hexdigest()
        return digest[:16]

    @cached_property
    def access_token_cipher(self) -> tuple[str, str]:
        return (
            self.backend_access_token_encryption_key or "ie21hOKjlXUiGDx9",
            self.backend_access_token_encryption_iv or "i0vKGXBWkzyAoGf4",
        )

    @cached_property
    def storage_cipher(self) -> tuple[str, str]:
        return (
            self.backend_storage_encryption_key or "73b00476e456323e",
            self.backend_storage_encryption_iv or "8c9183e4c175f63c",
        )

    @cached_property
    def mail_cipher(self) -> tuple[str, str]:
        return (
            self.backend_mail_encryption_key or "ie21hOKjlXUiGDx1",
            self.backend_mail_encryption_iv or "i0vKGXBWkzyAoGf1",
        )

    @cached_property
    def env_variable_secret(self) -> str:
        return self._resolve_secret(self.backend_env_variable_secret, "defaultSecretKey")

    # ---- durations ----

    @cached_property
    def jwt_expires_ms(self) -> int:
        return parse_ms(self.backend_jwt_expires_in)

    @cached_property
    def session_expires_ms(self) -> int:
        return parse_ms(self.backend_session_expires_in)

    @cached_property
    def email_code_expires_ms(self) -> int:
        return parse_ms(self.backend_email_code_expires_in)

    # auth.config.ts resolves EMAIL_CODE ?? RESET_PASSWORD ?? '30m' (and the
    # same EMAIL_CODE-first chain for signup verification)
    @cached_property
    def reset_password_email_expires_in_resolved(self) -> str:
        return (
            _env("BACKEND_EMAIL_CODE_EXPIRES_IN")
            or _env("BACKEND_RESET_PASSWORD_EMAIL_EXPIRES_IN")
            or "30m"
        )

    @cached_property
    def signup_verification_expires_in_resolved(self) -> str:
        return (
            _env("BACKEND_EMAIL_CODE_EXPIRES_IN")
            or _env("BACKEND_SIGNUP_VERIFICATION_EXPIRES_IN")
            or "30m"
        )

    @cached_property
    def signup_verification_send_code_mail_rate(self) -> int:
        return (
            self.backend_signup_verification_code_rate_limit_seconds
            or self.backend_signup_verification_send_code_mail_rate
        )

    @cached_property
    def is_turnstile_enabled(self) -> bool:
        return bool(self.turnstile_site_key and self.turnstile_secret_key)

    @cached_property
    def social_providers(self) -> list[str]:
        return [p.strip() for p in self.social_auth_providers.split(",") if p.strip()]

    @cached_property
    def is_mail_configured(self) -> bool:
        return bool(self.backend_mail_auth_user and self.backend_mail_auth_pass)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None
