# Skill: Kubernetes drift audit

## Purpose

Compare every resource rendered by a Helm chart declared in GitHub with the
corresponding live Kubernetes resources and produce an evidence-based report.

## Required order

1. Recursively read the complete chart from the requested GitHub repository, ref, and chart path.
2. Read recent GitHub commit history to identify the source revision.
3. Render the committed chart and enumerate every Kubernetes resource it produces.
4. Get every rendered resource from the requested namespace.
5. Read recent namespace events and verify the live objects carry Helm labels.
6. Compare every field declared by the chart while ignoring API defaults, status, and other server-added fields.
7. Assign severity using the deterministic policy below.
8. Use commit history, events, and verified differences to classify the likely cause and prioritize only verified drift IDs.
9. Turn the validated classification and priority order into a safe Helm reconciliation plan.

## Policy

- Image drift is high severity.
- Service port or selector drift is high severity.
- A missing rendered resource is high severity.
- Replica and other declared spec drift is medium severity.
- Non-sensitive environment-variable drift is classified by the AI as high, medium, or low using only verified name/value differences.
- Environment variables with names that look sensitive are always high severity and their values are not sent to the model.
- Invalid AI severity output falls back to high severity.
- Metadata drift is low severity.
- Any difference produces `DRIFT_DETECTED`; no differences produce `IN_SYNC`.

## Safety rules

- Never mutate Kubernetes resources from the agent.
- Never read Secrets.
- Never present an inferred cause as a confirmed fact.
- Never use a factual difference that was not returned by an MCP tool and verified by deterministic comparison.
- Never recommend fixing drift with an ad-hoc `kubectl patch`.
- Git and Helm are the source of truth; reconciliation must use `helm upgrade`.
- If an MCP call fails, fail the audit instead of guessing.
