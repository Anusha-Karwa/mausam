"""Vercel entrypoint (zero-config Flask): Vercel looks for a Flask instance named `app` in index.py.

Static files live in public/static and are served by Vercel's CDN at /static/...
Locally, `flask --app index run` serves them from the same folder.
"""
from app import create_app

app = create_app()
