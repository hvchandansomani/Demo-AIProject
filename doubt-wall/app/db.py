"""PostgreSQL access. Every SQL statement the app runs is in this file.

A connection pool is opened in the background, so the web server starts
even while PostgreSQL is still booting. /ready reports "not ready" until
the database answers, and Kubernetes holds traffic back until then."""
import logging
import threading
import time
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import metrics

log = logging.getLogger(__name__)

# The same table is created by k8s/base/postgres/init-app-role.sh in Kubernetes,
# where the app connects as a restricted role that cannot run DDL. Keep both in step.
# The CHECK limits are a hard ceiling in the database itself; the app enforces
# the tighter, configurable MAX_NICKNAME / MAX_QUESTION before any SQL runs.
SCHEMA = """
CREATE TABLE IF NOT EXISTS doubts (
    id         SERIAL PRIMARY KEY,
    nickname   TEXT        NOT NULL CHECK (char_length(nickname) BETWEEN 1 AND 100),
    question   TEXT        NOT NULL CHECK (char_length(question) BETWEEN 1 AND 2000),
    upvotes    INT         NOT NULL DEFAULT 0,
    answered   BOOLEAN     NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS doubts_created_at_idx ON doubts (created_at DESC);
"""

COLUMNS = "id, nickname, question, upvotes, answered, created_at"


def _row(r: dict) -> dict:
    r = dict(r)
    r["created_at"] = r["created_at"].isoformat()
    return r


class Database:
    def __init__(self, cfg):
        self.cfg = cfg
        self.pool = ConnectionPool(
            cfg.DATABASE_URL,
            min_size=cfg.DB_POOL_MIN,
            max_size=cfg.DB_POOL_MAX,
            timeout=cfg.DB_TIMEOUT_SECONDS,
            kwargs={"row_factory": dict_row, "connect_timeout": 5,
                    "application_name": "doubt-wall"},
            open=False,
        )
        self.pool.open(wait=False)
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    def close(self):
        self.pool.close()

    @contextmanager
    def _query(self, name: str):
        t0 = time.perf_counter()
        try:
            with self.pool.connection() as conn:
                yield conn
        except Exception:
            metrics.DB_ERRORS.labels(name).inc()
            raise
        finally:
            metrics.DB_LATENCY.labels(name).observe(time.perf_counter() - t0)

    # ------------------------------------------------------------ schema
    def ensure_schema(self) -> bool:
        if self._schema_ready:
            return True
        with self._schema_lock:
            if self._schema_ready:
                return True
            try:
                with self._query("schema") as conn:
                    exists = conn.execute(
                        "SELECT to_regclass('public.doubts') IS NOT NULL AS ok").fetchone()["ok"]
                    if not exists:
                        # Only reached when the app role is allowed to create tables
                        # (docker compose, local run). In Kubernetes the table is
                        # pre-created by the database owner, so no DDL runs here.
                        conn.execute(SCHEMA)
                self._schema_ready = True
                log.info("database schema ready")
            except Exception as exc:
                log.warning("database not reachable yet: %s", exc)
        return self._schema_ready

    def ping(self) -> bool:
        try:
            with self._query("ping") as conn:
                conn.execute("SELECT 1")
            return self.ensure_schema()
        except Exception:
            return False

    # ------------------------------------------------------------ doubts
    def list_doubts(self, sort: str = "top", limit: int = 200) -> list:
        order = ("answered ASC, upvotes DESC, created_at DESC" if sort == "top"
                 else "created_at DESC")
        with self._query("list") as conn:
            rows = conn.execute(
                f"SELECT {COLUMNS} FROM doubts ORDER BY {order} LIMIT %s", (limit,)
            ).fetchall()
        return [_row(r) for r in rows]

    def create(self, nickname: str, question: str) -> dict:
        with self._query("create") as conn:
            row = conn.execute(
                f"INSERT INTO doubts (nickname, question) VALUES (%s, %s) RETURNING {COLUMNS}",
                (nickname, question),
            ).fetchone()
        return _row(row)

    def upvote(self, doubt_id: int):
        with self._query("upvote") as conn:
            row = conn.execute(
                "UPDATE doubts SET upvotes = upvotes + 1 WHERE id = %s RETURNING upvotes",
                (doubt_id,),
            ).fetchone()
        return row["upvotes"] if row else None

    def set_answered(self, doubt_id: int, answered: bool) -> bool:
        with self._query("answer") as conn:
            cur = conn.execute("UPDATE doubts SET answered = %s WHERE id = %s",
                               (answered, doubt_id))
            return cur.rowcount == 1

    def delete(self, doubt_id: int) -> bool:
        with self._query("delete") as conn:
            cur = conn.execute("DELETE FROM doubts WHERE id = %s", (doubt_id,))
            return cur.rowcount == 1

    def stats(self) -> dict:
        with self._query("stats") as conn:
            row = conn.execute("""
                SELECT count(*)                          AS total,
                       count(*) FILTER (WHERE answered)  AS answered,
                       COALESCE(sum(upvotes), 0)         AS upvotes,
                       count(DISTINCT lower(nickname))   AS people
                FROM doubts
            """).fetchone()
        return {k: int(v) for k, v in row.items()}
