"""Mausam Flask app factory.

Flask is imported inside create_app() so that the ML scripts (GitHub Actions)
can import app.services.* without installing Flask.
"""
from __future__ import annotations

USER_ID = 1  # single user (Meera) for now; every table already carries user_id for multi-user later


def create_app():
    import secrets
    import traceback
    from datetime import date, datetime

    from flask import Flask, g, jsonify, render_template, request
    from flask.json.provider import DefaultJSONProvider

    from . import db
    from .config import config
    from .templating import register as register_templating

    class JSONProvider(DefaultJSONProvider):
        @staticmethod
        def default(o):
            if isinstance(o, (date, datetime)):
                return o.isoformat()
            return DefaultJSONProvider.default(o)

    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.json_provider_class = JSONProvider
    app.json = JSONProvider(app)
    app.config["JSON_SORT_KEYS"] = False
    register_templating(app)

    @app.before_request
    def setup_and_csrf():
        g.user_id = USER_ID
        # CSRF: double-submit cookie. Pages set the cookie; JS sends it back in a header on writes.
        g.csrf_token = request.cookies.get("mausam_csrf") or secrets.token_urlsafe(24)
        path = request.path
        if path.startswith("/static/") or path in ("/api/health",):
            return None
        missing = config.missing_required()
        if missing and not path.startswith("/api/cron") and not path.startswith("/api/admin"):
            if path.startswith("/api/"):
                return jsonify({"ok": False, "data": None, "error": f"Server not configured: {', '.join(missing)} missing"}), 503
            return render_template("setup.html", variables=config.status(), missing=missing), 503
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and path.startswith("/api/") \
                and not path.startswith("/api/cron") and not path.startswith("/api/admin"):
            sent = request.headers.get("X-CSRF-Token", "")
            cookie = request.cookies.get("mausam_csrf", "")
            if not cookie or not secrets.compare_digest(sent, cookie):
                return jsonify({"ok": False, "data": None,
                                "error": "Security check failed. Please reload the page and try again."}), 403
        return None

    @app.after_request
    def set_cookie(resp):
        if not request.cookies.get("mausam_csrf") and getattr(g, "csrf_token", None):
            resp.set_cookie("mausam_csrf", g.csrf_token, samesite="Strict", secure=request.is_secure, httponly=False,
                            max_age=60 * 60 * 24 * 30)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return resp

    from .routes import (api_alerts, api_chat, api_forecasts, api_funds, api_market, api_portfolio, api_reports,
                         api_settings, api_transactions, admin, cron, health, pages)

    for module in (pages, api_funds, api_portfolio, api_transactions, api_reports, api_market, api_forecasts,
                   api_chat, api_alerts, api_settings, cron, health, admin):
        app.register_blueprint(module.bp)

    def _debug_allowed() -> bool:
        token = request.headers.get("X-Debug-Token", "")
        return bool(config.CRON_SECRET) and secrets.compare_digest(token, config.CRON_SECRET)

    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "data": None, "error": "Not found"}), 404
        return render_template("error.html", code=404, title="Page not found",
                               message="That page doesn't exist. Try the menu on the left."), 404

    @app.errorhandler(Exception)
    def server_error(e):
        from werkzeug.exceptions import HTTPException

        if isinstance(e, HTTPException):
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "data": None, "error": e.description}), e.code
            return render_template("error.html", code=e.code, title=e.name, message=e.description), e.code
        app.logger.exception("Unhandled error")
        if isinstance(e, db.DatabaseNotConfigured):
            return render_template("setup.html", variables=config.status(), missing=["DATABASE_URL"]), 503
        if _debug_allowed():
            return "<pre>" + traceback.format_exc() + "</pre>", 500
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "data": None, "error": "Something went wrong on our side. Please try again."}), 500
        return render_template("error.html", code=500, title="Something went wrong",
                               message="Mausam hit an unexpected problem. Your data is safe. Try again, or check System status."), 500

    return app
