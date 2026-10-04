"""Optional: enrich an alert with live pod logs and events.

Off by default. Turn on with K8S_CONTEXT_ENABLED=true when running in the
cluster; the Deployment's ServiceAccount then needs read access to pods,
pods/log and events (see the RBAC note in the README)."""
import logging

log = logging.getLogger(__name__)
_api = None


def _core_api():
    global _api
    if _api is None:
        from kubernetes import client, config
        config.load_incluster_config()
        _api = client.CoreV1Api()
    return _api


def gather(labels: dict, enabled: bool) -> str:
    if not enabled:
        return ""
    ns = labels.get("namespace")
    pod = labels.get("pod")
    if not ns:
        return ""
    parts = []
    try:
        api = _core_api()
        if pod:
            logs = api.read_namespaced_pod_log(pod, ns, tail_lines=40, timestamps=False)
            parts.append(f"Last log lines of {pod}:\n{logs}")
        events = api.list_namespaced_event(ns, limit=15)
        lines = [
            f"{e.type} {e.reason} {e.involved_object.kind}/{e.involved_object.name}: {e.message}"
            for e in events.items[-15:]
        ]
        if lines:
            parts.append("Recent events:\n" + "\n".join(lines))
    except Exception as exc:
        log.warning("k8s context unavailable: %s", exc)
    return "\n\n".join(parts)
