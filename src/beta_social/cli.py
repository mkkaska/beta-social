"""Komut satırı arayüzü.

    beta-social init                       veritabanını oluşturur
    beta-social token set instagram        Instagram token'ını güvenli kaydeder
    beta-social token status               token'ların durumunu gösterir
    beta-social token refresh instagram    Instagram token'ını elle yeniler
    beta-social tiktok-auth                TikTok hesabını bağlar (tarayıcıda izin)
    beta-social check                      iki hesabın profilini çeker (Faz 0 kapısı)
    beta-social collect                    istatistikleri çekip veritabanına yazar
    beta-social report                     günlük Markdown + CSV raporu üretir
    beta-social daily                      token yenile + collect + report (zamanlayıcı için)
"""

from __future__ import annotations

import argparse
import getpass
import sys

from . import __version__, db
from .collect import collect_all, ensure_fresh_tokens
from .config import load_settings
from .platforms import instagram, tiktok
from .report import write_report
from .tokens import TokenRecord, TokenStore


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # Windows konsolunda Türkçe karakterler için
        except (AttributeError, ValueError):
            pass


def cmd_init(args, settings, store) -> int:
    conn = db.connect(settings.db_path)
    db.init_db(conn)
    print(f"Veritabanı hazır: {settings.db_path}")
    print(f"Token deposu: {store.backend_name}")
    return 0


def cmd_token(args, settings, store) -> int:
    if args.action == "status":
        for platform in ("instagram", "tiktok"):
            rec = store.load(platform)
            if not rec:
                print(f"{platform:10} yok")
                continue
            days = rec.expires_in_days()
            left = f"{days:.1f} gün kaldı" if days is not None else "süre bilinmiyor"
            print(f"{platform:10} kayıtlı · {left}")
        return 0
    if args.platform != "instagram":
        print("TikTok için 'beta-social tiktok-auth' kullan.")
        return 2
    if args.action == "set":
        raw = getpass.getpass("Instagram erişim token'ını yapıştır (ekranda görünmez): ").strip()
        if not raw:
            print("Boş token, işlem iptal.")
            return 2
        if args.short_lived:
            if not settings.instagram_app_secret:
                print("Kısa ömürlü token'ı çevirmek için .env içinde INSTAGRAM_APP_SECRET gerekli.")
                return 2
            rec = instagram.exchange_for_long_lived(raw, settings.instagram_app_secret)
        else:
            days = args.days
            rec = TokenRecord(access_token=raw)
            if days:
                from datetime import datetime, timedelta, timezone
                rec.expires_at = (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0).isoformat()
        store.save("instagram", rec)
        print(f"Kaydedildi ({store.backend_name}). Şimdi 'beta-social check' çalıştır.")
        return 0
    if args.action == "refresh":
        rec = store.load("instagram")
        if not rec:
            print("Kayıtlı Instagram token'ı yok.")
            return 2
        store.save("instagram", instagram.refresh_long_lived(rec))
        print("Instagram token'ı yenilendi.")
        return 0
    return 2


def cmd_tiktok_auth(args, settings, store) -> int:
    missing = [n for n, v in (("TIKTOK_CLIENT_KEY", settings.tiktok_client_key),
                              ("TIKTOK_CLIENT_SECRET", settings.tiktok_client_secret),
                              ("TIKTOK_REDIRECT_URI", settings.tiktok_redirect_uri)) if not v]
    if missing:
        print(".env içinde eksik: " + ", ".join(missing))
        return 2
    rec = tiktok.authorize_interactive(settings.tiktok_client_key, settings.tiktok_client_secret,
                                       settings.tiktok_redirect_uri, settings.tiktok_scopes,
                                       open_browser=not args.no_browser)
    store.save("tiktok", rec)
    print(f"TikTok bağlandı. Verilen izinler: {(rec.extra or {}).get('scope')}")
    return 0


def cmd_check(args, settings, store) -> int:
    ok = True
    ig = store.load("instagram")
    if ig:
        try:
            p = instagram.InstagramClient(ig, settings.instagram_api_version).profile()
            print(f"Instagram: @{p.get('username')} · tür {p.get('account_type')} · "
                  f"takipçi {p.get('followers_count')} · gönderi {p.get('media_count')}")
        except Exception as exc:
            ok = False
            print(f"Instagram HATA: {exc}")
    else:
        ok = False
        print("Instagram: token yok ('beta-social token set instagram')")
    tt = store.load("tiktok")
    if tt:
        try:
            u = tiktok.TikTokClient(tt).user_info()
            print(f"TikTok: {u.get('display_name')} · takipçi {u.get('follower_count')} · "
                  f"beğeni {u.get('likes_count')} · video {u.get('video_count')}")
        except Exception as exc:
            ok = False
            print(f"TikTok HATA: {exc}")
    else:
        ok = False
        print("TikTok: bağlı değil ('beta-social tiktok-auth')")
    print("Faz 0 kapısı: GEÇTİ" if ok else "Faz 0 kapısı: henüz değil")
    return 0 if ok else 1


def cmd_collect(args, settings, store) -> int:
    conn = db.connect(settings.db_path)
    db.init_db(conn)
    with db.run_log(conn, "collect") as state:
        for note in ensure_fresh_tokens(settings, store):
            print(note)
        results = collect_all(conn, settings, store, tuple(args.platform) if args.platform else ("instagram", "tiktok"))
        for r in results:
            print(f"{r.platform}: {'tamam' if r.ok else 'HATA'} · {r.posts} gönderi {r.message}".rstrip())
        state["message"] = "; ".join(f"{r.platform}={'ok' if r.ok else r.message}" for r in results)
    return 0 if all(r.ok for r in results) else 1


def cmd_report(args, settings, store) -> int:
    conn = db.connect(settings.db_path)
    db.init_db(conn)
    md, csv_path = write_report(conn, settings.reports_dir)
    print(f"Rapor: {md}\nCSV:   {csv_path}")
    return 0


def cmd_daily(args, settings, store) -> int:
    code = cmd_collect(argparse.Namespace(platform=None), settings, store)
    cmd_report(args, settings, store)
    return code


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="beta-social", description="Instagram + TikTok yerel otomasyon")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="veritabanını oluştur").set_defaults(func=cmd_init)

    t = sub.add_parser("token", help="token işlemleri")
    t.add_argument("action", choices=["set", "status", "refresh"])
    t.add_argument("platform", nargs="?", default="instagram", choices=["instagram", "tiktok"])
    t.add_argument("--short-lived", action="store_true", help="kısa ömürlü token'ı 60 günlüğe çevir")
    t.add_argument("--days", type=int, default=60, help="token'ın kalan ömrü (gün); bilinmiyorsa 60")
    t.set_defaults(func=cmd_token)

    a = sub.add_parser("tiktok-auth", help="TikTok hesabını bağla")
    a.add_argument("--no-browser", action="store_true")
    a.set_defaults(func=cmd_tiktok_auth)

    sub.add_parser("check", help="iki hesabın profilini çek").set_defaults(func=cmd_check)

    c = sub.add_parser("collect", help="istatistikleri topla")
    c.add_argument("--platform", action="append", choices=["instagram", "tiktok"])
    c.set_defaults(func=cmd_collect)

    sub.add_parser("report", help="günlük rapor üret").set_defaults(func=cmd_report)
    sub.add_parser("daily", help="yenile + topla + raporla").set_defaults(func=cmd_daily)
    return p


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    args = build_parser().parse_args(argv)
    settings = load_settings()
    store = TokenStore()
    return args.func(args, settings, store)


if __name__ == "__main__":
    raise SystemExit(main())
