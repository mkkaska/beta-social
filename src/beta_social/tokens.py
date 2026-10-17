"""Erişim token'larını güvenli saklar.

Öncelik Windows Kimlik Yöneticisi (keyring kütüphanesi üzerinden). Keyring
kullanılamıyorsa proje kökündeki `.tokens.json` dosyasına yazılır; bu dosya
.gitignore'dadır ve bir uyarı basılır.

Her platform için tek bir JSON kaydı tutulur:
    {"access_token": "...", "refresh_token": "...", "expires_at": "ISO-8601", ...}
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT

SERVICE = "beta-social"
FALLBACK_FILE = PROJECT_ROOT / ".tokens.json"


@dataclass
class TokenRecord:
    access_token: str
    expires_at: str | None = None
    refresh_token: str | None = None
    refresh_expires_at: str | None = None
    extra: dict[str, Any] | None = None

    def expires_in_days(self, now: datetime | None = None) -> float | None:
        if not self.expires_at:
            return None
        now = now or datetime.now(timezone.utc)
        return (datetime.fromisoformat(self.expires_at) - now).total_seconds() / 86400

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "TokenRecord":
        return cls(**json.loads(raw))


class TokenStore:
    def __init__(self, backend: str = "auto", fallback_file: Path = FALLBACK_FILE):
        self.fallback_file = fallback_file
        self._keyring = None
        if backend in ("auto", "keyring"):
            try:
                import keyring
                from keyring.backends import fail

                if not isinstance(keyring.get_keyring(), fail.Keyring):
                    self._keyring = keyring
            except Exception:  # keyring kurulu değil veya arka uç yok
                self._keyring = None
        if backend == "keyring" and self._keyring is None:
            raise RuntimeError("Keyring arka ucu bulunamadı.")

    @property
    def backend_name(self) -> str:
        return "Windows Kimlik Yöneticisi / keyring" if self._keyring else str(self.fallback_file)

    # --- dosya yedeği ---
    def _read_file(self) -> dict[str, str]:
        if self.fallback_file.exists():
            return json.loads(self.fallback_file.read_text(encoding="utf-8"))
        return {}

    def _write_file(self, data: dict[str, str]) -> None:
        print(
            f"UYARI: keyring yok, token'lar {self.fallback_file} dosyasına yazılıyor.",
            file=sys.stderr,
        )
        self.fallback_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # --- genel arayüz ---
    def save(self, platform: str, record: TokenRecord) -> None:
        if self._keyring:
            self._keyring.set_password(SERVICE, platform, record.to_json())
        else:
            data = self._read_file()
            data[platform] = record.to_json()
            self._write_file(data)

    def load(self, platform: str) -> TokenRecord | None:
        if self._keyring:
            raw = self._keyring.get_password(SERVICE, platform)
        else:
            raw = self._read_file().get(platform)
        return TokenRecord.from_json(raw) if raw else None

    def delete(self, platform: str) -> None:
        if self._keyring:
            try:
                self._keyring.delete_password(SERVICE, platform)
            except Exception:
                pass
        else:
            data = self._read_file()
            data.pop(platform, None)
            self._write_file(data)
