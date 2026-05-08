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

### Local LLM Calls Fail

Ollama is only required for enrichment, contextualization, HyQE, and local chat. Start it before those workflows:

```bash
ollama serve
memosyne health
```
