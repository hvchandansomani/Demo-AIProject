"""Login, webhook token, URL prefix and chaos forwarding to a target app."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import create_app
from app.config import Config


def make_cfg(**over):
    cfg = Config()
    cfg.LLM_MODE, cfg.ANTHROPIC_API_KEY = "mock", ""
    cfg.ADMIN_TOKEN, cfg.WEBHOOK_TOKEN, cfg.URL_PREFIX = "s3cret", "", ""
    cfg.CHAOS_TARGET_URL, cfg.CHAOS_TARGET_TOKEN = "", "s3cret"
    cfg.SECRET_KEY = ""
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


def login(client, token="s3cret", prefix=""):
    return client.post(f"{prefix}/login", data={"token": token})


# ------------------------------------------------------------ login
def test_console_requires_login():
    c = create_app(make_cfg()).test_client()
    res = c.get("/")
    assert res.status_code == 302 and res.headers["Location"].endswith("/login")
    assert c.get("/api/incidents").status_code == 401
    assert c.post("/api/analyze", json={"text": "OOMKilled"}).status_code == 401
    assert c.post("/api/chaos/errors", json={"percent": 50}).status_code == 401


def test_probes_and_metrics_stay_open():
    c = create_app(make_cfg()).test_client()
    assert c.get("/health").status_code == 200
    assert c.get("/ready").status_code == 200
    assert c.get("/metrics").status_code == 200


def test_wrong_then_right_token():
    c = create_app(make_cfg()).test_client()
    bad = login(c, "nope")
    assert bad.status_code == 401 and b"not correct" in bad.data
    assert login(c).status_code == 302
    assert c.get("/").status_code == 200
    assert c.post("/api/analyze", json={"text": "OOMKilled"}).status_code == 201
    c.post("/logout")
    assert c.get("/api/incidents").status_code == 401


def test_login_is_rate_limited():
    c = create_app(make_cfg()).test_client()
    for _ in range(20):
        login(c, "wrong")
    res = login(c)  # even the right token is refused while locked
    assert b"Too many attempts" in res.data


def test_session_works_across_replicas():
    """Two pods with the same ADMIN_TOKEN must accept each other's cookie."""
    a = create_app(make_cfg()).test_client()
    b = create_app(make_cfg()).test_client()
    login(a)
    cookie = a.get_cookie("aiops_session")
    b.set_cookie("aiops_session", cookie.value)
    assert b.get("/api/incidents").status_code == 200


# ------------------------------------------------------------ webhooks
def test_webhook_token():
    c = create_app(make_cfg(WEBHOOK_TOKEN="hook")).test_client()
    body = {"alerts": [{"status": "firing", "labels": {"alertname": "HighCPU"},
                        "annotations": {"summary": "CPU high"}}]}
    assert c.post("/webhook/alertmanager", json=body).status_code == 401
    assert c.post("/webhook/alertmanager?token=bad", json=body).status_code == 401
    assert c.post("/webhook/alertmanager?token=hook", json=body).status_code == 200
    assert c.post("/webhook/cloudwatch", json={"AlarmName": "x"}).status_code == 401


# ------------------------------------------------------------ prefix
def test_url_prefix():
    c = create_app(make_cfg(URL_PREFIX="/ops")).test_client()
    res = c.get("/ops/")
    assert res.status_code == 302 and res.headers["Location"].endswith("/ops/login")
    assert login(c, prefix="/ops").status_code == 302
    page = c.get("/ops/")
    assert page.status_code == 200
    assert b'window.BASE = "/ops"' in page.data
    assert b"/ops/static/style.css" in page.data
    assert c.get("/ops/health").status_code == 200
    assert c.get("/health").status_code == 200  # probes can skip the prefix


# ------------------------------------------------------------ chaos target
class _Target(BaseHTTPRequestHandler):
    calls = []
    state = {"error_rate": 0, "latency_ms": 0, "ready": True, "cpu_burn_seconds_left": 0}

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/api/admin/chaos"):
            if self.headers.get("X-Admin-Token") != "s3cret":
                return self._send(401, {"error": "Admin token is missing or wrong."})
            return self._send(200, self.state)
        if self.path.startswith("/api/doubts"):
            return self._send(200, [])
        self._send(404, {"error": "Not found."})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        _Target.calls.append((self.path, body, self.headers.get("X-Admin-Token")))
        if self.headers.get("X-Admin-Token") != "s3cret":
            return self._send(401, {"error": "Admin token is missing or wrong."})
        if self.path.endswith("/errors"):
            self.state["error_rate"] = body.get("percent", 30)
        self._send(200, self.state)

    def log_message(self, *a):
        pass


@pytest.fixture()
def target():
    srv = HTTPServer(("127.0.0.1", 0), _Target)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Target.calls.clear()
    _Target.state["error_rate"] = 0
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_chaos_forwarded_to_target(target):
    c = create_app(make_cfg(CHAOS_TARGET_URL=target)).test_client()
    login(c)
    res = c.post("/api/chaos/errors", json={"percent": 30})
    assert res.status_code == 200 and res.json["target"] == "Doubt Wall"
    assert _Target.calls[-1] == ("/api/admin/chaos/errors", {"percent": 30}, "s3cret")
    stats = c.get("/api/stats").json
    assert stats["chaos"]["error_rate"] == 30 and stats["chaos"]["target"] == "Doubt Wall"
    local = c.application.config["CHAOS"]
    assert local.error_rate == 0  # the console itself is untouched


def test_traffic_to_target(target):
    c = create_app(make_cfg(CHAOS_TARGET_URL=target)).test_client()
    login(c)
    r = c.post("/api/chaos/traffic").json
    assert r["sent"] == 50 and r["ok"] == 50


def test_wrong_target_token_is_reported(target):
    c = create_app(make_cfg(CHAOS_TARGET_URL=target, CHAOS_TARGET_TOKEN="bad")).test_client()
    login(c)
    res = c.post("/api/chaos/errors", json={"percent": 30})
    assert res.status_code == 401 and "Admin token" in res.json["error"]


def test_unreachable_target():
    c = create_app(make_cfg(CHAOS_TARGET_URL="http://127.0.0.1:9")).test_client()
    login(c)
    res = c.post("/api/chaos/errors", json={"percent": 30})
    assert res.status_code == 502 and "Could not reach Doubt Wall" in res.json["error"]
    assert c.get("/api/stats").json["chaos"]["unreachable"] is True


def test_local_traffic_without_target():
    c = create_app(make_cfg()).test_client()
    login(c)
    c.post("/api/chaos/errors", json={"percent": 100})
    r = c.post("/api/chaos/traffic").json
    assert r["failed"] == 50
    c.post("/api/chaos/reset")


# ------------------------------------------------------------ chaos fan-out
def _pod_server(token="s3cret"):
    """A fake Doubt Wall pod with its own chaos state."""
    state = {"error_rate": 0, "latency_ms": 0, "ready": True, "cpu_burn_seconds_left": 0}

    class Pod(BaseHTTPRequestHandler):
        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.headers.get("X-Admin-Token") != token:
                return self._send(401, {"error": "Admin token is missing or wrong."})
            self._send(200, state)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.headers.get("X-Admin-Token") != token:
                return self._send(401, {"error": "Admin token is missing or wrong."})
            if self.path.endswith("/errors"):
                state["error_rate"] = body.get("percent", 30)
            elif self.path.endswith("/ready"):
                state["ready"] = body.get("ready", True)
            elif self.path.endswith("/reset"):
                state.update(error_rate=0, latency_ms=0, ready=True)
            self._send(200, state)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Pod)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, state


@pytest.fixture()
def two_pods(monkeypatch):
    (a, sa), (b, sb) = _pod_server(), _pod_server()
    bases = [f"http://127.0.0.1:{a.server_port}", f"http://127.0.0.1:{b.server_port}"]
    from app import routes
    monkeypatch.setattr(routes, "_pod_bases", lambda: bases)
    yield sa, sb
    a.shutdown(); b.shutdown()


def test_fanout_reaches_every_pod(two_pods, target):
    sa, sb = two_pods
    c = create_app(make_cfg(CHAOS_TARGET_URL=target, CHAOS_TARGET_PODS="unused:8000")).test_client()
    login(c)
    res = c.post("/api/chaos/errors", json={"percent": 30})
    assert res.status_code == 200
    assert sa["error_rate"] == 30 and sb["error_rate"] == 30
    assert res.json["pods"] == 2 and res.json["pods_ok"] == 2
    c.post("/api/chaos/ready", json={"ready": False})
    state = c.get("/api/stats").json["chaos"]
    assert state["ready"] is False and state["pods"] == 2
    c.post("/api/chaos/reset")
    assert sa == sb == {"error_rate": 0, "latency_ms": 0, "ready": True, "cpu_burn_seconds_left": 0}


def test_fanout_reports_partial_failure(monkeypatch, target):
    good, state = _pod_server()
    bad, _ = _pod_server(token="other")
    from app import routes
    monkeypatch.setattr(routes, "_pod_bases", lambda: [f"http://127.0.0.1:{good.server_port}",
                                                       f"http://127.0.0.1:{bad.server_port}"])
    c = create_app(make_cfg(CHAOS_TARGET_URL=target, CHAOS_TARGET_PODS="unused:8000")).test_client()
    login(c)
    res = c.post("/api/chaos/errors", json={"percent": 30})
    assert res.status_code == 401
    assert res.json["error"].startswith("Applied to 1 of 2 pods.")
    assert state["error_rate"] == 30
    good.shutdown(); bad.shutdown()


def test_pod_bases_parses_and_dedupes(monkeypatch):
    import socket as s
    from app import routes
    fake = [(s.AF_INET, s.SOCK_STREAM, 6, "", ("10.42.0.7", 8000)),
            (s.AF_INET, s.SOCK_STREAM, 6, "", ("10.42.0.5", 8000)),
            (s.AF_INET, s.SOCK_STREAM, 6, "", ("10.42.0.7", 8000))]
    monkeypatch.setattr(routes.socket, "getaddrinfo", lambda *a, **k: fake)
    app = create_app(make_cfg(CHAOS_TARGET_PODS="doubt-wall-pods:8000"))
    with app.app_context():
        assert routes._pod_bases() == ["http://10.42.0.5:8000", "http://10.42.0.7:8000"]
    bad = create_app(make_cfg(CHAOS_TARGET_PODS="no-port"))
    with bad.app_context():
        assert routes._pod_bases() == []


def test_fanout_falls_back_to_service_when_dns_fails(monkeypatch, target):
    from app import routes

    real = routes.socket.getaddrinfo

    def boom(host, *a, **k):
        if host == "doubt-wall-pods":
            raise OSError("no such host")
        return real(host, *a, **k)
    monkeypatch.setattr(routes.socket, "getaddrinfo", boom)
    c = create_app(make_cfg(CHAOS_TARGET_URL=target, CHAOS_TARGET_PODS="doubt-wall-pods:8000")).test_client()
    login(c)
    res = c.post("/api/chaos/errors", json={"percent": 30})
    assert res.status_code == 200 and "pods" not in res.json
