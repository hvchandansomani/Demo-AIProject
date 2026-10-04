"""Doubt Wall settings, all from environment variables."""
import os
import re
import socket
from urllib.parse import quote


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _database_url() -> str:
    """Use DATABASE_URL if given, otherwise build it from the parts that
    Kubernetes supplies (DB_PASSWORD comes from a Secret)."""
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    # User and password are percent-encoded, so passwords containing
    # characters like @ : / ? # still produce a valid connection URL.
    return "postgresql://{user}:{pw}@{host}:{port}/{db}".format(
        user=quote(os.getenv("DB_USER", "doubtwall"), safe=""),
        pw=quote(os.getenv("DB_PASSWORD", "doubtwall"), safe=""),
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        db=os.getenv("DB_NAME", "doubtwall"),
    )


class Config:
    APP_NAME = "Doubt Wall"
    APP_VERSION = os.getenv("APP_VERSION", "1.0.0")
    INSTANCE = os.getenv("POD_NAME") or socket.gethostname()

    # Shown across the top. Change it in a new build to demo a rolling update.
    BANNER_TEXT = os.getenv("BANNER_TEXT", "Welcome to the DevOps marathon. Ask anything.")
    # Only a hex colour is accepted, because it is written into the page CSS.
    BANNER_COLOR = (os.getenv("BANNER_COLOR", "#1F4E79")
                    if re.fullmatch(r"#[0-9A-Fa-f]{6}", os.getenv("BANNER_COLOR", "#1F4E79"))
                    else "#1F4E79")
    SESSION_TITLE = os.getenv("SESSION_TITLE", "DevOps Marathon")

    DATABASE_URL = _database_url()
    DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "1"))
    DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))
    DB_TIMEOUT_SECONDS = float(os.getenv("DB_TIMEOUT_SECONDS", "5"))

    # Moderation and chaos need this token. Empty = admin features disabled.
    ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
    CHAOS_ENABLED = _bool("CHAOS_ENABLED", True)

    # Abuse limits (per client, per pod, per minute)
    MAX_NICKNAME = int(os.getenv("MAX_NICKNAME", "30"))
    MAX_QUESTION = int(os.getenv("MAX_QUESTION", "500"))
    POSTS_PER_MINUTE = int(os.getenv("POSTS_PER_MINUTE", "5"))
    VOTES_PER_MINUTE = int(os.getenv("VOTES_PER_MINUTE", "60"))
    # A backstop against scripts, not the main limit. Each open browser makes about
    # 40 requests a minute while polling, and a classroom (or a load balancer that
    # hides client IPs) can put 60+ learners behind one IP. 6000/min covers ~150.
    IP_REQUESTS_PER_MINUTE = int(os.getenv("IP_REQUESTS_PER_MINUTE", "6000"))
