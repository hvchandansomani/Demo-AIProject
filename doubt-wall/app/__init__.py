import logging
import os
import sys

from flask import Flask, jsonify
from psycopg import OperationalError
from psycopg_pool import PoolTimeout
from werkzeug.middleware.proxy_fix import ProxyFix

from .chaos import Chaos
from .config import Config
from .db import Database
from .metrics import init_metrics
from .ratelimit import RateLimiter
from .routes import bp


def create_app(cfg: Config = None) -> Flask:
    cfg = cfg or Config()
    logging.basicConfig(
        stream=sys.stdout, level=logging.INFO,
        format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )
    app = Flask(__name__)
    app.json.sort_keys = False

    # Behind Traefik/Ingress the real client IP arrives in X-Forwarded-For.
    if os.getenv("TRUST_PROXY", "true").lower() == "true":
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db = Database(cfg)
    app.config.update(CFG=cfg, DB=db, CHAOS=Chaos(), LIMITER=RateLimiter())
    init_metrics(app)
    app.register_blueprint(bp)

    @app.errorhandler(OperationalError)
    @app.errorhandler(PoolTimeout)
    def _db_down(exc):
        app.logger.error("database error: %s", exc)
        return jsonify(error="The database is not reachable right now. Try again in a moment."), 503

    @app.errorhandler(404)
    def _not_found(_):
        return jsonify(error="Not found."), 404

    db.ensure_schema()  # best effort; /ready keeps retrying if PostgreSQL is still starting
    if not cfg.ADMIN_TOKEN:
        app.logger.warning("ADMIN_TOKEN is empty: moderation and chaos are disabled.")
    app.logger.info("started %s v%s instance=%s", cfg.APP_NAME, cfg.APP_VERSION, cfg.INSTANCE)
    return app
