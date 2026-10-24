# AI agent toolkit

The agent has four read-only tools from two ready-made MCP servers.

| Tool | MCP server | What it does | Input |
|---|---|---|---|
| `get_file_contents` | `github-mcp-server` | Recursively discovers and reads the committed Helm chart | `owner`, `repo`, `path`, `ref` |
| `list_commits` | `github-mcp-server` | Returns commits that changed the chart | `owner`, `repo`, `path`, `sha`, `perPage` |
| `resources_get` | `kubernetes-mcp-server` | Reads each live resource rendered by the chart | `apiVersion`, `kind`, `name`, `namespace` |
| `events_list` | `kubernetes-mcp-server` | Reads recent rollout and scheduling events | `namespace`, `limit` |

The Kubernetes MCP server is configured with `read_only = true`, destructive
operations disabled, and Secret resources denied. The official GitHub MCP
server runs with `--read-only` and exposes only the two tools listed above.
