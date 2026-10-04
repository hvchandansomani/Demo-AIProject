import hmac
import logging
import re

from flask import Blueprint, current_app, jsonify, render_template, request
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import metrics

bp = Blueprint("main", __name__)
log = logging.getLogger(__name__)

_CLIENT_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _svc():
    c = current_app.config
    return c["CFG"], c["DB"], c["CHAOS"], c["LIMITER"]


def _client_ip() -> str:
    return request.remote_addr or "unknown"


def _client_key() -> str:
    """Browsers send a random id they keep in localStorage. Using it (not
    only the IP) keeps learners behind one classroom NAT from sharing a limit."""
    cid = request.headers.get("X-Client-Id", "")
    return cid if _CLIENT_ID.match(cid) else _client_ip()


def _err(message: str, status: int):
    return jsonify(error=message), status


def _clean(text: str) -> str:
    return " ".join(_CONTROL.sub("", str(text or "")).split())


# ------------------------------------------------------------ guards
@bp.before_app_request
def _guards():
    cfg, _, chaos, limiter = _svc()
    path = request.path
    if not path.startswith("/api/"):
        return None
    if not limiter.allow(f"ip:{_client_ip()}", cfg.IP_REQUESTS_PER_MINUTE):
        metrics.RATE_LIMITED.labels("ip").inc()
        return _err("Too many requests from your network. Wait a minute and try again.", 429)
    if not path.startswith("/api/admin") and chaos.apply():
        return _err("Something went wrong on our side. Try again.", 500)
    return None


def _require_admin():
    cfg = _svc()[0]
    if not cfg.ADMIN_TOKEN:
        return _err("Admin features are off. Set ADMIN_TOKEN to turn them on.", 403)
    given = request.headers.get("X-Admin-Token", "")
    if not hmac.compare_digest(given.encode(), cfg.ADMIN_TOKEN.encode()):
        return _err("Admin token is missing or wrong.", 401)
    return None


# ------------------------------------------------------------ pages
@bp.get("/")
def index():
    return render_template("index.html", cfg=_svc()[0])


# ------------------------------------------------------------ probes
@bp.get("/health")
def health():
    """Liveness: the process is up. Deliberately does NOT check PostgreSQL,
    or a database blip would make Kubernetes restart every app pod."""
    return jsonify(status="ok")


@bp.get("/ready")
def ready():
    """Readiness: can this pod serve learners right now?"""
    _, db, chaos, _ = _svc()
    if not chaos.ready:
        return jsonify(status="not ready", reason="chaos: readiness disabled"), 503
    if not db.ping():
        return jsonify(status="not ready", reason="database unreachable"), 503
    return jsonify(status="ready")


@bp.get("/metrics")
def prom_metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


# ------------------------------------------------------------ learner API
@bp.get("/api/info")
def info():
    cfg = _svc()[0]
    return jsonify(app=cfg.APP_NAME, version=cfg.APP_VERSION, instance=cfg.INSTANCE,
                   banner=cfg.BANNER_TEXT, banner_color=cfg.BANNER_COLOR,
                   title=cfg.SESSION_TITLE)


@bp.get("/api/doubts")
def list_doubts():
    _, db, _, _ = _svc()
    sort = request.args.get("sort", "top")
    return jsonify(db.list_doubts("new" if sort == "new" else "top"))


@bp.post("/api/doubts")
def create_doubt():
    cfg, db, _, limiter = _svc()
    body = request.get_json(silent=True) or {}
    nickname = _clean(body.get("nickname"))
    question = _clean(body.get("question"))

    if not nickname:
        return _err("Add a nickname so the class knows who asked.", 400)
    if len(nickname) > cfg.MAX_NICKNAME:
        return _err(f"Keep the nickname under {cfg.MAX_NICKNAME} characters.", 400)
    if len(question) < 5:
        return _err("Write your doubt in at least 5 characters.", 400)
    if len(question) > cfg.MAX_QUESTION:
        return _err(f"Keep the doubt under {cfg.MAX_QUESTION} characters.", 400)
    if not limiter.allow(f"post:{_client_key()}", cfg.POSTS_PER_MINUTE):
        metrics.RATE_LIMITED.labels("post").inc()
        return _err("You're posting quickly. Wait a minute before the next doubt.", 429)

    doubt = db.create(nickname, question)
    metrics.DOUBTS_CREATED.inc()
    log.info("doubt %s posted by %s", doubt["id"], nickname)
    return jsonify(doubt), 201


@bp.post("/api/doubts/<int:doubt_id>/upvote")
def upvote(doubt_id):
    cfg, db, _, limiter = _svc()
    if not limiter.allow(f"vote:{_client_key()}", cfg.VOTES_PER_MINUTE):
        metrics.RATE_LIMITED.labels("vote").inc()
        return _err("Too many upvotes in a minute. Slow down a little.", 429)
    votes = db.upvote(doubt_id)
    if votes is None:
        return _err("That doubt no longer exists.", 404)
    metrics.UPVOTES.inc()
    return jsonify(id=doubt_id, upvotes=votes)


@bp.get("/api/stats")
def stats():
    cfg, db, _, _ = _svc()
    s = db.stats()
    metrics.DOUBTS_IN_DB.set(s["total"])
    return jsonify(s | {"instance": cfg.INSTANCE, "version": cfg.APP_VERSION})


# ------------------------------------------------------------ admin API
@bp.post("/api/admin/check")
def admin_check():
    return _require_admin() or jsonify(status="ok")


@bp.post("/api/admin/doubts/<int:doubt_id>/answered")
def mark_answered(doubt_id):
    denied = _require_admin()
    if denied:
        return denied
    answered = bool((request.get_json(silent=True) or {}).get("answered", True))
    if not _svc()[1].set_answered(doubt_id, answered):
        return _err("That doubt no longer exists.", 404)
    return jsonify(id=doubt_id, answered=answered)


@bp.delete("/api/admin/doubts/<int:doubt_id>")
def delete_doubt(doubt_id):
    denied = _require_admin()
    if denied:
        return denied
    if not _svc()[1].delete(doubt_id):
        return _err("That doubt no longer exists.", 404)
    return jsonify(id=doubt_id, deleted=True)


@bp.get("/api/admin/chaos")
def chaos_state():
    return _require_admin() or jsonify(_svc()[2].state())


@bp.post("/api/admin/chaos/<action>")
def chaos_action(action):
    denied = _require_admin()
    if denied:
        return denied
    cfg, _, chaos, limiter = _svc()
    if not cfg.CHAOS_ENABLED:
        return _err("Chaos is turned off (CHAOS_ENABLED=false).", 403)
    body = request.get_json(silent=True) or {}
    try:
        if action == "cpu":
            chaos.burn_cpu(body.get("seconds", 120))
        elif action == "errors":
            chaos.set_error_rate(body.get("percent", 30))
        elif action == "latency":
            chaos.set_latency(body.get("ms", 1500))
        elif action == "ready":
            chaos.set_ready(body.get("ready", True))
        elif action == "reset":
            chaos.reset()
            limiter.reset()
        else:
            return _err(f"Unknown chaos action '{action}'.", 404)
    except (TypeError, ValueError):
        return _err("Chaos values must be numbers.", 400)
    log.warning("chaos %s -> %s", action, chaos.state())
    return jsonify(chaos.state())
