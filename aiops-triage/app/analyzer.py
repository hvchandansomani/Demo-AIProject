"""Turns an alert or log snippet into a structured diagnosis.

Two engines share one output shape, so the UI never cares which ran:
  mock   - regex rules for common Kubernetes/AWS failures (free, offline)
  claude - the Claude Messages API, asked to return strict JSON
"""
import json
import re
import time

from . import metrics

SEVERITIES = ("critical", "warning", "info")

SYSTEM_PROMPT = """You are an on-call SRE assistant for a small app running on \
Kubernetes (k3s on AWS EC2), built by Jenkins and monitored by Prometheus, \
Grafana and CloudWatch.

Read the alert or log and reply with ONLY a JSON object, no prose, no code fences:
{
  "severity": "critical" | "warning" | "info",
  "title": "short incident title, max 8 words",
  "root_cause": "most likely cause in 1-2 sentences",
  "impact": "who or what is affected, 1 sentence",
  "suggested_actions": ["ordered, concrete steps, max 5"],
  "commands": ["kubectl/aws/helm commands that help verify or fix, max 4"],
  "confidence": "high" | "medium" | "low"
}
Be specific to the evidence. If the evidence is thin, say so in root_cause and \
set confidence to low."""

# ---------------------------------------------------------------- mock engine
MOCK_RULES = [
    {
        "match": r"oomkilled|exit code 137|out of memory|memory limit",
        "severity": "critical",
        "title": "Container killed for exceeding memory",
        "root_cause": "The container used more memory than its limit and the kernel OOM killer stopped it (exit code 137).",
        "impact": "Pod restarts repeatedly; in-flight requests on that pod fail.",
        "suggested_actions": [
            "Confirm the OOMKilled reason in the pod's last state",
            "Compare actual usage against resources.limits.memory",
            "Raise the memory limit or fix the leak, then redeploy through Jenkins",
            "Add a Prometheus alert on container_memory_working_set_bytes near the limit",
        ],
        "commands": [
            "kubectl describe pod <pod> -n <ns> | grep -A3 'Last State'",
            "kubectl top pod -n <ns>",
        ],
    },
    {
        "match": r"crashloopbackoff|back-off restarting",
        "severity": "critical",
        "title": "Pod stuck in CrashLoopBackOff",
        "root_cause": "The app exits right after starting. Usual suspects are a missing env var or Secret, a bad config, or a failing dependency at boot.",
        "impact": "No healthy replicas of this version; the rollout stalls.",
        "suggested_actions": [
            "Read logs from the previous (crashed) container",
            "Check that the Secret and ConfigMap the Deployment references exist",
            "Roll back to the last good revision if users are affected",
        ],
        "commands": [
            "kubectl logs <pod> -n <ns> --previous",
            "kubectl get secret,configmap -n <ns>",
            "kubectl rollout undo deployment/<name> -n <ns>",
        ],
    },
    {
        "match": r"imagepullbackoff|errimagepull|pull access denied|manifest unknown",
        "severity": "critical",
        "title": "Cluster cannot pull the container image",
        "root_cause": "The image tag does not exist in ECR, or the node has no permission to pull from the registry.",
        "impact": "New pods never start; the deployment is stuck on the old version.",
        "suggested_actions": [
            "Check the exact image:tag in the Deployment against ECR",
            "Confirm the Jenkins push stage succeeded for that build number",
            "Check the ECR pull secret or the node's IAM role",
        ],
        "commands": [
            "kubectl describe pod <pod> -n <ns> | grep -i image",
            "aws ecr describe-images --repository-name aiops-triage --query 'imageDetails[].imageTags'",
        ],
    },
    {
        "match": r"\b5\d\d\b|error rate|internal server error|bad gateway|service unavailable",
        "severity": "critical",
        "title": "Spike in server errors",
        "root_cause": "The service is returning 5xx responses. Check whether it started with a deploy, a dependency failure, or injected chaos.",
        "impact": "Users see failed requests; the error-rate SLO is burning.",
        "suggested_actions": [
            "Line up the error spike with the last deploy time in Grafana",
            "Check pod logs for stack traces",
            "Roll back if the spike began at a deploy",
        ],
        "commands": [
            "kubectl rollout history deployment/<name> -n <ns>",
            "kubectl logs -l app=aiops-triage -n <ns> --tail=100",
        ],
    },
    {
        "match": r"cpu|throttl",
        "severity": "warning",
        "title": "High CPU usage",
        "root_cause": "CPU usage is close to the container limit, most likely from a traffic burst or a CPU-heavy code path.",
        "impact": "Slower responses; the HPA should add replicas if it is configured.",
        "suggested_actions": [
            "Check whether the HPA is scaling and has hit maxReplicas",
            "Look at request rate in Grafana to separate load from a hot loop",
            "Raise CPU requests/limits or maxReplicas if load is real",
        ],
        "commands": [
            "kubectl get hpa -n <ns>",
            "kubectl top pods -n <ns>",
        ],
    },
    {
        "match": r"disk|no space left|diskpressure|filesystem",
        "severity": "critical",
        "title": "Node is running out of disk",
        "root_cause": "The node's disk is nearly full, usually from old container images or growing logs.",
        "impact": "Kubelet may evict pods and new images cannot be pulled.",
        "suggested_actions": [
            "Find the largest directories on the node",
            "Prune unused images",
            "Increase the EBS volume size in Terraform for a lasting fix",
        ],
        "commands": [
            "df -h && sudo du -sh /var/lib/rancher/* | sort -h",
            "sudo k3s crictl rmi --prune",
        ],
    },
    {
        "match": r"readiness probe failed|liveness probe failed|unhealthy",
        "severity": "warning",
        "title": "Health probes are failing",
        "root_cause": "The kubelet's probe is not getting a healthy response, so the pod is removed from the Service or restarted.",
        "impact": "Reduced capacity; traffic concentrates on the remaining pods.",
        "suggested_actions": [
            "Call /ready and /health from inside the pod",
            "Check if probe timeouts are shorter than app start-up time",
        ],
        "commands": [
            "kubectl describe pod <pod> -n <ns> | grep -A5 -i probe",
            "kubectl exec <pod> -n <ns> -- python -c \"import urllib.request;print(urllib.request.urlopen('http://localhost:8000/ready').read())\"",
        ],
    },
    {
        "match": r"notready|node.*not ready|kubelet stopped",
        "severity": "critical",
        "title": "Node is not ready",
        "root_cause": "The kubelet on the node stopped reporting, often from the EC2 instance being stopped, out of memory, or network loss.",
        "impact": "Pods on the node are unavailable until rescheduled.",
        "suggested_actions": [
            "Check the EC2 instance state and status checks in CloudWatch",
            "Check the k3s service on the node",
        ],
        "commands": [
            "kubectl get nodes -o wide",
            "sudo systemctl status k3s",
        ],
    },
    {
        "match": r"latency|timeout|timed out|slow",
        "severity": "warning",
        "title": "Responses are slow",
        "root_cause": "Request latency is above normal. A slow dependency or saturated CPU is the most common reason.",
        "impact": "Users wait longer; some requests may time out.",
        "suggested_actions": [
            "Compare p95 latency with CPU in Grafana",
            "Check the AI latency panel for slow upstream calls",
        ],
        "commands": ["kubectl top pods -n <ns>"],
    },
]

FALLBACK = {
    "severity": "info",
    "title": "Needs a human look",
    "root_cause": "No known failure pattern matched this text. Mock mode only recognises common Kubernetes and AWS failures.",
    "impact": "Unknown.",
    "suggested_actions": [
        "Add more context, such as the pod name, error message and time",
        "Switch to Claude mode for open-ended analysis",
    ],
    "commands": ["kubectl get events -n <ns> --sort-by=.lastTimestamp | tail -20"],
}


def mock_analyze(text: str) -> dict:
    lowered = text.lower()
    for rule in MOCK_RULES:
        if re.search(rule["match"], lowered):
            result = {k: v for k, v in rule.items() if k != "match"}
            result["confidence"] = "medium"
            return result
    return dict(FALLBACK, confidence="low")


# -------------------------------------------------------------- claude engine
def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("model did not return JSON")
    return json.loads(text[start:end + 1])


def _normalise(result: dict) -> dict:
    sev = str(result.get("severity", "info")).lower()
    return {
        "severity": sev if sev in SEVERITIES else "info",
        "title": str(result.get("title", "Untitled incident"))[:120],
        "root_cause": str(result.get("root_cause", "")),
        "impact": str(result.get("impact", "")),
        "suggested_actions": [str(a) for a in result.get("suggested_actions", [])][:5],
        "commands": [str(c) for c in result.get("commands", [])][:4],
        "confidence": str(result.get("confidence", "medium")).lower(),
    }


class Analyzer:
    def __init__(self, cfg):
        self.cfg = cfg
        self._client = None

    @property
    def mode(self) -> str:
        return self.cfg.effective_mode

    def _claude(self):
        if self._client is None:
            import anthropic  # imported lazily so mock mode needs no key
            self._client = anthropic.Anthropic(
                api_key=self.cfg.ANTHROPIC_API_KEY,
                timeout=self.cfg.LLM_TIMEOUT_SECONDS,
                max_retries=1,
            )
        return self._client

    def analyze(self, text: str, context: str = "") -> tuple:
        """Returns (analysis, meta). Never raises: a Claude failure falls back
        to the mock engine and is flagged in meta so the UI can show it."""
        text = text[: self.cfg.MAX_INPUT_CHARS]
        meta = {"mode": self.mode, "input_tokens": 0, "output_tokens": 0,
                "cost_usd": 0.0, "instance": self.cfg.INSTANCE, "error": None}
        t0 = time.perf_counter()

        if self.mode == "claude":
            try:
                prompt = f"Alert / log:\n{text}"
                if context:
                    prompt += f"\n\nCluster context:\n{context[:3000]}"
                resp = self._claude().messages.create(
                    model=self.cfg.CLAUDE_MODEL,
                    max_tokens=self.cfg.MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
                analysis = _normalise(_extract_json(raw))
                usage = resp.usage
                meta["input_tokens"] = usage.input_tokens
                meta["output_tokens"] = usage.output_tokens
                meta["cost_usd"] = round(
                    usage.input_tokens / 1e6 * self.cfg.PRICE_INPUT_PER_MTOK
                    + usage.output_tokens / 1e6 * self.cfg.PRICE_OUTPUT_PER_MTOK, 6)
                metrics.LLM_TOKENS.labels("input").inc(usage.input_tokens)
                metrics.LLM_TOKENS.labels("output").inc(usage.output_tokens)
                metrics.LLM_COST.inc(meta["cost_usd"])
                status = "ok"
            except Exception as exc:  # network, auth, bad JSON...
                analysis = mock_analyze(text)
                meta["error"] = f"Claude call failed ({type(exc).__name__}); showing rule-based result."
                meta["mode"] = "mock"
                status = "error"
        else:
            analysis = mock_analyze(text)
            status = "ok"

        latency = time.perf_counter() - t0
        meta["latency_ms"] = round(latency * 1000)
        metrics.LLM_REQUESTS.labels(self.mode, status).inc()
        metrics.LLM_LATENCY.labels(self.mode).observe(latency)
        return analysis, meta
