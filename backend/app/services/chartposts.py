"""Chart posts for InstaFin, drawn from live market data.

"Nexis Charts" publishes square, Instagram-sized charts: the Dubai market's daily movers, global
indices and a "chart of the day" for the instrument most mentioned in the news. One post per chart
per day; later refreshes the same day redraw it with the latest prices.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NexisError
from app.core.logging import get_logger
from app.db.base import utcnow
from app.models import Media, Post, User
from app.services import markets

log = get_logger(__name__)
BG, INK, MUTED, GRID = "#0b1220", "#f1f5f9", "#94a3b8", "#1e293b"
UP, DOWN, ACCENT = "#22c55e", "#ef4444", "#38bdf8"


def _fig() -> tuple[Any, Any]:
    fig, ax = plt.subplots(figsize=(6, 6), dpi=180)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    return fig, ax


def _png(fig: Any) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="jpeg", facecolor=BG, dpi=180, pil_kwargs={"quality": 88})
    plt.close(fig)
    return buf.getvalue()


def _title(fig: Any, title: str, sub: str) -> None:
    fig.text(0.06, 0.95, title, color=INK, fontsize=17, fontweight="bold", va="top")
    fig.text(0.06, 0.905, sub, color=MUTED, fontsize=10, va="top")
    fig.text(0.06, 0.025, "Nexis Charts · data: Yahoo Finance public endpoints", color=MUTED, fontsize=7.5)


def movers_chart(items: list[dict[str, Any]], title: str, sub: str, label_key: str = "symbol") -> bytes:
    rows = sorted([x for x in items if x.get("change_pct") is not None], key=lambda x: x["change_pct"])
    fig, ax = _fig()
    fig.subplots_adjust(left=0.30, right=0.9, top=0.84, bottom=0.08)
    labels = [str(x[label_key]).replace(".AE", "") for x in rows]
    vals = [x["change_pct"] * 100 for x in rows]
    ax.barh(labels, vals, color=[UP if v >= 0 else DOWN for v in vals], height=0.66)
    for i, v in enumerate(vals):
        ax.text(
            v + (0.05 if v >= 0 else -0.05),
            i,
            f"{v:+.2f}%",
            va="center",
            ha="left" if v >= 0 else "right",
            color=INK,
            fontsize=7.5,
        )
    ax.axvline(0, color=MUTED, linewidth=0.8)
    ax.tick_params(axis="y", colors=INK, labelsize=8)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _p: f"{v:+.0f}%"))
    lim = max(abs(min([*vals, 0])), abs(max([*vals, 0])), 0.5) * 1.35
    ax.set_xlim(-lim, lim)
    _title(fig, title, sub)
    return _png(fig)


def price_chart(symbol: str, name: str, bars: list[dict[str, Any]], currency: str) -> bytes:
    xs = [datetime.fromisoformat(b["t"]) for b in bars]
    ys = [b["close"] for b in bars]
    up = ys[-1] >= ys[0]
    color = UP if up else DOWN
    fig, ax = _fig()
    fig.subplots_adjust(left=0.1, right=0.95, top=0.8, bottom=0.1)
    ax.plot(xs, ys, color=color, linewidth=2)
    ax.fill_between(xs, ys, min(ys), color=color, alpha=0.12)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=5))
    chg = ys[-1] / ys[0] - 1
    _title(fig, name[:34], f"{symbol} · 3 months · {ys[-1]:,.2f} {currency} ({chg * 100:+.1f}%)")
    hi, lo = max(ys), min(ys)
    ax.annotate(
        f"high {hi:,.2f}", (xs[ys.index(hi)], hi), textcoords="offset points", xytext=(0, 8), ha="center", color=INK, fontsize=8
    )
    ax.annotate(
        f"low {lo:,.2f}", (xs[ys.index(lo)], lo), textcoords="offset points", xytext=(0, -14), ha="center", color=INK, fontsize=8
    )
    return _png(fig)


def _upsert(db: Session, page: User, key: str, body: str, image: bytes, symbols: list[str], tags: list[str]) -> bool:
    media = Media(user_id=page.id, content_type="image/jpeg", width=1080, height=1080, data=image)
    db.add(media)
    db.flush()
    p = db.scalars(select(Post).where(Post.external_key == key)).first()
    if p is None:
        db.add(
            Post(user_id=page.id, body=body, media_id=media.id, symbols=symbols, tags=tags, external_key=key, created_at=utcnow())
        )
        created = True
    else:
        old = p.media_id
        p.body, p.media_id, p.symbols = body, media.id, symbols
        if old:
            db.query(Media).filter(Media.id == old).delete()
        created = False
    db.commit()
    return created


def publish(db: Session, page: User, hot_symbol: str | None) -> int:
    today = datetime.now(UTC).date().isoformat()
    made = 0
    try:
        uae = markets.market_list(db, "uae")["items"]
        index = next((x for x in uae if x["symbol"] == "DFMGI.AE"), None)
        stocks = [x for x in uae if x["symbol"] != "DFMGI.AE" and x.get("change_pct") is not None]
        if stocks:
            top, bottom = max(stocks, key=lambda x: x["change_pct"]), min(stocks, key=lambda x: x["change_pct"])
            adv = sum(1 for x in stocks if x["change_pct"] > 0)
            dec = sum(1 for x in stocks if x["change_pct"] < 0)
            idx = (
                f"The DFM General Index is {index['change_pct'] * 100:+.2f}% at {index['price']:,.2f}. "
                if index and index.get("change_pct") is not None
                else ""
            )
            body = (f"📊 Dubai market pulse — {idx}{adv} stocks up, {dec} down.\n\nTop gainer: {top['name']} (${top['symbol']}) {top['change_pct'] * 100:+.2f}%. "
                    f"Biggest decliner: {bottom['name']} (${bottom['symbol']}) {bottom['change_pct'] * 100:+.2f}%.\n\n#uae #dubai #dfm #markets")  # fmt: skip
            img = movers_chart(stocks, "Dubai market pulse", f"DFM-listed shares · daily change · {today}")
            made += _upsert(
                db,
                page,
                f"chart:uae-pulse:{today}",
                body,
                img,
                [top["symbol"], bottom["symbol"]],
                ["uae", "dubai", "dfm", "markets"],
            )
    except NexisError as exc:
        log.warning("uae pulse chart skipped: %s", exc.message)
    try:
        idx = markets.market_list(db, "indices")["items"]
        rows = [{**x, "label": x["name"][:22]} for x in idx if x.get("change_pct") is not None]
        if rows:
            best, worst = max(rows, key=lambda x: x["change_pct"]), min(rows, key=lambda x: x["change_pct"])
            body = (f"🌍 World markets today — best: {best['name']} {best['change_pct'] * 100:+.2f}%, weakest: {worst['name']} "
                    f"{worst['change_pct'] * 100:+.2f}%.\n\n#markets #stocks #globalmarkets")  # fmt: skip
            img = movers_chart(rows, "World markets", f"Major indices · daily change · {today}", label_key="label")
            made += _upsert(db, page, f"chart:world:{today}", body, img, [], ["markets", "stocks", "globalmarkets"])
    except NexisError as exc:
        log.warning("world chart skipped: %s", exc.message)
    if hot_symbol:
        try:
            d = markets.details(db, hot_symbol)
            h = markets.history(db, hot_symbol, "1d", (utcnow() - timedelta(days=92)).date(), None)
            if len(h["bars"]) > 10:
                an = d["analysts"]
                extra = (
                    f" Analysts: {an['recommendation'].replace('_', ' ')} ({int(an['count'])}), mean target {an['target_mean']:,.2f}."
                    if an.get("recommendation") and an.get("count")
                    else ""
                )
                body = (f"📈 Chart of the day: {d['name']} (${d['symbol']}) — the most talked-about name in today's headlines. "
                        f"{d['quote']['price']:,.2f} {d['currency']}.{extra}\n\n#chartoftheday #stocks")  # fmt: skip
                img = price_chart(d["symbol"], d["name"], h["bars"], d["currency"])
                made += _upsert(db, page, f"chart:cotd:{today}", body, img, [d["symbol"]], ["chartoftheday", "stocks"])
        except NexisError as exc:
            log.warning("chart of the day skipped: %s", exc.message)
    return made
