"""All settings come from environment variables so the same image runs
locally, in Docker, and in Kubernetes (via ConfigMap + Secret)."""
import os
import socket


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    APP_NAME = "AIOps Triage"
    APP_VERSION = os.getenv("APP_VERSION", "1.0.0")
    # Pod name in Kubernetes, container id in Docker — shown in the UI so
    # learners can see which replica served them.
    INSTANCE = os.getenv("POD_NAME") or socket.gethostname()

    # "mock" = rule-based answers, no API key, free and deterministic.
    # "claude" = real Claude API. Falls back to mock if no key is set.
    LLM_MODE = os.getenv("LLM_MODE", "mock").strip().lower()
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
    CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")
    MAX_TOKENS = int(os.getenv("MAX_TOKENS", "700"))
    LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))

    # USD per million tokens, used only for the cost estimate metric.
    # Check current pricing and override via env if it has changed.
    PRICE_INPUT_PER_MTOK = float(os.getenv("PRICE_INPUT_PER_MTOK", "1.0"))
    PRICE_OUTPUT_PER_MTOK = float(os.getenv("PRICE_OUTPUT_PER_MTOK", "5.0"))

    # Day 2 finale switches.
    CHAOS_ENABLED = _bool("CHAOS_ENABLED", True)
    K8S_CONTEXT_ENABLED = _bool("K8S_CONTEXT_ENABLED", False)

    # Instructor-only access. When set, the console asks for this token once
    # and remembers it in a signed cookie. Empty = open (fine on a laptop only).
    ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
    SECRET_KEY = os.getenv("SECRET_KEY", "")
    # When set, webhooks need ?token=<value> in their URL (Alertmanager, SNS).
    WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "")
    # Serve the console under a path, e.g. /ops behind the Ingress.
    URL_PREFIX = os.getenv("URL_PREFIX", "").rstrip("/")

    # Where the chaos switches act. Empty = on this app itself.
    # In the cluster: http://doubt-wall.marathon.svc.cluster.local
    CHAOS_TARGET_URL = os.getenv("CHAOS_TARGET_URL", "").rstrip("/")
    CHAOS_TARGET_NAME = os.getenv("CHAOS_TARGET_NAME", "Doubt Wall")
    # Admin token of the target app; defaults to this app's ADMIN_TOKEN.
    CHAOS_TARGET_TOKEN = os.getenv("CHAOS_TARGET_TOKEN", "") or os.getenv("ADMIN_TOKEN", "")
    # Optional "host:port" of a headless Service listing every target pod, e.g.
    # doubt-wall-pods:8000. When set, chaos switches go to each pod directly,
    # so "Fail 30%" means 30% on every replica, not just on whichever pod the
    # Service picked. Empty = send through CHAOS_TARGET_URL only.
    CHAOS_TARGET_PODS = os.getenv("CHAOS_TARGET_PODS", "").strip()

    MAX_INCIDENTS = int(os.getenv("MAX_INCIDENTS", "100"))
    MAX_INPUT_CHARS = int(os.getenv("MAX_INPUT_CHARS", "6000"))
    MAX_ALERTS_PER_WEBHOOK = int(os.getenv("MAX_ALERTS_PER_WEBHOOK", "5"))

    @property
    def effective_mode(self) -> str:
        if self.LLM_MODE == "claude" and self.ANTHROPIC_API_KEY:
            return "claude"
        return "mock"
