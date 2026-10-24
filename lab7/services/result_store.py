import json
import os
import re
from datetime import datetime, timezone

from common import QuietHandler, json_response, read_json, serve


REPORT_DIR = os.getenv("REPORT_DIR", "/reports")


def safe_name(value):
    return re.sub(r"[^a-zA-Z0-9._-]", "-", value)


def render_report(payload):
    analysis = payload["analysis"]
    agent = payload["agent"]
    drift_rows = "\n".join(
        f"| `{item['resource']}` | `{item['field']}` | `{item['desired']}` | `{item['live']}` | {item['severity']} |"
        for item in analysis["drift"]
    ) or "| — | — | — | — | no drift |"
    actions = "\n".join(f"{index}. {item}" for index, item in enumerate(analysis["actions"], 1))
    trace = "\n".join(
        f"- `{item['tool']}` via `{item['mcp_server']}` — {item['status']}" for item in agent["tool_calls"]
    )
    return f"""# Kubernetes drift report: {payload['release']}

- Namespace: `{payload['namespace']}`
- Audited resources: `{', '.join(payload['audited_resources'])}`
- Verdict: **{analysis['verdict']}**
- Generated at: {datetime.now(timezone.utc).isoformat()}
- AI model: `{agent['model']}`
- Skill: `{agent['skill']}`
- LLM invoked: `{str(agent['llm_invoked']).lower()}`
- AI draft SHA-256: `{agent['llm_draft_sha256']}`
- Guardrail: {agent['guardrail']}
- Accepted AI fields: `{', '.join(agent['accepted_model_fields']) or 'none'}`
- Guardrail fallback fields: `{', '.join(agent['guardrail_fallback_fields']) or 'none'}`

## Summary

{analysis['summary']}

### AI assessment

- Likely cause: {analysis['likely_cause']}
- Possible impact: {analysis['impact']}

## Detected drift

| Resource | Field | Desired in GitHub | Live in Kubernetes | Severity |
|---|---|---|---|---|
{drift_rows}

## Actions

{actions}

## Agent tool trace

{trace}

## Verified evidence

```json
{json.dumps(payload['evidence'], indent=2, ensure_ascii=False)}
```
"""


class Handler(QuietHandler):
    def do_GET(self):
        if not self.health("result-store"):
            json_response(self, 404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/reports":
            json_response(self, 404, {"error": "not found"})
            return
        payload = read_json(self)
        os.makedirs(REPORT_DIR, exist_ok=True)
        filename = f"{safe_name(payload['namespace'])}-{safe_name(payload['release'])}.md"
        path = os.path.join(REPORT_DIR, filename)
        with open(path, "w", encoding="utf-8") as report:
            report.write(render_report(payload))
        payload["report_path"] = f"reports/{filename}"
        json_response(self, 201, payload)


if __name__ == "__main__":
    serve(Handler)
