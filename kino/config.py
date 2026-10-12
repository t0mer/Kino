"""Application settings: env (KINO_*) > YAML (KINO_DATA_DIR/config.yaml) > defaults."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class _YamlSource(PydanticBaseSettingsSource):
    """Reads `config.yaml` from KINO_DATA_DIR (default /data) if it exists."""

    def _load(self) -> dict[str, Any]:
        path = Path(os.environ.get("KINO_DATA_DIR", "/data")) / "config.yaml"
        if not path.is_file():
            return {}
        data = yaml.safe_load(path.read_text()) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path} must contain a mapping")
        return {k.lower(): v for k, v in data.items()}

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return self._load().get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return {k: v for k, v in self._load().items() if k in self.settings_cls.model_fields}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KINO_", extra="ignore")

    data_dir: Path = Path("/data")
    photos_dir: Path = Path("/data/photos")
    # Built web UI (index.html + assets/). The Docker image ships it here; when the directory
    # is missing (dev, tests) the API runs without serving the UI.
    web_dir: Path = Path("/opt/venv/share/kino/web")
    strip_gps: bool = True
    # IANA zone used for photo folders/filenames and EXIF local time (sent_at is stored in UTC).
    timezone: str = "UTC"
    openwa_url: str | None = None
    openwa_api_key: SecretStr | None = None
    openwa_session_id: str | None = None
    webhook_secret: SecretStr | None = None
    admin_user: str | None = None
    admin_password_hash: SecretStr | None = None
    cf_access_team_domain: str | None = None
    cf_access_aud: str | None = None
    cf_access_allowed_emails: str | None = None
    secret_key: SecretStr | None = None
    metrics_public: bool = False
    # Shared directory for multiprocess Prometheus metrics; default <data_dir>/metrics.
    metrics_dir: Path | None = None
    embedder: Literal["auraface", "buffalo_l"] = "auraface"
    det_size: int = 1024
    tiling: Literal["off", "auto", "always"] = "auto"
    tile_grid: Literal["2x2", "3x3", "auto"] = "auto"
    tile_overlap: float = Field(0.2, ge=0, lt=1)
    workers: int = Field(2, ge=1)
    ort_threads: int = Field(2, ge=1)

    @property
    def metrics_path(self) -> Path:
        return self.metrics_dir or self.data_dir / "metrics"

    @field_validator("det_size")
    @classmethod
    def _check_det_size(cls, v: int) -> int:
        if v < 32 or v % 32 != 0:
            raise ValueError("det_size must be a multiple of 32 and at least 32")
        return v

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(
                f"unknown timezone {v!r}; use an IANA name such as 'UTC' or 'Asia/Jerusalem'"
            ) from None
        return v

    raw_retention_days: int = 7
    face_retention_days: int = 30
    burst_enabled: bool = False
    digest_cron: str = "0 20 * * *"
    apprise_urls: str | None = None
    forward_rate_per_min: int = Field(6, ge=1)
    backfill_delay_seconds: float = Field(1.0, ge=0)  # pause after each fetched history photo
    log_level: str = "INFO"
    log_json: bool = False

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, env_settings, _YamlSource(settings_cls))

    def require_webhook_secret(self) -> str:
        """The env value only; runtime code uses ``OpenWAConfig.require_webhook_secret``."""
        value = self.webhook_secret.get_secret_value() if self.webhook_secret else ""
        if not value:
            raise RuntimeError(
                "KINO_WEBHOOK_SECRET is not set; set it or connect OpenWA with the setup wizard"
            )
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
