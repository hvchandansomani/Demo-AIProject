"""One-click sample alerts for the UI, written the way real tools phrase them."""

SAMPLES = [
    {
        "key": "oom",
        "label": "Pod out of memory",
        "text": "Pod aiops-triage-7d9c8b-x2kqp in namespace aiops restarted 4 times.\n"
                "Last State: Terminated, Reason: OOMKilled, Exit Code: 137\n"
                "Limits: memory 256Mi. Working set before kill: 255Mi.",
    },
    {
        "key": "crashloop",
        "label": "CrashLoopBackOff",
        "text": "Warning  BackOff  pod/aiops-triage-5f6b9-kq2lm  Back-off restarting failed container\n"
                "Status: CrashLoopBackOff\n"
                "Logs: KeyError: 'ANTHROPIC_API_KEY' at app/config.py line 21",
    },
    {
        "key": "5xx",
        "label": "5xx error spike",
        "text": "[FIRING] HighErrorRate namespace=aiops service=aiops-triage\n"
                "summary: 5xx error rate is 23% over the last 5 minutes (threshold 5%).\n"
                "Started 3 minutes after deploy of build #42.",
    },
    {
        "key": "cpu",
        "label": "CPU spike",
        "text": "[FIRING] HighCPU pod=aiops-triage-7d9c8b-x2kqp namespace=aiops\n"
                "CPU usage at 96% of limit (500m) for 5 minutes. HPA at 3/3 replicas.",
    },
    {
        "key": "imagepull",
        "label": "Image pull failed",
        "text": "Failed to pull image \"123456789012.dkr.ecr.ap-south-1.amazonaws.com/aiops-triage:43\": "
                "manifest unknown. Status: ImagePullBackOff",
    },
    {
        "key": "disk",
        "label": "Disk almost full",
        "text": "CloudWatch ALARM k3s-node-disk: disk_used_percent = 93.4 on i-0abc123 "
                "(threshold 85). Node condition DiskPressure=True.",
    },
]
