import json

import pytest

from app import create_app
from app.config import Config


class TestConfig(Config):
    LLM_MODE = "mock"
    ANTHROPIC_API_KEY = ""
    CHAOS_ENABLED = True


@pytest.fixture()
def client():
    app = create_app(TestConfig())
    app.testing = True
    return app.test_client()


def test_health_and_ready(client):
    assert client.get("/health").json == {"status": "ok"}
    assert client.get("/ready").status_code == 200


def test_index_renders(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"AIOps Triage" in res.data
    assert b"Mock mode" in res.data


def test_metrics_exposed(client):
    client.get("/api/work")
    body = client.get("/metrics").data.decode()
    assert "aiops_http_requests_total" in body
    assert "aiops_llm_requests_total" in body


def test_analyze_requires_text(client):
    res = client.post("/api/analyze", json={"text": "  "})
    assert res.status_code == 400
    assert "Paste an alert" in res.json["error"]


@pytest.mark.parametrize("text,severity,phrase", [
    ("Reason: OOMKilled, Exit Code: 137", "critical", "memory"),
    ("Status: CrashLoopBackOff", "critical", "exits right after starting"),
    ("CPU usage at 96% of limit", "warning", "CPU"),
    ("something odd happened", "info", "No known failure pattern"),
])
def test_mock_analysis(client, text, severity, phrase):
    res = client.post("/api/analyze", json={"text": text})
    assert res.status_code == 201
    inc = res.json
    assert inc["severity"] == severity
    assert phrase.lower() in inc["analysis"]["root_cause"].lower()
    assert inc["meta"]["mode"] == "mock"
    assert inc["analysis"]["suggested_actions"]


def test_claude_mode_without_key_falls_back_to_mock():
    cfg = TestConfig()
    cfg.LLM_MODE = "claude"
    assert cfg.effective_mode == "mock"


def test_alertmanager_webhook(client):
    payload = {
        "version": "4", "status": "firing",
        "alerts": [
            {"status": "firing",
             "labels": {"alertname": "HighCPU", "namespace": "aiops", "pod": "p1", "severity": "warning"},
             "annotations": {"summary": "CPU at 95% of limit"}},
            {"status": "resolved", "labels": {"alertname": "Old"}, "annotations": {}},
        ],
    }
    res = client.post("/webhook/alertmanager", json=payload)
    assert res.status_code == 200
    assert res.json["received"] == 2 and len(res.json["analysed"]) == 1
    incs = client.get("/api/incidents").json
    assert incs[0]["source"] == "alertmanager" and incs[0]["title"] == "HighCPU"


def test_cloudwatch_sns_notification(client):
    alarm = {"AlarmName": "k3s-node-disk", "NewStateValue": "ALARM",
             "NewStateReason": "disk_used_percent 93 > 85",
             "Trigger": {"Namespace": "CWAgent", "MetricName": "disk_used_percent"}}
    envelope = {"Type": "Notification", "Message": json.dumps(alarm)}
    res = client.post("/webhook/cloudwatch", data=json.dumps(envelope),
                      headers={"x-amz-sns-message-type": "Notification",
                               "Content-Type": "text/plain"})
    assert res.status_code == 201
    assert res.json["severity"] == "critical"


def test_cloudwatch_ok_state_ignored(client):
    res = client.post("/webhook/cloudwatch", json={"AlarmName": "x", "NewStateValue": "OK"})
    assert res.json["status"] == "ignored"


def test_sns_subscribe_url_must_be_aws(client):
    res = client.post("/webhook/cloudwatch",
                      json={"Type": "SubscriptionConfirmation", "SubscribeURL": "https://evil.example.com/x"})
    assert res.status_code == 400


def test_chaos_errors_and_readiness(client):
    client.post("/api/chaos/errors", json={"percent": 100})
    assert client.get("/api/work").status_code == 500
    client.post("/api/chaos/ready", json={"ready": False})
    assert client.get("/ready").status_code == 503
    assert client.get("/health").status_code == 200  # liveness unaffected
    client.post("/api/chaos/reset")
    assert client.get("/api/work").status_code == 200
    assert client.get("/ready").status_code == 200


def test_chaos_disabled():
    cfg = TestConfig()
    cfg.CHAOS_ENABLED = False
    c = create_app(cfg).test_client()
    assert c.post("/api/chaos/errors", json={"percent": 50}).status_code == 403


def test_stats_and_clear(client):
    client.post("/api/analyze", json={"text": "OOMKilled"})
    s = client.get("/api/stats").json
    assert s["total"] == 1 and s["critical"] == 1
    client.delete("/api/incidents")
    assert client.get("/api/stats").json["total"] == 0


class _FakeBlock:
    type = "text"
    text = '```json\n{"severity":"warning","title":"Slow DB","root_cause":"Connection pool exhausted.",' \
           '"impact":"Checkout slow","suggested_actions":["Raise pool size"],"commands":["kubectl top pods"],' \
           '"confidence":"high"}\n```'


class _FakeUsage:
    input_tokens = 1000
    output_tokens = 200


class _FakeResp:
    content = [_FakeBlock()]
    usage = _FakeUsage()


class _FakeClient:
    class messages:
        @staticmethod
        def create(**kwargs):
            assert kwargs["system"] and kwargs["max_tokens"] > 0
            return _FakeResp()


def test_claude_path_parses_json_and_costs():
    cfg = TestConfig()
    cfg.LLM_MODE, cfg.ANTHROPIC_API_KEY = "claude", "test-key"
    app = create_app(cfg)
    app.config["ANALYZER"]._client = _FakeClient()
    res = app.test_client().post("/api/analyze", json={"text": "db timeouts"})
    inc = res.json
    assert inc["severity"] == "warning"
    assert inc["meta"]["mode"] == "claude"
    assert inc["meta"]["input_tokens"] == 1000
    assert inc["meta"]["cost_usd"] == pytest.approx(0.002)


def test_claude_failure_falls_back_to_rules():
    cfg = TestConfig()
    cfg.LLM_MODE, cfg.ANTHROPIC_API_KEY = "claude", "test-key"
    app = create_app(cfg)

    class Broken:
        class messages:
            @staticmethod
            def create(**_):
                raise ConnectionError("no network")

    app.config["ANALYZER"]._client = Broken()
    inc = app.test_client().post("/api/analyze", json={"text": "OOMKilled"}).json
    assert inc["meta"]["mode"] == "mock" and "Claude call failed" in inc["meta"]["error"]
    assert inc["severity"] == "critical"
