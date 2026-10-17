"""Günlük rapor: SQLite'taki son ölçümlerden Markdown ve CSV üretir.

Markdown dosyası Claude ile paylaşmak için yazılır; CSV ham veridir.
"""

from __future__ import annotations

import csv
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from . import db

COLUMNS = ("views", "reach", "likes", "comments", "shares", "saved", "reels_skip_rate", "ig_reels_avg_watch_time")
LABELS = {
    "views": "İzlenme", "reach": "Erişim", "likes": "Beğeni", "comments": "Yorum",
    "shares": "Paylaşım", "saved": "Kayıt", "reels_skip_rate": "İlk 3 sn geçen",
    "ig_reels_avg_watch_time": "Ort. izlenme",
}


def _fmt(metric: str, value: float | None) -> str:
    if value is None:
        return "–"
    if metric == "reels_skip_rate":
        # Meta oran (0-1) veya yüzde (0-100) döndürebilir
        pct = value * 100 if value <= 1 else value
        return f"%{pct:.0f}"
    if metric == "ig_reels_avg_watch_time":
        return f"{value / 1000:.1f} sn"
    return f"{value:,.0f}".replace(",", ".")


def build_rows(conn: sqlite3.Connection) -> list[dict]:
    posts: dict[tuple[str, str], dict] = {}
    for r in db.latest_post_metrics(conn):
        key = (r["platform"], r["platform_id"])
        row = posts.setdefault(key, {
            "platform": r["platform"], "id": r["platform_id"], "type": r["media_type"],
            "published_at": r["published_at"], "caption": (r["caption"] or "").split("\n")[0][:60],
            "permalink": r["permalink"],
        })
        row[r["metric"]] = r["value"]
    return sorted(posts.values(), key=lambda x: x.get("published_at") or "", reverse=True)


def write_report(conn: sqlite3.Connection, reports_dir: Path, today: datetime | None = None) -> tuple[Path, Path]:
    today = today or datetime.now()
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = today.strftime("%Y-%m-%d")
    rows = build_rows(conn)

    csv_path = reports_dir / f"{stamp}.csv"
    fieldnames = ["platform", "id", "type", "published_at", "caption", "permalink", *COLUMNS]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    accounts: dict[str, dict[str, float]] = defaultdict(dict)
    for r in db.latest_account_snapshot(conn):
        accounts[r["platform"]][r["metric"]] = r["value"]

    lines = [f"# Beta Hardware günlük rapor · {stamp}", ""]
    for platform in ("instagram", "tiktok"):
        acc = accounts.get(platform)
        if not acc:
            continue
        name = "Instagram" if platform == "instagram" else "TikTok"
        parts = [f"Takipçi {acc.get('followers', 0):.0f}", f"Gönderi {acc.get('media_count', 0):.0f}"]
        if "likes_total" in acc:
            parts.append(f"Toplam beğeni {acc['likes_total']:.0f}")
        if "day_reach" in acc:
            parts.append(f"Dünkü erişim {acc['day_reach']:.0f}")
        lines.append(f"**{name}:** " + " · ".join(parts))
    lines += ["", "| Platform | Tarih | Gönderi | " + " | ".join(LABELS[c] for c in COLUMNS) + " |",
              "| --- | --- | --- | " + " | ".join("---" for _ in COLUMNS) + " |"]
    for row in rows:
        date = (row.get("published_at") or "")[:10]
        cells = [_fmt(c, row.get(c)) for c in COLUMNS]
        title = row["caption"].replace("|", "/") or row["id"]
        lines.append(f"| {row['platform']} | {date} | {title} | " + " | ".join(cells) + " |")
    md_path = reports_dir / f"{stamp}.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path, csv_path
