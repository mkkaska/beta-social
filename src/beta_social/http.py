"""Ortak HTTP yardımcıları: zaman aşımı, sınır aşımında bekleme, okunur hatalar."""

from __future__ import annotations

import time
from typing import Any

import requests

DEFAULT_TIMEOUT = 30


class ApiError(RuntimeError):
    def __init__(self, platform: str, status: int, message: str, payload: Any = None):
        super().__init__(f"[{platform}] HTTP {status}: {message}")
        self.platform = platform
        self.status = status
        self.payload = payload


def request_json(
    platform: str,
    method: str,
    url: str,
    *,
    session: requests.Session | None = None,
    retries: int = 3,
    backoff: float = 5.0,
    **kwargs: Any,
) -> dict:
    """İstek gönderir, JSON döndürür.

    429 ve 5xx yanıtlarında artan beklemeyle tekrar dener; diğer hatalarda
    platformun hata mesajını içeren ApiError fırlatır. Yayın gibi tekrarlanması
    tehlikeli işlemlerde `retries=0` verilmelidir.
    """
    sess = session or requests
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    attempt = 0
    while True:
        resp = sess.request(method, url, **kwargs)
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt < retries:
                wait = float(resp.headers.get("Retry-After", backoff * (2**attempt)))
                time.sleep(wait)
                attempt += 1
                continue
        try:
            data = resp.json()
        except ValueError:
            data = {"raw": resp.text[:500]}
        if not resp.ok:
            raise ApiError(platform, resp.status_code, _error_message(data), data)
        return data


def _error_message(data: Any) -> str:
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            # Meta: {"error": {"message": ...}}, TikTok: {"error": {"code": ..., "message": ...}}
            return str(err.get("message") or err.get("code") or err)
        if isinstance(err, str):
            return data.get("error_description") or err
    return str(data)[:300]
