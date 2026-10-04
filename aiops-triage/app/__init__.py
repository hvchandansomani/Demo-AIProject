import hashlib
import logging
import secrets
import sys
from datetime import timedelta

from flask import Flask
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.middleware.proxy_fix import ProxyFix

from .analyzer import Analyzer
from .chaos import Chaos
from .config import Config
from .metrics import init_metrics
from .routes import bp
from .store import IncidentStore


def create_app(cfg: Config = None) -> Flask:
    cfg = cfg or Config()
    logging.basicConfig(
        stream=sys.stdout, level=logging.INFO,
        format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )

    app = Flask(__name__)
    app.config.update(
        CFG=cfg,
        STORE=IncidentStore(cfg.MAX_INCIDENTS),
        ANALYZER=Analyzer(cfg),
        CHAOS=Chaos(),
    )
    app.json.sort_keys = False

    # Same key on every replica, so a login works whichever pod answers.
    app.secret_key = cfg.SECRET_KEY or (
        hashlib.sha256(f"aiops-triage:{cfg.ADMIN_TOKEN}".encode()).hexdigest()
        if cfg.ADMIN_TOKEN else secrets.token_hex(32))
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_NAME="aiops_session",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    )
    init_metrics(app)
    app.register_blueprint(bp)

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    if cfg.URL_PREFIX:
        # Browser uses /ops/..., Kubernetes probes and Prometheus still hit /health, /metrics.
        app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {cfg.URL_PREFIX: app.wsgi_app})

    if cfg.LLM_MODE == "claude" and not cfg.ANTHROPIC_API_KEY:
        app.logger.warning("LLM_MODE=claude but ANTHROPIC_API_KEY is empty; using mock mode.")
    if not cfg.ADMIN_TOKEN:
        app.logger.warning("ADMIN_TOKEN is empty: the console is open to anyone who can reach it.")
    app.logger.info("started %s v%s mode=%s instance=%s",
                    cfg.APP_NAME, cfg.APP_VERSION, cfg.effective_mode, cfg.INSTANCE)
    return app
