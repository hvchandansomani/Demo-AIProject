import hmac
import json
import logging
import re
import socket
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from flask import (Blueprint, current_app, jsonify, redirect, render_template,
                   request, session, url_for)
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import k8s_context, metrics
from .samples import SAMPLES

bp = Blueprint("main", __name__)
log = logging.getLogger(__name__)

_SNS_HOST = re.compile(r"^sns\.[a-z0-9-]+\.amazonaws\.com(\.cn)?$")


def _svc():
    return current_app.config["CFG"], current_app.config["STORE"], \
        current_app.config["ANALYZER"], current_app.config["CHAOS"]


def _record(source: str, title: str, text: str, labels: dict = None) -> dict:
    cfg, store, analyzer, _ = _svc()
    inc = store.add(source, title, text)
    context = k8s_context.gather(labels or {}, cfg.K8S_CONTEXT_ENABLED)
    analysis, meta = analyzer.analyze(text, context)
    inc.analysis, inc.meta, inc.severity = analysis, meta, analysis["severity"]
    if source == "manual" or not title:
        inc.title = analysis["title"]
    metrics.INCIDENTS.labels(inc.severity, source).inc()
    log.info("incident %s source=%s severity=%s mode=%s latency_ms=%s",
             inc.id, source, inc.severity, meta["mode"], meta["latency_ms"])
    return inc.to_dict()


# ------------------------------------------------------------------ access
# /api/work stands in for business traffic, so it stays open like a real endpoint.
_OPEN = ("/health", "/ready", "/metrics", "/static/", "/login", "/logout", "/webhook/", "/api/work")
LOGIN_ATTEMPTS_PER_MINUTE = 20


def _token_ok(given: str, expected: str) -> bool:
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


@bp.before_app_request
def _require_login():
    cfg = _svc()[0]
    if not cfg.ADMIN_TOKEN or session.get("admin") or request.path.startswith(_OPEN):
        return None
    if request.path.startswith("/api/"):
        return jsonify(error="Sign in to the console first."), 401
    return redirect(url_for("main.login"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    cfg = _svc()[0]
    if not cfg.ADMIN_TOKEN:
        return redirect(url_for("main.index"))
    error = None
    if request.method == "POST":
        attempts = current_app.config.setdefault("LOGIN_ATTEMPTS", {})
        ip = request.remote_addr or "?"
        now = time.time()
        recent = [t for t in attempts.get(ip, []) if t > now - 60]
        if len(recent) >= LOGIN_ATTEMPTS_PER_MINUTE:
            error = "Too many attempts. Wait a minute and try again."
        elif _token_ok(request.form.get("token", "").strip(), cfg.ADMIN_TOKEN):
            attempts.pop(ip, None)
            session.permanent = True
            session["admin"] = True
            return redirect(url_for("main.index"))
        else:
            recent.append(now)
            attempts[ip] = recent
            error = "That token is not correct."
    return render_template("login.html", cfg=cfg, error=error), (401 if error else 200)


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("main.login"))


def _webhook_denied():
    cfg = _svc()[0]
    if cfg.WEBHOOK_TOKEN and not _token_ok(request.args.get("token", ""), cfg.WEBHOOK_TOKEN):
        return jsonify(error="Webhook token is missing or wrong. Add ?token=... to the URL."), 401
    return None


# ------------------------------------------------------------------ pages
@bp.get("/")
def index():
    cfg, *_ = _svc()
    return render_template("index.html", cfg=cfg, samples=SAMPLES)


# ------------------------------------------------------------ probes/metrics
@bp.get("/health")
def health():
    """Liveness: the process is up. Never touches chaos or Claude."""
    return jsonify(status="ok")


@bp.get("/ready")
def ready():
    """Readiness: should this pod receive traffic?"""
    *_, chaos = _svc()
    if not chaos.ready:
        return jsonify(status="not ready", reason="chaos: readiness disabled"), 503
    return jsonify(status="ready")


@bp.get("/metrics")
def prom_metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


# ------------------------------------------------------------------ API
@bp.get("/api/info")
def info():
    cfg, *_ = _svc()
    return jsonify(app=cfg.APP_NAME, version=cfg.APP_VERSION, instance=cfg.INSTANCE,
                   mode=cfg.effective_mode, model=cfg.CLAUDE_MODEL if cfg.effective_mode == "claude" else None,
                   chaos_enabled=cfg.CHAOS_ENABLED, requested_mode=cfg.LLM_MODE,
                   chaos_target=cfg.CHAOS_TARGET_NAME if cfg.CHAOS_TARGET_URL else "this app")


@bp.get("/api/incidents")
def list_incidents():
    _, store, *_ = _svc()
    return jsonify(store.list(int(request.args.get("limit", 50))))


@bp.delete("/api/incidents")
def clear_incidents():
    _, store, *_ = _svc()
    store.clear()
    return jsonify(status="cleared")


@bp.get("/api/stats")
def stats():
    cfg, store, _, _ = _svc()
    return jsonify(store.stats() | {"instance": cfg.INSTANCE, "chaos": _chaos_state()})


@bp.post("/api/analyze")
def analyze():
    body = request.get_json(silent=True) or {}
    text = str(body.get("text", "")).strip()
    if not text:
        return jsonify(error="Paste an alert or log line to analyse."), 400
    return jsonify(_record("manual", "", text)), 201


@bp.get("/api/work")
def work():
    """A stand-in for real business traffic. Chaos error rate applies here,
    so Prometheus sees a genuine 5xx spike."""
    *_, chaos = _svc()
    if chaos.should_fail():
        return jsonify(error="injected failure"), 500
    return jsonify(status="ok")


# --------------------------------------------------------------- webhooks
@bp.post("/webhook/alertmanager")
def alertmanager():
    """Prometheus Alertmanager webhook receiver (payload version 4)."""
    denied = _webhook_denied()
    if denied:
        return denied
    cfg, *_ = _svc()
    payload = request.get_json(silent=True) or {}
    alerts = [a for a in payload.get("alerts", []) if a.get("status", "firing") == "firing"]
    created = []
    for alert in alerts[: cfg.MAX_ALERTS_PER_WEBHOOK]:
        labels = alert.get("labels", {})
        ann = alert.get("annotations", {})
        name = labels.get("alertname", "Alert")
        text = "\n".join(filter(None, [
            f"[FIRING] {name}",
            " ".join(f"{k}={v}" for k, v in labels.items() if k != "alertname"),
            ann.get("summary"), ann.get("description"),
            f"startsAt: {alert.get('startsAt', '')}",
        ]))
        created.append(_record("alertmanager", name, text, labels)["id"])
    return jsonify(received=len(payload.get("alerts", [])), analysed=created), 200


@bp.post("/webhook/cloudwatch")
def cloudwatch():
    """CloudWatch alarm -> SNS -> HTTP(S) subscription.
    Also accepts the bare alarm JSON for easy testing with curl."""
    denied = _webhook_denied()
    if denied:
        return denied
    raw = request.get_data(as_text=True) or "{}"
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return jsonify(error="Body must be JSON."), 400

    msg_type = request.headers.get("x-amz-sns-message-type") or body.get("Type")
    if msg_type == "SubscriptionConfirmation":
        url = body.get("SubscribeURL", "")
        host = urlparse(url).hostname or ""
        if urlparse(url).scheme != "https" or not _SNS_HOST.match(host):
            return jsonify(error="SubscribeURL is not an AWS SNS https URL."), 400
        try:
            urllib.request.urlopen(url, timeout=5).read()
        except Exception as exc:
            return jsonify(error=f"Could not confirm subscription: {exc}"), 502
        return jsonify(status="subscription confirmed")

    alarm = body
    if msg_type == "Notification":
        try:
            alarm = json.loads(body.get("Message", "{}"))
        except json.JSONDecodeError:
            alarm = {"AlarmName": body.get("Subject", "SNS message"),
                     "NewStateReason": body.get("Message", "")}

    if alarm.get("NewStateValue", "ALARM") != "ALARM":
        return jsonify(status="ignored", reason="alarm is not in ALARM state")

    trig = alarm.get("Trigger", {})
    name = alarm.get("AlarmName", "CloudWatch alarm")
    text = "\n".join(filter(None, [
        f"CloudWatch ALARM {name}",
        alarm.get("AlarmDescription"),
        alarm.get("NewStateReason"),
        f"metric: {trig.get('Namespace', '')}/{trig.get('MetricName', '')}" if trig else None,
    ]))
    return jsonify(_record("cloudwatch", name, text)), 201


# ------------------------------------------------------------------ chaos
# With CHAOS_TARGET_URL set, every switch is forwarded to the Doubt Wall's
# admin API, so the app learners are using is the one that breaks.

def _target(method: str, path: str, body: dict = None, timeout: float = 5.0,
            base: str = None, cfg=None):
    # cfg is passed in explicitly when called from worker threads, which have
    # no Flask app context of their own.
    cfg = cfg or _svc()[0]
    data = json.dumps(body or {}).encode() if method != "GET" else None
    req = urllib.request.Request(
        (base or cfg.CHAOS_TARGET_URL) + path, data=data, method=method,
        headers={"Content-Type": "application/json", "X-Admin-Token": cfg.CHAOS_TARGET_TOKEN},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read() or b"{}")
        except ValueError:
            payload = {}
        return exc.code, {"error": payload.get("error") or f"{cfg.CHAOS_TARGET_NAME} returned {exc.code}."}
    except Exception as exc:
        return 502, {"error": f"Could not reach {cfg.CHAOS_TARGET_NAME}: {exc.__class__.__name__}."}


def _pod_bases() -> list:
    """Every target pod as a base URL, from the headless Service's DNS records.
    Returns [] when fan-out isn't configured or the name doesn't resolve."""
    cfg = _svc()[0]
    if not cfg.CHAOS_TARGET_PODS:
        return []
    host, _, port = cfg.CHAOS_TARGET_PODS.rpartition(":")
    if not host or not port.isdigit():
        log.warning("CHAOS_TARGET_PODS must look like host:port, got %r", cfg.CHAOS_TARGET_PODS)
        return []
    try:
        infos = socket.getaddrinfo(host, int(port), type=socket.SOCK_STREAM)
    except OSError as exc:
        log.warning("could not resolve %s: %s", host, exc)
        return []
    bases = []
    for family, *_, addr in infos:
        ip = addr[0]
        url = f"http://[{ip}]:{port}" if family == socket.AF_INET6 else f"http://{ip}:{port}"
        if url not in bases:
            bases.append(url)
    return sorted(bases)


def _merge_states(states: list) -> dict:
    """One summary for the UI: the worst value across pods."""
    return {
        "error_rate": max(s.get("error_rate", 0) for s in states),
        "latency_ms": max(s.get("latency_ms", 0) for s in states),
        "cpu_burn_seconds_left": max(s.get("cpu_burn_seconds_left", 0) for s in states),
        "ready": all(s.get("ready", True) for s in states),
    }


def _chaos_call(method: str, path: str, body: dict = None, timeout: float = 5.0):
    """Send a chaos request to every pod (fan-out) or, without fan-out, through
    the Service. Returns (status, data) with data shaped like a chaos state."""
    cfg = _svc()[0]
    bases = _pod_bases()
    if not bases:
        return _target(method, path, body, timeout, cfg=cfg)
    with ThreadPoolExecutor(max_workers=min(10, len(bases))) as pool:
        results = list(pool.map(
            lambda b: _target(method, path, body, timeout, base=b, cfg=cfg), bases))
    ok = [data for status, data in results if status == 200]
    failed = [(status, data) for status, data in results if status != 200]
    summary = (_merge_states(ok) if ok else {}) | {"pods": len(bases), "pods_ok": len(ok)}
    if not failed:
        return 200, summary
    status, data = failed[0]
    error = data.get("error", "unknown error")
    if ok:
        error = f"Applied to {len(ok)} of {len(bases)} pods. {error}"
    return status, summary | {"error": error}


def _chaos_state() -> dict:
    cfg, *_, chaos = _svc()
    if not cfg.CHAOS_TARGET_URL:
        return chaos.state() | {"target": "this app"}
    status, data = _chaos_call("GET", "/api/admin/chaos", timeout=2)
    if status != 200 and not data.get("pods_ok"):
        return {"target": cfg.CHAOS_TARGET_NAME, "unreachable": True, "error": data.get("error")}
    return data | {"target": cfg.CHAOS_TARGET_NAME}


@bp.get("/api/chaos")
def chaos_state():
    return jsonify(_chaos_state())


def _send_traffic(count: int = 50) -> dict:
    cfg = _svc()[0]
    if cfg.CHAOS_TARGET_URL:
        url = cfg.CHAOS_TARGET_URL + "/api/doubts?sort=new"

        def hit(_):
            t0 = time.perf_counter()
            try:
                with urllib.request.urlopen(url, timeout=15) as r:
                    ok = r.status == 200
            except Exception:
                ok = False
            return ok, time.perf_counter() - t0

        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(hit, range(count)))
    else:
        client = current_app.test_client()
        results = []
        for _ in range(count):
            t0 = time.perf_counter()
            results.append((client.get("/api/work").status_code == 200, time.perf_counter() - t0))
    ok = sum(1 for r in results if r[0])
    return {"sent": count, "ok": ok, "failed": count - ok,
            "avg_ms": round(sum(r[1] for r in results) / count * 1000)}


@bp.post("/api/chaos/<action>")
def chaos_action(action):
    cfg, *_, chaos = _svc()
    if not cfg.CHAOS_ENABLED:
        return jsonify(error="Chaos is turned off (CHAOS_ENABLED=false)."), 403
    body = request.get_json(silent=True) or {}
    if action == "traffic":
        return jsonify(_send_traffic())
    if action not in {"cpu", "errors", "latency", "ready", "reset"}:
        return jsonify(error=f"Unknown chaos action '{action}'."), 404
    log.warning("chaos %s %s -> %s", action, body,
                cfg.CHAOS_TARGET_NAME if cfg.CHAOS_TARGET_URL else "self")
    if cfg.CHAOS_TARGET_URL:
        status, data = _chaos_call("POST", f"/api/admin/chaos/{action}", body)
        return jsonify(data | {"target": cfg.CHAOS_TARGET_NAME}), status
    try:
        if action == "cpu":
            chaos.burn_cpu(body.get("seconds", 120))
        elif action == "errors":
            chaos.set_error_rate(body.get("percent", 30))
        elif action == "latency":
            chaos.set_latency(body.get("ms", 1500))
        elif action == "ready":
            chaos.set_ready(body.get("ready", True))
        else:
            chaos.reset()
    except (TypeError, ValueError):
        return jsonify(error="Chaos values must be numbers."), 400
    return jsonify(chaos.state() | {"target": "this app"})
