"""Settings from environment variables. No other module reads os.environ."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    unifi_url: str
    unifi_api_key: str
    unifi_site: str
    sync_interval_seconds: int
    db_path: str
    verify_tls: bool
    stale_days: int
    sync_idle_minutes: int = 10  # pause the background sync after this long without a viewer


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_settings(env: dict[str, str] | None = None) -> Settings:
    e = os.environ if env is None else env
    return Settings(
        unifi_url=e.get("UNIFI_URL", "").rstrip("/"),
        unifi_api_key=e.get("UNIFI_API_KEY", ""),
        unifi_site=e.get("UNIFI_SITE", "default"),
        sync_interval_seconds=int(e.get("SYNC_INTERVAL_SECONDS", "300")),
        db_path=e.get("DB_PATH", "./data/ipplan.db"),
        verify_tls=_bool(e.get("VERIFY_TLS"), True),
        stale_days=int(e.get("STALE_DAYS", "14")),
        sync_idle_minutes=int(e.get("SYNC_IDLE_MINUTES", "10")),
    )
