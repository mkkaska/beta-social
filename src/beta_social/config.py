"""Ayarları .env dosyasından ve ortam değişkenlerinden okur."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    instagram_app_id: str = ""
    instagram_app_secret: str = ""
    instagram_api_version: str = "v25.0"
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_redirect_uri: str = ""
    tiktok_scopes: tuple[str, ...] = field(default_factory=tuple)
    data_dir: Path = PROJECT_ROOT / "data"
    reports_dir: Path = PROJECT_ROOT / "reports"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "beta_social.sqlite"


def _path(value: str | None, default: Path) -> Path:
    return Path(value).expanduser() if value else default


def load_settings(env_file: Path | None = None) -> Settings:
    """`.env` dosyasını yükler ve ayarları döndürür.

    Ortamda zaten tanımlı değişkenler `.env` içindekileri ezmez; böylece
    testlerde veya zamanlanmış görevde değerler dışarıdan verilebilir.
    """
    load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)
    scopes = os.getenv("TIKTOK_SCOPES", "user.info.basic,user.info.stats,video.list")
    return Settings(
        instagram_app_id=os.getenv("INSTAGRAM_APP_ID", ""),
        instagram_app_secret=os.getenv("INSTAGRAM_APP_SECRET", ""),
        instagram_api_version=os.getenv("INSTAGRAM_API_VERSION", "v25.0") or "v25.0",
        tiktok_client_key=os.getenv("TIKTOK_CLIENT_KEY", ""),
        tiktok_client_secret=os.getenv("TIKTOK_CLIENT_SECRET", ""),
        tiktok_redirect_uri=os.getenv("TIKTOK_REDIRECT_URI", ""),
        tiktok_scopes=tuple(s.strip() for s in scopes.split(",") if s.strip()),
        data_dir=_path(os.getenv("BETA_DATA_DIR"), PROJECT_ROOT / "data"),
        reports_dir=_path(os.getenv("BETA_REPORTS_DIR"), PROJECT_ROOT / "reports"),
    )
