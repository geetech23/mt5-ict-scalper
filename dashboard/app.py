"""Flask dashboard (read-only view of the bot state)."""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict

from flask import Flask, jsonify, render_template


def create_app(snapshot: Callable[[], Dict[str, Any]]) -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index() -> str:
        return render_template("index.html")

    @app.route("/api/state")
    def state():
        resp = jsonify(snapshot())
        resp.headers["Cache-Control"] = "no-store"
        return resp

    return app


def start_dashboard(snapshot: Callable[[], Dict[str, Any]], host: str, port: int) -> threading.Thread:
    app = create_app(snapshot)
    t = threading.Thread(target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
                         daemon=True, name="dashboard")
    t.start()
    return t
