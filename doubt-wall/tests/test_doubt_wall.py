"""Doubt Wall tests. They run against a real PostgreSQL database:

    TEST_DATABASE_URL=postgresql://doubtwall:doubtwall@localhost:5432/doubtwall_test pytest -q

In Jenkins, start a throwaway container first:
    docker run -d --name pg-test -e POSTGRES_USER=doubtwall -e POSTGRES_PASSWORD=doubtwall \\
      -e POSTGRES_DB=doubtwall_test -p 5432:5432 postgres:18-alpine
"""
import os
import threading
import uuid

import psycopg
import pytest

from app import create_app
from app.config import Config

TEST_DB = os.getenv("TEST_DATABASE_URL",
                    "postgresql://doubtwall:doubtwall@localhost:5432/doubtwall_test")


def _db_available() -> bool:
    try:
        with psycopg.connect(TEST_DB, connect_timeout=3):
            return True
    except Exception:
        return False


_DB_UP = _db_available()

# Locally a missing database just skips these tests. In CI (Jenkins sets
# REQUIRE_DB=1) a missing database is an error, so a broken pipeline can never
# report "0 failed" by quietly skipping everything.
if not _DB_UP and os.getenv("REQUIRE_DB"):
    raise RuntimeError(f"REQUIRE_DB is set but PostgreSQL is not reachable at {TEST_DB}")

pytestmark = pytest.mark.skipif(not _DB_UP, reason=f"PostgreSQL not reachable at {TEST_DB}")


def make_cfg(**over):
    cfg = Config()
    cfg.DATABASE_URL = TEST_DB
    cfg.ADMIN_TOKEN = "adm1n"
    cfg.CHAOS_ENABLED = True
    cfg.POSTS_PER_MINUTE = 5
    cfg.VOTES_PER_MINUTE = 60
    cfg.IP_REQUESTS_PER_MINUTE = 10_000
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


@pytest.fixture()
def app():
    application = create_app(make_cfg())
    with psycopg.connect(TEST_DB, autocommit=True) as conn:
        conn.execute("TRUNCATE doubts RESTART IDENTITY")
    yield application
    application.config["DB"].close()


@pytest.fixture()
def client(app):
    return app.test_client()


def cid():
    return {"X-Client-Id": str(uuid.uuid4())}


ADMIN = {"X-Admin-Token": "adm1n"}


def post(client, nickname="Priya", question="Why does my pod restart?", headers=None):
    return client.post("/api/doubts", json={"nickname": nickname, "question": question},
                       headers=headers or cid())


# ------------------------------------------------------------ basics
def test_health_ready_page(client):
    assert client.get("/health").json == {"status": "ok"}
    assert client.get("/ready").json == {"status": "ready"}
    page = client.get("/")
    assert page.status_code == 200 and b"Ask a doubt" in page.data


def test_post_and_list(client):
    res = post(client)
    assert res.status_code == 201
    d = res.json
    assert d["id"] == 1 and d["upvotes"] == 0 and d["answered"] is False
    assert client.get("/api/doubts").json[0]["question"] == "Why does my pod restart?"


def test_whitespace_is_tidied(client):
    d = post(client, "  Asha  ", "  Why   is\nTerraform   slow?  ").json
    assert d["nickname"] == "Asha" and d["question"] == "Why is Terraform slow?"


@pytest.mark.parametrize("nick,q,msg", [
    ("", "A real question here", "Add a nickname"),
    ("x" * 31, "A real question here", "nickname under 30"),
    ("Ravi", "hey", "at least 5"),
    ("Ravi", "q" * 501, "under 500"),
])
def test_validation(client, nick, q, msg):
    res = post(client, nick, q)
    assert res.status_code == 400 and msg in res.json["error"]


def test_html_is_stored_as_text(client):
    """The API returns exactly what was typed; the browser escapes it on render."""
    d = post(client, "Eve", "<script>alert(1)</script> is this safe?").json
    assert d["question"] == "<script>alert(1)</script> is this safe?"


def test_upvote_and_sorting(client):
    a = post(client, "A", "First question here").json["id"]
    b = post(client, "B", "Second question here").json["id"]
    for _ in range(3):
        assert client.post(f"/api/doubts/{b}/upvote", headers=cid()).status_code == 200
    top = client.get("/api/doubts?sort=top").json
    new = client.get("/api/doubts?sort=new").json
    assert [d["id"] for d in top] == [b, a]
    assert [d["id"] for d in new] == [b, a]
    assert top[0]["upvotes"] == 3
    assert client.post("/api/doubts/999/upvote", headers=cid()).status_code == 404


def test_concurrent_upvotes_are_not_lost(app):
    """40 learners upvoting at the same moment: PostgreSQL's
    UPDATE ... SET upvotes = upvotes + 1 keeps every vote."""
    c = app.test_client()
    doubt = post(c).json["id"]
    errors = []

    def vote():
        r = app.test_client().post(f"/api/doubts/{doubt}/upvote", headers=cid())
        if r.status_code != 200:
            errors.append(r.status_code)

    threads = [threading.Thread(target=vote) for _ in range(40)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert c.get("/api/doubts").json[0]["upvotes"] == 40


def test_stats(client):
    post(client, "Priya", "Question number one")
    post(client, "priya", "Question number two")
    d = post(client, "Rahul", "Question number three").json["id"]
    client.post(f"/api/doubts/{d}/upvote", headers=cid())
    client.post(f"/api/admin/doubts/{d}/answered", json={"answered": True}, headers=ADMIN)
    s = client.get("/api/stats").json
    assert (s["total"], s["answered"], s["upvotes"], s["people"]) == (3, 1, 1, 2)


# ------------------------------------------------------------ rate limits
def test_post_rate_limit_is_per_learner(client):
    me = cid()
    for i in range(5):
        assert post(client, "Me", f"Question number {i}", me).status_code == 201
    res = post(client, "Me", "One too many questions", me)
    assert res.status_code == 429 and "Wait a minute" in res.json["error"]
    # Someone else on the same classroom Wi-Fi is not affected
    assert post(client, "Friend", "My own question here").status_code == 201


def test_ip_rate_limit():
    application = create_app(make_cfg(IP_REQUESTS_PER_MINUTE=5))
    c = application.test_client()
    codes = [c.get("/api/stats").status_code for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429
    assert c.get("/health").status_code == 200  # probes never limited
    application.config["DB"].close()


# ------------------------------------------------------------ admin
def test_admin_requires_token(client):
    d = post(client).json["id"]
    assert client.post("/api/admin/check").status_code == 401
    assert client.post("/api/admin/check", headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.post("/api/admin/check", headers=ADMIN).status_code == 200
    assert client.delete(f"/api/admin/doubts/{d}").status_code == 401
    assert client.post("/api/admin/chaos/errors", json={"percent": 50}).status_code == 401


def test_admin_disabled_without_token():
    application = create_app(make_cfg(ADMIN_TOKEN=""))
    c = application.test_client()
    res = c.post("/api/admin/check", headers={"X-Admin-Token": ""})
    assert res.status_code == 403 and "Admin features are off" in res.json["error"]
    application.config["DB"].close()


def test_answer_and_delete(client):
    d = post(client).json["id"]
    r = client.post(f"/api/admin/doubts/{d}/answered", json={"answered": True}, headers=ADMIN)
    assert r.json == {"id": d, "answered": True}
    assert client.get("/api/doubts").json[0]["answered"] is True
    assert client.delete(f"/api/admin/doubts/{d}", headers=ADMIN).status_code == 200
    assert client.get("/api/doubts").json == []
    assert client.delete(f"/api/admin/doubts/{d}", headers=ADMIN).status_code == 404


# ------------------------------------------------------------ chaos
def test_chaos_errors_hit_learners_not_admin(client):
    client.post("/api/admin/chaos/errors", json={"percent": 100}, headers=ADMIN)
    assert client.get("/api/doubts").status_code == 500
    assert post(client).status_code == 500
    assert client.get("/api/admin/chaos", headers=ADMIN).status_code == 200
    client.post("/api/admin/chaos/reset", headers=ADMIN)
    assert client.get("/api/doubts").status_code == 200


def test_chaos_latency(client):
    import time
    client.post("/api/admin/chaos/latency", json={"ms": 300}, headers=ADMIN)
    t0 = time.perf_counter()
    client.get("/api/doubts")
    assert time.perf_counter() - t0 >= 0.3
    client.post("/api/admin/chaos/reset", headers=ADMIN)


def test_chaos_readiness(client):
    client.post("/api/admin/chaos/ready", json={"ready": False}, headers=ADMIN)
    assert client.get("/ready").status_code == 503
    assert client.get("/health").status_code == 200
    client.post("/api/admin/chaos/reset", headers=ADMIN)
    assert client.get("/ready").status_code == 200


def test_chaos_bad_input(client):
    r = client.post("/api/admin/chaos/errors", json={"percent": "lots"}, headers=ADMIN)
    assert r.status_code == 400
    assert client.post("/api/admin/chaos/explode", headers=ADMIN).status_code == 404


# ------------------------------------------------------------ database
def test_data_survives_app_restart(client):
    post(client, "Priya", "Will this survive a restart?")
    fresh = create_app(make_cfg())
    assert fresh.test_client().get("/api/doubts").json[0]["nickname"] == "Priya"
    fresh.config["DB"].close()


def test_database_down():
    bad = create_app(make_cfg(DATABASE_URL="postgresql://nobody:x@127.0.0.1:1/none",
                              DB_TIMEOUT_SECONDS=1))
    c = bad.test_client()
    assert c.get("/health").status_code == 200           # liveness: keep the pod
    ready = c.get("/ready")
    assert ready.status_code == 503 and "database" in ready.json["reason"]
    res = c.get("/api/doubts")
    assert res.status_code == 503 and "database is not reachable" in res.json["error"]
    bad.config["DB"].close()


def test_metrics(client):
    post(client)
    body = client.get("/metrics").data.decode()
    for name in ("doubtwall_http_requests_total", "doubtwall_db_query_duration_seconds",
                 "doubtwall_doubts_created_total", "doubtwall_chaos_error_rate_percent"):
        assert name in body


def test_banner_color_is_validated(monkeypatch):
    monkeypatch.setenv("BANNER_COLOR", "red;}body{display:none")
    import importlib
    import app.config as config_module
    importlib.reload(config_module)
    assert config_module.Config.BANNER_COLOR == "#1F4E79"
    monkeypatch.setenv("BANNER_COLOR", "#0B7A3E")
    importlib.reload(config_module)
    assert config_module.Config.BANNER_COLOR == "#0B7A3E"


def test_database_check_constraint_is_a_hard_ceiling(app):
    """Even if the app's own limit were raised or bypassed, PostgreSQL refuses huge rows."""
    with psycopg.connect(TEST_DB, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO doubts (nickname, question) VALUES ('x', %s)", ("q" * 2001,))


def test_database_url_encodes_special_characters(monkeypatch):
    import importlib
    import app.config as config_module
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_PASSWORD", "p@ss:w/rd?#1")
    importlib.reload(config_module)
    assert config_module.Config.DATABASE_URL.startswith("postgresql://doubtwall:p%40ss%3Aw%2Frd%3F%231@")
    monkeypatch.delenv("DB_PASSWORD")
    importlib.reload(config_module)
