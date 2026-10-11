"""Jinja filters, globals and the layout context (data-as-of date, freshness dot, alerts bell)."""
from __future__ import annotations

import json
from datetime import date, datetime

from markupsafe import Markup, escape

from .services import glossary
from .services.attribution import label as attribution_label
from .services.benchmarks import ASSET_LABELS, BENCHMARK_LABELS
from .services.verdict import weather

NAV = [
    {"href": "/", "label": "Today", "icon": "sun", "mobile": True},
    {"href": "/portfolio", "label": "Portfolio", "icon": "pie-chart", "mobile": True},
    {"href": "/reports", "label": "Weekly reports", "icon": "newspaper", "mobile": True},
    {"href": "/trends", "label": "Trends", "icon": "activity"},
    {"href": "/market", "label": "Market pulse", "icon": "globe"},
    {"href": "/compare", "label": "Compare", "icon": "git-compare"},
    {"href": "/forecasts", "label": "Forecasts", "icon": "trending-up"},
    {"href": "/transactions", "label": "Transactions", "icon": "receipt", "mobile": True},
    {"href": "/manage", "label": "Manage funds", "icon": "list-plus"},
    {"href": "/calculators", "label": "Calculators", "icon": "calculator"},
    {"href": "/ask", "label": "Ask Mausam", "icon": "message-circle"},
    {"href": "/learn", "label": "Learn", "icon": "book-open"},
    {"href": "/alerts", "label": "Alerts", "icon": "bell"},
    {"href": "/settings", "label": "Settings", "icon": "settings"},
    {"href": "/status", "label": "System status", "icon": "server"},
    {"href": "/about", "label": "About", "icon": "info"},
]


def indian_group(n: int) -> str:
    s = str(abs(int(n)))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def inr(value, decimals: int = 0, signed: bool = False) -> str:
    if value is None:
        return "–"
    v = float(value)
    a = round(abs(v), decimals)  # round first, so 1234.999 -> 1,235.00 (not 1,234.00)
    whole = indian_group(round(a) if decimals == 0 else int(a))
    frac = ""
    if decimals:
        frac = f"{a - int(a):.{decimals}f}"[1:]
    sign = ("+" if v >= 0 else "−") if signed else ("−" if v < 0 else "")
    return f"{sign}₹{whole}{frac}"


def signed_class(value) -> str:
    if value is None:
        return "muted"
    return "gain" if float(value) >= 0 else "loss"


def pct(value, decimals: int = 2, arrow: bool = True) -> Markup:
    """Signed percentage with arrow and colour class (never colour alone)."""
    if value is None:
        return Markup('<span class="num muted" title="Not enough history">–</span>')
    v = float(value)
    sign = "+" if v >= 0 else "−"
    mark = ("▲ " if v >= 0 else "▼ ") if arrow else ""
    return Markup(f'<span class="num {signed_class(v)}">{mark}{sign}{abs(v):.{decimals}f}%</span>')


def pts(value, decimals: int = 2) -> Markup:
    if value is None:
        return Markup('<span class="num muted">–</span>')
    v = float(value)
    sign = "+" if v >= 0 else "−"
    return Markup(f'<span class="num {signed_class(v)}">{sign}{abs(v):.{decimals}f} pts</span>')


def money(value, signed: bool = False) -> Markup:
    if value is None:
        return Markup('<span class="num muted">–</span>')
    cls = signed_class(value) if signed else ""
    return Markup(f'<span class="num {cls}">{escape(inr(value, signed=signed))}</span>')


def fmt_date(value, style: str = "long") -> str:
    if value is None:
        return "–"
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return value
    if isinstance(value, datetime):
        value = value.date()
    if style == "short":
        return f"{value.day} {value.strftime('%b')}"
    return f"{value.day} {value.strftime('%b %Y')}"


def fmt_datetime(value) -> str:
    if not value:
        return "–"
    from zoneinfo import ZoneInfo

    from .config import config

    if isinstance(value, datetime):
        local = value.astimezone(ZoneInfo(config.TZ_DISPLAY)) if value.tzinfo else value
        return f"{local.day} {local.strftime('%b, %H:%M')}"
    return str(value)


def num(value, decimals: int = 2) -> str:
    if value is None:
        return "–"
    return f"{float(value):,.{decimals}f}"


def term(key: str, text: str | None = None) -> Markup:
    """A finance word with a dotted underline and a plain-language tooltip."""
    entry = glossary.GLOSSARY.get(key.lower())
    label = text or (entry["term"] if entry else key)
    if not entry:
        return Markup(escape(label))
    return Markup(f'<button type="button" class="term" data-tip="{escape(entry["short"])}" '
                  f'aria-label="{escape(label)}: {escape(entry["short"])}">{escape(label)}</button>')


def tojson_attr(value) -> str:
    return json.dumps(value, default=str)


def register(app) -> None:
    app.jinja_env.filters.update(
        inr=inr, pct=pct, pts=pts, money=money, d=fmt_date, dt=fmt_datetime, num=num,
        signed_class=signed_class, attribution=attribution_label,
        asset=lambda k: ASSET_LABELS.get(k or "", (k or "").capitalize()),
        bench=lambda k: BENCHMARK_LABELS.get(k or "", k or "–"),
        dumps=tojson_attr,
    )
    app.jinja_env.globals.update(term=term, weather=weather, NAV=NAV, glossary=glossary.GLOSSARY,
                                 BENCHMARK_LABELS=BENCHMARK_LABELS)

    @app.context_processor
    def layout_context():
        from flask import g, request

        from . import db
        from .config import config
        from .services.returns import is_stale

        ctx = {"csrf_token": getattr(g, "csrf_token", ""), "current_path": request.path,
               "data_as_of": None, "fresh": None, "unread_alerts": 0, "platform_url": config.INVESTING_PLATFORM_URL}
        if not config.DATABASE_URL:
            return ctx
        try:
            row = db.one("""SELECT MAX(n.nav_date) AS d FROM nav_history n JOIN funds f ON f.scheme_code = n.scheme_code
                            WHERE f.is_active AND f.user_id = %s""", [getattr(g, "user_id", 1)])
            d = row["d"] if row else None
            ctx["data_as_of"] = d
            ctx["fresh"] = (d is not None) and not is_stale(d, date.today())
            ctx["unread_alerts"] = int(db.scalar(
                "SELECT COUNT(*) FROM alerts WHERE (user_id = %s OR user_id IS NULL) AND read_at IS NULL",
                [getattr(g, "user_id", 1)]) or 0)
        except Exception:  # noqa: BLE001 - layout must render even if the DB is down
            pass
        return ctx
