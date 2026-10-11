"""Resend email (REST). Without a verified domain, Resend only delivers to the account owner's address."""
from __future__ import annotations

import html as html_lib

import httpx

from ..config import config
from . import verdict as verdict_engine

API = "https://api.resend.com/emails"


class EmailError(RuntimeError):
    pass


def available() -> bool:
    return bool(config.RESEND_API_KEY)


def send(to: str, subject: str, html: str) -> dict:
    if not available():
        raise EmailError("RESEND_API_KEY is not set")
    if not to:
        raise EmailError("No recipient email set (Settings → Report email)")
    resp = httpx.post(API, headers={"Authorization": f"Bearer {config.RESEND_API_KEY}", "Content-Type": "application/json"},
                      json={"from": config.REPORT_EMAIL_FROM, "to": [to], "subject": subject, "html": html}, timeout=15)
    if resp.status_code >= 300:
        raise EmailError(f"Resend HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def subject_for(report: dict) -> str:
    week = report["week_ending"]
    label = verdict_engine.weather(report["verdict"])["label"]
    return f"Your money weather for the week ending {week.day} {week.strftime('%b')}: {label}"


def _e(text) -> str:
    return html_lib.escape(str(text or ""))


def _pct(v) -> str:
    if v is None:
        return "–"
    arrow = "▲" if v >= 0 else "▼"
    sign = "+" if v >= 0 else "−"
    return f"{arrow} {sign}{abs(v):.2f}%"


def report_email_html(report: dict) -> str:
    """Simplified, inline-styled version of the report (email clients ignore stylesheets)."""
    rj = report["report_json"]
    facts = report["facts_json"]
    weather = verdict_engine.weather(report["verdict"])
    colour = {"clear": "#E9962E", "clouds": "#6B7A8F", "storm": "#7A3B69"}.get(weather["key"], "#0E7C7B")
    emoji = {"clear": "☀️", "clouds": "⛅", "storm": "🌧️"}.get(weather["key"], "🌤️")
    by_name = {f["name"]: f for f in facts.get("funds", [])}
    fund_blocks = []
    for f in rj.get("funds", []):
        ff = by_name.get(f["name"], {})
        sentences = " ".join(_e(s) for s in f.get("summary", []))
        fund_blocks.append(f"""
        <tr><td style="padding:14px 0;border-top:1px solid #E3E8E7">
          <div style="font-weight:600;font-size:16px;color:#172033">{_e(f['name'])}
            <span style="font-weight:400;color:#6B7A8F;font-size:13px">&nbsp;week {_pct(ff.get('ret_1w'))}</span></div>
          <div style="font-size:15px;line-height:1.55;color:#172033;margin-top:4px">{sentences}</div>
        </td></tr>""")
    link = f"{config.APP_BASE_URL}/reports/{report['week_ending'].isoformat()}"
    return f"""<!doctype html><html><body style="margin:0;background:#EEF2F1;font-family:Figtree,Segoe UI,Arial,sans-serif">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#EEF2F1;padding:24px 12px">
<tr><td align="center"><table role="presentation" width="600" cellspacing="0" cellpadding="0"
  style="max-width:600px;width:100%;background:#FFFFFF;border-radius:14px;overflow:hidden">
  <tr><td style="background:{colour};padding:28px 28px 22px;color:#FFFFFF">
    <div style="font-size:13px;opacity:.9">Mausam · week ending {report['week_ending'].strftime('%d %b %Y')}</div>
    <div style="font-size:30px;margin-top:6px">{emoji} {_e(weather['label'])}</div>
    <div style="font-size:17px;margin-top:8px;line-height:1.4">{_e(rj.get('headline'))}</div>
  </td></tr>
  <tr><td style="padding:22px 28px 6px">
    <div style="background:#F6F8F8;border-left:4px solid {colour};padding:12px 14px;border-radius:8px">
      <div style="font-weight:600;color:#172033">{_e(report['verdict'])}</div>
      <div style="color:#172033;margin-top:4px;line-height:1.5">{_e(rj.get('verdict_reason'))}</div>
    </div>
    <p style="color:#172033;line-height:1.55;font-size:15px">{_e(rj.get('market_paragraph'))}</p>
  </td></tr>
  <tr><td style="padding:0 28px"><table role="presentation" width="100%">{''.join(fund_blocks)}</table></td></tr>
  <tr><td style="padding:20px 28px 28px" align="center">
    <a href="{_e(link)}" style="display:inline-block;background:#0E7C7B;color:#FFFFFF;text-decoration:none;
       padding:12px 22px;border-radius:8px;font-weight:600">Open full report</a>
    <p style="color:#6B7A8F;font-size:12px;line-height:1.5;margin-top:18px">Mausam is an educational tool, not investment advice.
      Mutual fund investments are subject to market risks. Numbers are computed from public NAV data (mfapi.in);
      the words were written by Gemini Flash and checked by code.</p>
  </td></tr>
</table></td></tr></table></body></html>"""
