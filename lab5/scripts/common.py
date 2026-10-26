"""Shared CLI helpers. Never print credentials or Kubernetes Secret objects."""
import json
import os
from pathlib import Path
import subprocess
import shutil

ROOT = Path(__file__).resolve().parents[1]
_local_helm=ROOT / ".tools" / "windows-amd64" / "helm.exe"
HELM = str(_local_helm) if os.name == "nt" and _local_helm.exists() else (shutil.which("helm") or "helm")


def run(*args, input=None, check=True, timeout=120):
    result = subprocess.run(list(map(str, args)), input=input, capture_output=True,
                            text=True, encoding="utf-8", timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f"{args[0]} {args[1:4]}: {result.stderr or result.stdout}")
    return result


def kube(*args, **kwargs):
    return run("kubectl", *args, **kwargs)


def save(name, value):
    path = ROOT / "artifacts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
