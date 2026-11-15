"""Keep every test away from real data.

Runs before any test module is imported: Memosyne is pointed at an empty
temporary home, so module-level paths (vault, artifacts, spring, .env) can
never resolve to a developer's real vault or pick up real API keys.
"""

import os
import tempfile

_HOME = tempfile.mkdtemp(prefix="memosyne-test-home-")
os.environ["MEMOSYNE_HOME"] = _HOME
for _var in (
    "MEMOSYNE_VAULT_DIR", "MEMOSYNE_ARTIFACT_DIR", "MEMOSYNE_SPRING_DIR",
    "MEMOSYNE_CAPTURE_QUERIES", "MEMOSYNE_SEARCH_PROFILE", "MEMOSYNE_SEARCH_CHATS",
    "MEMOSYNE_INDEX_CHATS", "MEMOSYNE_MCP_TOOLS",
):
    os.environ.pop(_var, None)
