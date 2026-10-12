"""Jinja filters, globals and the layout context (data-as-of date, freshness dot, alerts bell)."""
from __future__ import annotations

import json
from datetime import date, datetime

from markupsafe import Markup, escape

from .services import glossary
from .services.attribution import label as attribution_label
from .services.benchmarks import ASSET_LABELS, BENCHMARK_LABELS
from .services.verdict import weather

# Sidebar menu: plain-word page names in three groups. 'match' = extra URL prefixes that light the item up.
NAV_GROUPS = [
    ("This week", [
        # The Saturday report opens from Today, so Today stays lit on /reports pages.
        {"href": "/", "label": "Today", "icon": "sun", "mobile": True, "match": ["/reports"]},
        {"href": "/market", "label": "Markets and news", "icon": "globe"},
    ]),
    ("My money", [
        {"href": "/portfolio", "label": "My portfolio", "icon": "wallet", "mobile": True},
        {"href": "/manage", "label": "Manage funds", "icon": "layers", "mobile": True, "match": ["/funds"]},
        {"href": "/transactions", "label": "SIPs and payments", "icon": "receipt", "mobile": True},
    ]),
    ("Look ahead", [
        {"href": "/trends", "label": "Past weeks", "icon": "history"},
        {"href": "/forecasts", "label": "Next month", "icon": "telescope"},
        {"href": "/compare", "label": "Compare funds", "icon": "git-compare"},
        {"href": "/calculators", "label": "Calculators", "icon": "calculator"},
    ]),
]
NAV = [item for _, items in NAV_GROUPS for item in items]
# Reachable from the top bar instead of the sidebar
CORNER_LINKS = [
    {"href": "/alerts", "label": "Alerts", "icon": "bell"},
    {"href": "/settings", "label": "Settings", "icon": "settings"},
]


def nav_active(item: dict, path: str) -> bool:
    extra = item.get("match", [])
    if item["href"] == "/":
        return path == "/" or any(path.startswith(p) for p in extra)
    return any(path.startswith(p) for p in [item["href"], *extra])



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


def latest_updates(ctx: dict, report_week, news_item) -> list[dict]:
    """Short messages the birds carry across the top of the screen."""
    out = []
    sky = ctx.get("sky") or {}
    if report_week:
        out.append({"text": f"Saturday report ready: {sky.get('label', 'see this week')} for the week ending {fmt_date(report_week, 'short')}",
                    "href": f"/reports/{report_week.isoformat()}"})
    if ctx.get("data_as_of"):
        word = "Fresh" if ctx.get("fresh") else "Last"
        out.append({"text": f"{word} NAVs from {fmt_date(ctx['data_as_of'], 'short')}", "href": "/portfolio"})
    if ctx.get("unread_alerts"):
        n = ctx["unread_alerts"]
        out.append({"text": f"{n} new alert{'s' if n != 1 else ''} for you", "href": "/alerts"})
    if news_item and news_item.get("t"):
        title = news_item["t"] if len(news_item["t"]) <= 70 else news_item["t"][:67].rstrip() + "…"
        out.append({"text": f"In the news: {title}", "href": news_item.get("u") or "/market", "external": True})
    return out


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
    app.jinja_env.globals.update(term=term, weather=weather, NAV=NAV, NAV_GROUPS=NAV_GROUPS, CORNER_LINKS=CORNER_LINKS, nav_active=nav_active, glossary=glossary.GLOSSARY,
                                 BENCHMARK_LABELS=BENCHMARK_LABELS)

    @app.context_processor
    def layout_context():
        from flask import g, request

        from . import db
        from .config import config
        from .services.returns import is_stale

        ctx = {"updates": [], "csrf_token": getattr(g, "csrf_token", ""), "current_path": request.path,
               "data_as_of": None, "fresh": None, "sky": weather(None), "unread_alerts": 0, "platform_url": config.INVESTING_PLATFORM_URL}
        if not config.DATABASE_URL:
            return ctx
        try:
            # One round trip for everything the top bar needs (data date, sky, unread alerts).
            uid = getattr(g, "user_id", 1)
            row = db.one(
                """SELECT (SELECT MAX(n.nav_date) FROM nav_history n JOIN funds f ON f.scheme_code = n.scheme_code
                           WHERE f.is_active AND f.user_id = %(u)s) AS d,
                          (SELECT verdict FROM reports WHERE user_id = %(u)s ORDER BY week_ending DESC LIMIT 1) AS v,
                          (SELECT COUNT(*) FROM alerts WHERE (user_id = %(u)s OR user_id IS NULL) AND read_at IS NULL) AS c,
                          (SELECT week_ending FROM reports WHERE user_id = %(u)s ORDER BY week_ending DESC LIMIT 1) AS w,
                          (SELECT json_build_object('t', title, 'u', url) FROM news_items
                             ORDER BY published_at DESC NULLS LAST, id DESC LIMIT 1) AS n""",
                {"u": uid}) or {}
            d = row.get("d")
            ctx["data_as_of"] = d
            ctx["fresh"] = (d is not None) and not is_stale(d, date.today())
            ctx["sky"] = weather(row.get("v"))
            ctx["unread_alerts"] = int(row.get("c") or 0)
            ctx["updates"] = latest_updates(ctx, row.get("w"), row.get("n"))
        except Exception:  # noqa: BLE001 - layout must render even if the DB is down
            pass
        # Demo preview: ?sky=clear|clouds|storm shows that weather on any page.
        preview = {"clear": "No action needed", "clouds": "One fund worth watching",
                   "storm": "Consider reviewing your allocation"}.get(request.args.get("sky", ""))
        if preview:
            ctx["sky"] = weather(preview)
            ctx["sky_preview"] = True
        return ctx
