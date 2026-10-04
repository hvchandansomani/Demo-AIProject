#!/usr/bin/env python3
"""Regenerates the Appendix of PRACTICE-TASK.md from the real files.

    python3 tools/build_practice_guide.py

Everything after the APPENDIX:START marker is replaced, so the copies in the
guide always match the repository. Edit the real files, never the appendix.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "PRACTICE-TASK.md"
MARKER = "<!-- APPENDIX:START"

# (section title, list of glob patterns), in the order the guide uses them
SECTIONS = [
    ("Docker and local run", ["docker-compose.yml", ".env.example",
                              "doubt-wall/Dockerfile", "aiops-triage/Dockerfile",
                              "doubt-wall/.dockerignore", ".gitattributes"]),
    ("Terraform: state bucket (bootstrap)", ["terraform/bootstrap/*.tf"]),
    ("Terraform: infrastructure", ["terraform/infra/versions.tf", "terraform/infra/backend.tf",
                                   "terraform/infra/backend.hcl.example",
                                   "terraform/infra/variables.tf", "terraform/infra/terraform.tfvars.example",
                                   "terraform/infra/network.tf", "terraform/infra/security.tf",
                                   "terraform/infra/iam.tf", "terraform/infra/ecr.tf",
                                   "terraform/infra/ec2.tf", "terraform/infra/budget.tf",
                                   "terraform/infra/inventory.tf", "terraform/infra/templates/*",
                                   "terraform/infra/outputs.tf"]),
    ("Ansible", ["ansible/ansible.cfg", "ansible/site.yml", "ansible/group_vars/all.yml",
                 "ansible/inventory/hosts.ini.example", "ansible/roles/*/defaults/main.yml",
                 "ansible/roles/*/tasks/main.yml", "ansible/roles/*/handlers/main.yml",
                 "ansible/roles/*/templates/*"]),
    ("Kubernetes manifests", ["k8s/create-secrets.sh", "k8s/base/kustomization.yaml",
                              "k8s/base/namespace.yaml", "k8s/base/ingress.yaml",
                              "k8s/base/postgres/*", "k8s/base/doubt-wall/*",
                              "k8s/base/aiops-triage/*", "k8s/overlays/*/kustomization.yaml"]),
    ("Jenkins pipeline", ["Jenkinsfile", "ci/*.sh"]),
    ("Argo CD", ["argocd/application.yaml"]),
]

LANG = {".tf": "hcl", ".hcl": "hcl", ".example": "", ".yml": "yaml", ".yaml": "yaml",
        ".sh": "bash", ".j2": "jinja", ".tftpl": "ini", ".cfg": "ini", ".ini": "ini",
        ".conf": "ini", ".py": "python"}
NAMED = {"Dockerfile": "dockerfile", "Jenkinsfile": "groovy", ".dockerignore": "",
         ".gitattributes": "", "docker-compose.yml": "yaml"}


def lang_for(p: Path) -> str:
    if p.name in NAMED:
        return NAMED[p.name]
    if p.name.endswith(".hcl.example"):
        return "hcl"
    if p.name.endswith(".tfvars.example"):
        return "hcl"
    if p.name.endswith(".ini.example"):
        return "ini"
    if p.name == ".env.example":
        return "bash"
    return LANG.get(p.suffix, "")


def fence_for(text: str) -> str:
    fence = "```"
    while fence in text:
        fence += "`"
    return fence


def main():
    guide = GUIDE.read_text()
    head, sep, _ = guide.partition(MARKER)
    if not sep:
        raise SystemExit(f"Marker {MARKER!r} not found in {GUIDE}")
    marker_line = MARKER + guide.split(MARKER, 1)[1].split("\n", 1)[0]

    out, index, seen, count = [], [], set(), 0
    for title, patterns in SECTIONS:
        files = []
        for pat in patterns:
            for p in sorted(ROOT.glob(pat)):
                if p.is_file() and p not in seen:
                    seen.add(p)
                    files.append(p)
        if not files:
            continue
        anchor = title.lower().replace(":", "").replace("(", "").replace(")", "").replace(" ", "-")
        index.append(f"- [{title}](#{anchor}) ({len(files)} files)")
        out.append(f"\n### {title}\n")
        for p in files:
            rel = p.relative_to(ROOT).as_posix()
            text = p.read_text().rstrip("\n")
            fence = fence_for(text)
            out.append(f"<details>\n<summary><code>{rel}</code></summary>\n\n"
                       f"{fence}{lang_for(p)}\n{text}\n{fence}\n\n</details>\n")
            count += 1

    appendix = "\n".join(index) + "\n" + "\n".join(out)
    GUIDE.write_text(head + marker_line + "\n\n" + appendix)
    print(f"Appendix rebuilt: {count} files in {len(index)} sections.")


if __name__ == "__main__":
    main()
