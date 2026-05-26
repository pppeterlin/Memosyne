# Memosyne MCP Setup

Memosyne exposes the private memory vault to MCP-compatible clients such as Claude Desktop and Cursor. v0.3 keeps MCP setup explicit and checkable.

## Prerequisites

```bash
source .venv/bin/activate
pip install -r Personal_Brain_DB/00_System/requirements.txt
pip install -e .
memosyne health
memosyne mcp --check
```

`memosyne mcp --check` imports the MCP server without starting it. Use this before editing client config.

## Generate Client Config

Print a config snippet using the current Python interpreter and repo path:

```bash
memosyne mcp --print-config
```

Optional custom server name:

```bash
memosyne mcp --print-config --name memosyne
```

Output shape:

```json
{
  "mcpServers": {
    "memosyne": {
      "command": "/path/to/venv/bin/python",
      "args": [
        "/path/to/memosyne/Personal_Brain_DB/00_System/mcp_server.py"
      ]
    }
  }
}
```

Paste that object into your MCP client configuration. The exact config file location depends on the client.

## Start Manually

For debugging:

```bash
memosyne mcp
```

Most users should let the MCP client start the server from the generated config.

## Tools

| Tool | Purpose | Writes? |
|---|---|---|
| `search_memory` | Hybrid memory search with ACT-R reranking | no |
| `read_file` | Read a vault file by relative path | records Chronicle access |
| `get_profile` | Read profile sections | no |
| `list_journals` | List journal entries | no |
| `get_memory_health` | Chronicle stats and most active memories | no |
| `optimize_memory` | Run Slumber maintenance actions | yes for non-`stats` actions |
| `query_memory_at_time` | Search with a temporal anchor | records search access |
| `get_entity_timeline` | Inspect Tapestry entity timeline | no |
| `aletheia_add_fact` | Add a personal fact | dry-run by default |
| `aletheia_update_fact` | Update a personal fact | dry-run by default |
| `aletheia_invalidate_fact` | Remove a personal fact | dry-run by default |
| `aletheia_correct_text` | Literal body text correction | dry-run by default |
| `aletheia_revert` | Revert an Aletheia operation | dry-run by default |
| `memosyne_guide` | Tool-selection guide for agents | no |

Correction tools require `apply=True` before writing. This is intentional: MCP writes must not be silent.

## Recommended Agent Behavior

- Prefer `search_memory` for recall questions.
- Use `return_parent=True` when snippets are too narrow.
- Use `memosyne_guide` only when tool choice is unclear.
- For any correction, call Aletheia once with `apply=False`, inspect the result, then call again with `apply=True` only after confirmation.
- After corrections that affect retrieval, run or request a rebuild.

## Troubleshooting

### Import Fails

```text
[fail] MCP server import failed: No module named 'mcp'
```

Fix:

```bash
pip install -r Personal_Brain_DB/00_System/requirements.txt
memosyne mcp --check
```

### Wrong Python

If the MCP client starts a different interpreter than your terminal, regenerate the config while the project virtualenv is active:

```bash
source .venv/bin/activate
memosyne mcp --print-config
```

### Search Returns Nothing

Check artifacts:

```bash
memosyne health
memosyne chronicle --stats
```

If retrieval indexes are missing:

```bash
memosyne rebuild
```

## HTTP Transport — The Open Threshold

stdio is the default transport — it's already authenticated by being on
the same machine as the user, and is what Claude Desktop / Cursor /
Claude Code use. For agents that can't speak stdio (cloud agents,
custom MCP clients, browser extensions), Memosyne v0.5 ships an HTTP
transport with bearer-token auth.

> **Warning.** HTTP transport binds to `127.0.0.1` by default and is
> intended for local-only use (loopback, Tailscale, ngrok with auth).
> Never expose the HTTP port on a public interface without an
> additional reverse-proxy auth layer — bearer tokens alone are not a
> substitute for TLS + IP allowlisting. OAuth 2.1 is planned for v0.6.

### Mint a token

```bash
memosyne auth create cursor-laptop --scope read
# label:  cursor-laptop
# scope:  read
# token:  <copy this immediately — shown once>
```

Scopes ladder from least to most privileged:

| Scope | Allowed tools |
|---|---|
| `read`  | `search_memory`, `get_profile`, `list_journals`, `read_file`, `get_entity_timeline`, `query_memory_at_time`, `get_memory_health`, `invocation_protocol` |
| `write` | + `optimize_memory`, all non-revert `aletheia_*` operations |
| `admin` | + `aletheia_revert` — **also marked `local_only` and hidden on HTTP** |

`local_only` tools are stripped from the HTTP-exposed tool set
regardless of scope; admin-scope tokens can only invoke them via
stdio.

### Start the server

```bash
memosyne mcp --http              # 127.0.0.1:8000
memosyne mcp --http --port 7777  # custom port
```

The banner reports which tools were hidden and where to find the auth
log. Tokens live at `~/.memosyne/tokens.sqlite` (override with
`MEMOSYNE_AUTH_DB`).

### Manage tokens

```bash
memosyne auth list                  # show all (active + revoked)
memosyne auth revoke cursor-laptop  # revoke by label
```

Tokens are stored only as SHA-256 hashes. Lookups touch `last_used`
so you can audit which credentials are actually being used.

### Reaching the server from a client

Most MCP HTTP clients accept a URL + headers map:

```
url:     http://127.0.0.1:8000/mcp
headers: { "Authorization": "Bearer <token>" }
```

For Tailscale / Mesh VPN scenarios, point the URL at the Tailscale
hostname instead of localhost; the token check still applies. Do not
disable the DNS rebinding protection in `FastMCP.settings` without
understanding what it blocks.

## Choosing a transport

| Scenario | Transport |
|---|---|
| Claude Desktop, Cursor, Claude Code on the same machine | stdio (default) |
| Custom local agent process | stdio |
| Remote agent over Tailscale / SSH tunnel | HTTP + bearer |
| Cloud agent (OpenAI, Perplexity, etc.) | HTTP behind your own auth proxy |
| Public internet exposure | wait for OAuth 2.1 (v0.6) |

---

### Local LLM Calls Fail

A local LLM endpoint is only required for enrichment, contextualization, HyQE, and local chat. Memosyne does not bundle a runtime — start whichever local LLM server you use (Ollama, llama.cpp, LM Studio, vLLM, …) so the configured endpoint is reachable, then verify:

```bash
memosyne health
```
