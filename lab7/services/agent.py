import hashlib
import base64
import json
import os
import re
import select
import subprocess
import tempfile
import time
from pathlib import Path

import yaml

from common import QuietHandler, json_response, post_json, read_json, serve


OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434/api/chat")
MODEL = os.getenv("MODEL", "qwen2.5:1.5b")
SKILL_FILE = os.getenv("SKILL_FILE", "/skills/kubernetes-drift.md")
GITHUB_TOKEN_FILE = os.getenv("GITHUB_TOKEN_FILE", "/run/secrets/github_token")
KUBERNETES_MCP_CONFIG = os.getenv("KUBERNETES_MCP_CONFIG", "/config/kubernetes-mcp.toml")

TOOLKIT = [
    {"server": "github", "tool": "get_file_contents", "input": "owner, repo, path, ref"},
    {"server": "github", "tool": "list_commits", "input": "owner, repo, path, sha, perPage"},
    {"server": "kubernetes", "tool": "resources_get", "input": "apiVersion, kind, name, namespace"},
    {"server": "kubernetes", "tool": "events_list", "input": "namespace, limit"},
]


class McpStdioClient:
    def __init__(self, command, server_name, environment=None):
        self.command = command
        self.server_name = server_name
        self.environment = environment
        self.process = None
        self.request_id = 0

    def __enter__(self):
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=self.environment,
        )
        response = self.request(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "kubernetes-drift-agent", "version": "1.0.0"},
            },
        )
        if "error" in response:
            raise RuntimeError(f"{self.server_name} initialize failed: {response['error']}")
        self.notify("notifications/initialized", {})
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def send(self, payload):
        self.process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def notify(self, method, params):
        self.send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method, params, timeout=30):
        self.request_id += 1
        request_id = self.request_id
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.process.stdout], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                break
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError(f"{self.server_name} MCP server exited unexpectedly")
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue
            if response.get("id") == request_id:
                return response
        raise TimeoutError(f"{self.server_name} MCP request {method} timed out")

    def call_tool(self, name, arguments):
        response = self.request("tools/call", {"name": name, "arguments": arguments}, timeout=45)
        if "error" in response:
            raise RuntimeError(f"{self.server_name}.{name}: {response['error']}")
        result = response["result"]
        if result.get("isError"):
            raise RuntimeError(f"{self.server_name}.{name}: {content_text(result)}")
        return content_text(result)


def content_text(result):
    resources = [
        item.get("resource", {}).get("text", "")
        for item in result.get("content", [])
        if item.get("type") == "resource" and item.get("resource", {}).get("text")
    ]
    if resources:
        return "\n".join(resources)
    text = "\n".join(item.get("text", "") for item in result.get("content", []) if item.get("type") == "text")
    if text:
        return text
    if result.get("structuredContent") is not None:
        return json.dumps(result["structuredContent"])
    return ""


def github_environment():
    with open(GITHUB_TOKEN_FILE, encoding="utf-8") as source:
        token = source.read().strip()
    if not token:
        raise RuntimeError("GitHub token secret is empty")
    environment = os.environ.copy()
    environment["GITHUB_PERSONAL_ACCESS_TOKEN"] = token
    environment["GITHUB_READ_ONLY"] = "1"
    environment["GITHUB_TOOLS"] = "get_file_contents,list_commits"
    return environment


def github_file_content(text):
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(payload, dict):
        content = payload.get("content")
        if isinstance(content, str):
            if payload.get("encoding") == "base64":
                return base64.b64decode(content).decode()
            return content
    return text


def extract_json(text):
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def parse_yaml_document(text):
    cleaned = re.sub(r"^```(?:yaml)?\s*|\s*```$", "", text.strip(), flags=re.S)
    start = cleaned.find("apiVersion:")
    if start >= 0:
        cleaned = cleaned[start:]
    document = yaml.safe_load(cleaned)
    if not isinstance(document, dict):
        raise ValueError("MCP response did not contain a Kubernetes object")
    return document


def read_github_chart(github, owner, repository, ref, root_path):
    directories = [root_path]
    files = {}
    calls = 0
    while directories:
        directory = directories.pop(0)
        listing = json.loads(
            github.call_tool(
                "get_file_contents",
                {"owner": owner, "repo": repository, "path": directory, "ref": ref},
            )
        )
        calls += 1
        if not isinstance(listing, list):
            raise ValueError(f"GitHub path {directory} is not a directory")
        for entry in listing:
            path = entry.get("path", "")
            if entry.get("type") == "dir":
                directories.append(path)
            elif entry.get("type") == "file":
                content = github.call_tool(
                    "get_file_contents",
                    {"owner": owner, "repo": repository, "path": path, "ref": ref},
                )
                calls += 1
                files[path.removeprefix(root_path + "/")] = github_file_content(content)
    return files, calls


def render_chart(files, release, namespace):
    with tempfile.TemporaryDirectory(prefix="drift-chart-") as temporary:
        chart_dir = Path(temporary) / "chart"
        chart_dir.mkdir()
        for relative, content in files.items():
            target = (chart_dir / relative).resolve()
            if chart_dir.resolve() not in target.parents:
                raise ValueError("unsafe path returned by GitHub MCP")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        result = subprocess.run(
            ["/usr/local/bin/helm", "template", release, str(chart_dir), "--namespace", namespace],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    resources = [item for item in yaml.safe_load_all(result.stdout) if isinstance(item, dict) and item.get("kind")]
    if not resources:
        raise ValueError("Helm chart rendered no Kubernetes resources")
    return resources


def comparable(resource):
    metadata = resource.get("metadata", {})
    result = {}
    if metadata.get("labels"):
        result["metadata"] = {"labels": metadata["labels"]}
    if metadata.get("annotations"):
        result.setdefault("metadata", {})["annotations"] = metadata["annotations"]
    if "spec" in resource:
        result["spec"] = resource["spec"]
    if "data" in resource:
        result["data"] = resource["data"]
    if "stringData" in resource:
        result["stringData"] = resource["stringData"]
    return result


def compare_declared(desired, live, path=""):
    differences = []
    if isinstance(desired, dict):
        live_dict = live if isinstance(live, dict) else {}
        for key, value in desired.items():
            child = f"{path}.{key}" if path else key
            differences.extend(compare_declared(value, live_dict.get(key), child))
    elif isinstance(desired, list):
        live_list = live if isinstance(live, list) else []
        named = all(isinstance(item, dict) and item.get("name") for item in desired)
        if named:
            live_by_name = {
                item.get("name"): item for item in live_list if isinstance(item, dict) and item.get("name")
            }
            for value in desired:
                name = value["name"]
                differences.extend(compare_declared(value, live_by_name.get(name), f"{path}[name={name}]"))
        else:
            for index, value in enumerate(desired):
                child = f"{path}[{index}]"
                differences.extend(compare_declared(value, live_list[index] if index < len(live_list) else None, child))
    elif desired != live:
        differences.append((path, desired, live))
    return differences


def severity_for(kind, field, missing=False):
    if missing:
        return "high"
    if kind == "Deployment" and field.endswith(".image"):
        return "high"
    if kind == "Service" and (field.startswith("spec.ports") or field.startswith("spec.selector")):
        return "high"
    if kind == "Deployment" and ".env[" in field:
        env_name = re.search(r"\.env\[name=([^\]]+)\]", field)
        if env_name and re.search(r"password|passwd|secret|token|api[_-]?key|credential|private[_-]?key", env_name.group(1), re.I):
            return "high"
        return "pending_ai"
    if kind == "Deployment" and field == "spec.replicas":
        return "medium"
    if field.startswith("metadata"):
        return "low"
    return "medium"


def apply_ai_env_severity(draft, drift):
    pending = {
        f"e{index}": item
        for index, item in enumerate((entry for entry in drift if entry["severity"] == "pending_ai"), 1)
    }
    if not pending:
        return True
    mapping = draft.get("env_severity") if isinstance(draft, dict) else None
    valid = (
        isinstance(mapping, dict)
        and set(mapping) == set(pending)
        and all(value in {"high", "medium", "low"} for value in mapping.values())
    )
    for item_id, item in pending.items():
        item["severity"] = mapping[item_id] if valid else "high"
    return valid


def analyze_resources(desired_resources, live_resources):
    drift = []
    desired_evidence = {}
    live_evidence = {}
    for desired in desired_resources:
        kind = desired["kind"]
        name = desired.get("metadata", {}).get("name")
        identity = f"{kind}/{name}"
        desired_part = comparable(desired)
        desired_evidence[identity] = desired_part
        live = live_resources.get(identity)
        if live is None:
            severity = severity_for(kind, "resource", missing=True)
            drift.append(
                {"resource": identity, "field": "resource", "desired": "present", "live": "missing", "severity": severity}
            )
            live_evidence[identity] = None
            continue
        live_part = comparable(live)
        live_evidence[identity] = live_part
        for field, desired_value, live_value in compare_declared(desired_part, live_part):
            severity = severity_for(kind, field)
            drift.append(
                {"resource": identity, "field": field, "desired": desired_value, "live": live_value, "severity": severity}
            )
    return desired_evidence, live_evidence, drift


def safe_ai_assessment(draft, fallback_actions, drift, env_severity_ok):
    if not drift:
        return {
            "likely_cause": "No configuration drift was detected, so no cause analysis is required.",
            "impact": "The declared Helm configuration and all audited live resources are in sync.",
            "actions": fallback_actions,
            "accepted_model_fields": [],
            "guardrail_fallback_fields": ["cause_category", "priority_order"],
        }
    facts = {f"d{index}": item for index, item in enumerate(drift, 1)}
    allowed_categories = {"out_of_band_change", "rollout_issue", "unknown"}
    category = draft.get("cause_category") if isinstance(draft, dict) else None
    priority = draft.get("priority_order") if isinstance(draft, dict) else None
    category_ok = category in allowed_categories
    priority_ok = (
        isinstance(priority, list)
        and len(priority) == len(facts)
        and all(isinstance(item, str) for item in priority)
        and set(priority) == set(facts)
    )
    if priority_ok:
        severity_rank = {"high": 3, "medium": 2, "low": 1}
        priority_ranks = [severity_rank[facts[item]["severity"]] for item in priority]
        priority_ok = priority_ranks == sorted(priority_ranks, reverse=True)
    if not category_ok:
        category = "unknown"
    if not priority_ok:
        severity_rank = {"high": 3, "medium": 2, "low": 1}
        priority = sorted(facts, key=lambda item: severity_rank[facts[item]["severity"]], reverse=True)

    causes = {
        "out_of_band_change": "The AI classified the evidence as a likely out-of-band change made after the reviewed Helm revision.",
        "rollout_issue": "The AI classified the evidence as a possible incomplete or inconsistent rollout.",
        "unknown": "The available evidence is not sufficient to identify a cause; an out-of-band change remains possible.",
    }
    ordered = [facts[item] for item in priority]
    priority_labels = [f"{item['resource']}:{item['field']}" for item in ordered]
    priority_source = "AI" if priority_ok else "deterministic policy"
    impact = (
        f"The {priority_source} priority order is " + " → ".join(priority_labels) + ". "
        "These verified configuration differences can affect workload behaviour or availability until Helm reconciliation."
    )
    top = ordered[0]
    actions = [
        f"Review authorization and operational impact of {top['resource']} field {top['field']} first.",
        "Run helm upgrade with the committed chart to restore every declared resource.",
        "Run the drift workflow again and verify an IN_SYNC result.",
    ]
    return {
        "likely_cause": causes[category],
        "impact": impact,
        "actions": actions if priority_ok else fallback_actions,
        "accepted_model_fields": [
            name
            for name, accepted in (("env_severity", env_severity_ok), ("cause_category", category_ok), ("priority_order", priority_ok))
            if accepted
        ],
        "guardrail_fallback_fields": [
            name
            for name, accepted in (("env_severity", env_severity_ok), ("cause_category", category_ok), ("priority_order", priority_ok))
            if not accepted
        ],
    }


def validate_request(payload):
    kubernetes_name = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
    namespace = str(payload.get("namespace", ""))
    release = str(payload.get("release", ""))
    if not namespace or len(namespace) > 63 or not kubernetes_name.fullmatch(namespace):
        raise ValueError("namespace is not a valid Kubernetes name")
    if not release or len(release) > 53 or not kubernetes_name.fullmatch(release):
        raise ValueError("release is not a valid Helm release name")

    repository_value = str(payload.get("repository", ""))
    repository_match = re.fullmatch(r"([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/([A-Za-z0-9._-]+)", repository_value)
    if not repository_match:
        raise ValueError("repository must use owner/name format")
    owner, repository = repository_match.groups()

    ref = str(payload.get("ref", ""))
    if not ref or len(ref) > 255 or not re.fullmatch(r"[A-Za-z0-9._/-]+", ref) or ".." in ref:
        raise ValueError("ref is not a safe Git reference")
    chart_path = str(payload.get("chart_path", "")).strip("/")
    path_parts = chart_path.split("/")
    if not chart_path or any(part in ("", ".", "..") for part in path_parts):
        raise ValueError("chart_path must be a relative repository path")
    return namespace, release, owner, repository, ref, chart_path


def run_agent(namespace, release, owner, repository, ref, chart_path):
    started = time.monotonic()
    with open(SKILL_FILE, encoding="utf-8") as source:
        skill = source.read()

    trace = []
    with McpStdioClient(
        [
            "/usr/local/bin/github-mcp-server",
            "stdio",
            "--read-only",
            "--tools=get_file_contents,list_commits",
        ],
        "github",
        github_environment(),
    ) as github:
        chart_files, github_calls = read_github_chart(github, owner, repository, ref, chart_path)
        trace.append(
            {"step": 1, "tool": "get_file_contents", "mcp_server": "github-mcp-server", "status": "ok", "calls": github_calls, "result": f"{len(chart_files)} chart files"}
        )
        github_history = github.call_tool(
            "list_commits",
            {
                "owner": owner,
                "repo": repository,
                "path": chart_path,
                "sha": ref,
                "perPage": 3,
            },
        )
        trace.append({"step": 2, "tool": "list_commits", "mcp_server": "github-mcp-server", "status": "ok"})

    desired_resources = render_chart(chart_files, release, namespace)
    live_resources = {}
    with McpStdioClient(
        ["/usr/local/bin/kubernetes-mcp-server", "--config", KUBERNETES_MCP_CONFIG], "kubernetes"
    ) as kubernetes:
        for desired_resource in desired_resources:
            kind = desired_resource["kind"]
            name = desired_resource.get("metadata", {}).get("name")
            identity = f"{kind}/{name}"
            try:
                live_text = kubernetes.call_tool(
                    "resources_get",
                    {"apiVersion": desired_resource["apiVersion"], "kind": kind, "name": name, "namespace": namespace},
                )
                live_resources[identity] = parse_yaml_document(live_text)
            except RuntimeError as error:
                if "not found" not in str(error).lower():
                    raise
                live_resources[identity] = None
        trace.append(
            {"step": 3, "tool": "resources_get", "mcp_server": "kubernetes-mcp-server", "status": "ok", "calls": len(desired_resources), "resources": [f"{item['kind']}/{item['metadata']['name']}" for item in desired_resources]}
        )
        namespace_events = kubernetes.call_tool("events_list", {"namespace": namespace, "limit": 20})
        trace.append({"step": 4, "tool": "events_list", "mcp_server": "kubernetes-mcp-server", "status": "ok"})

    desired, live, drift = analyze_resources(desired_resources, live_resources)
    verdict = "DRIFT_DETECTED" if drift else "IN_SYNC"
    drifted_resources = {item["resource"] for item in drift}
    resource_status = {
        identity: "DRIFT_DETECTED" if identity in drifted_resources else "IN_SYNC" for identity in desired
    }

    ai_facts = []
    env_facts = []
    for index, item in enumerate(drift, 1):
        fact = {"id": f"d{index}", "resource": item["resource"], "field": item["field"], "severity": item["severity"]}
        ai_facts.append(fact)
        if item["severity"] == "pending_ai":
            env_name = re.search(r"\.env\[name=([^\]]+)\]", item["field"])
            env_facts.append(
                {
                    "env_id": f"e{len(env_facts) + 1}",
                    "drift_id": f"d{index}",
                    "name": env_name.group(1) if env_name else "unknown",
                    "desired": item["desired"],
                    "live": item["live"],
                }
            )
    prompt = f"""You are a Kubernetes configuration drift analyst.
Follow this skill exactly:
---
{skill}
---
Namespace: {namespace}
Helm release: {release}
Rendered resources: {json.dumps(list(desired), ensure_ascii=False)}
Verified resource status: {json.dumps(resource_status, ensure_ascii=False)}
Verified deterministic drift facts: {json.dumps(ai_facts, ensure_ascii=False)}
Non-sensitive environment drift facts to classify: {json.dumps(env_facts, ensure_ascii=False)}
Recent GitHub history: {github_history[:3500]}
Recent Kubernetes events: {namespace_events[:3500]}

Return ONLY JSON with:
- env_severity: an object mapping every supplied env_id to exactly "high", "medium", or "low"; for example, one low env fact must be returned as {{"e1":"low"}}; return an empty object if there are no env facts;
- cause_category: exactly one of "out_of_band_change", "rollout_issue", or "unknown";
- priority_order: every supplied drift ID exactly once, ordered from most urgent to least urgent.
Classify env drift as high when it can break startup, connectivity, security, or data access; medium when it changes application behaviour or performance; low when it only affects diagnostics, verbosity, or cosmetic behaviour.
Do not return prose, commands, resource names, new facts, or any other keys.
"""
    llm = post_json(
        OLLAMA_URL,
        {"model": MODEL, "stream": False, "format": "json", "options": {"temperature": 0}, "messages": [{"role": "user", "content": prompt}]},
        timeout=120,
    )
    raw = llm["message"]["content"]
    try:
        draft = extract_json(raw)
    except (ValueError, json.JSONDecodeError):
        draft = {}

    env_severity_ok = apply_ai_env_severity(draft, drift)

    if drift:
        fields = ", ".join(f"{item['resource']}:{item['field']}" for item in drift)
        summary = (
            f"The agent audited {len(desired_resources)} resources rendered from the committed Helm chart and found {len(drift)} differences: "
            f"{fields}. Each difference is classified as high, medium, or low severity."
        )
        fallback_actions = [
            "Review whether the live changes were authorized.",
            "Run helm upgrade with the committed chart to restore the declared state.",
            "Run the drift workflow again and verify an IN_SYNC result.",
        ]
    else:
        summary = f"All {len(desired_resources)} resources rendered from the committed Helm chart match their declared fields in Kubernetes. No reconciliation is required."
        fallback_actions = [
            "Keep the Helm chart as the source of truth.",
            "Continue scheduled drift checks.",
            "Review future manual cluster changes through Git.",
        ]
    ai_assessment = safe_ai_assessment(draft, fallback_actions, drift, env_severity_ok)

    duration = round(time.monotonic() - started, 3)
    return {
        "namespace": namespace,
        "release": release,
        "repository": f"{owner}/{repository}",
        "ref": ref,
        "chart_path": chart_path,
        "audited_resources": [f"{item['kind']}/{item['metadata']['name']}" for item in desired_resources],
        "agent": {
            "model": MODEL,
            "skill": "kubernetes-drift-audit",
            "llm_invoked": True,
            "llm_draft_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "llm_draft": draft,
            "guardrail": "MCP evidence and deterministic comparison override model-generated facts",
            "accepted_model_fields": ai_assessment["accepted_model_fields"],
            "guardrail_fallback_fields": ai_assessment["guardrail_fallback_fields"],
            "tool_calls": trace,
            "duration_seconds": duration,
        },
        "evidence": {
            "desired_from_github": desired,
            "live_from_kubernetes": live,
            "github_repository": f"{owner}/{repository}@{ref}",
            "github_history": github_history,
            "namespace_events": namespace_events,
        },
        "analysis": {
            "verdict": verdict,
            "drift_count": len(drift),
            "drift": drift,
            "summary": summary,
            "likely_cause": ai_assessment["likely_cause"],
            "impact": ai_assessment["impact"],
            "actions": ai_assessment["actions"],
        },
        "metrics": {
            "status": "success",
            "agent_duration_seconds": duration,
            "drift_by_severity": {
                severity: sum(1 for item in drift if item["severity"] == severity)
                for severity in ("high", "medium", "low")
            },
            "resources_out_of_sync": len({item["resource"] for item in drift}),
        },
    }


class Handler(QuietHandler):
    def do_GET(self):
        if self.health("kubernetes-drift-agent"):
            return
        if self.path == "/toolkit":
            json_response(self, 200, {"tools": TOOLKIT})
            return
        json_response(self, 404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/analyze":
            json_response(self, 404, {"error": "not found"})
            return
        try:
            payload = read_json(self)
            namespace, release, owner, repository, ref, chart_path = validate_request(payload)
            json_response(self, 200, run_agent(namespace, release, owner, repository, ref, chart_path))
        except Exception as error:
            json_response(self, 500, {"error": type(error).__name__, "message": str(error)})


if __name__ == "__main__":
    serve(Handler)
