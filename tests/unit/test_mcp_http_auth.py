"""HTTP transport: bearer authentication and per-tool scope enforcement."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from memosyne import auth, mcp_server


def _call(tool: str, request_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
            "params": {"name": tool, "arguments": {}}}


class TestScopeEnforcement(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(
            os.environ, {"MEMOSYNE_AUTH_DB": str(Path(self._tmp.name) / "tokens.sqlite")})
        self._env.start()
        self.tokens = {s: auth.create(f"{s}-token", s) for s in ("read", "write", "admin")}

        async def echo(request):   # stands in for the MCP app; proves the body is replayed
            return JSONResponse({"received": json.loads(await request.body() or b"null")})

        app = Starlette(routes=[Route("/mcp", echo, methods=["GET", "POST"])])
        app.add_middleware(mcp_server.BearerScopeMiddleware)
        self.client = TestClient(app)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def post(self, payload, scope: str | None = "read"):
        headers = {"Authorization": f"Bearer {self.tokens[scope]}"} if scope else {}
        return self.client.post("/mcp", json=payload, headers=headers)

    def test_requests_without_a_valid_token_are_rejected(self):
        self.assertEqual(self.post(_call("search_memory"), scope=None).status_code, 401)
        bad = self.client.post("/mcp", json=_call("search_memory"),
                               headers={"Authorization": "Bearer not-a-token"})
        self.assertEqual(bad.status_code, 401)
        self.assertEqual(self.client.get("/mcp").status_code, 401)

    def test_revoked_tokens_stop_working(self):
        auth.revoke("read-token")
        self.assertEqual(self.post(_call("search_memory")).status_code, 401)

    def test_read_token_can_call_read_tools_and_the_body_reaches_the_app(self):
        payload = _call("search_memory")
        resp = self.post(payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"received": payload})

    def test_read_token_cannot_call_write_tools(self):
        resp = self.post(_call("muse_answer"))
        self.assertEqual(resp.status_code, 403)
        self.assertIn("muse_answer", resp.json()["error"])
        self.assertEqual(self.post(_call("aletheia_add_fact")).status_code, 403)

    def test_write_token_can_write_but_not_administer(self):
        self.assertEqual(self.post(_call("muse_answer"), scope="write").status_code, 200)
        self.assertEqual(self.post(_call("aletheia_revert"), scope="write").status_code, 403)
        self.assertEqual(self.post(_call("aletheia_revert"), scope="admin").status_code, 200)

    def test_unlisted_tools_fail_closed(self):
        self.assertEqual(self.post(_call("brand_new_tool"), scope="write").status_code, 403)
        self.assertEqual(self.post(_call("brand_new_tool"), scope="admin").status_code, 200)

    def test_a_batch_is_rejected_if_any_call_is_not_allowed(self):
        batch = [_call("search_memory", 1), _call("muse_answer", 2)]
        self.assertEqual(self.post(batch).status_code, 403)
        self.assertEqual(self.post(batch, scope="write").status_code, 200)

    def test_other_methods_and_non_json_bodies_pass_through(self):
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        self.assertEqual(self.post(init).status_code, 200)
        self.assertEqual(self.post({"jsonrpc": "2.0", "method": "tools/list"}).status_code, 200)
        get = self.client.get("/mcp", headers={"Authorization": f"Bearer {self.tokens['read']}"})
        self.assertEqual(get.status_code, 200)


class TestScopeTable(unittest.TestCase):
    def test_every_registered_tool_has_a_scope(self):
        registered = set(mcp_server.mcp._tool_manager._tools)
        self.assertTrue(registered)
        self.assertEqual(registered - set(mcp_server.TOOL_SCOPES), set())

    def test_write_tools_are_not_readable(self):
        for tool in ("muse_answer", "optimize_memory", "aletheia_add_fact", "aletheia_correct_text"):
            self.assertEqual(mcp_server.required_scope(tool), "write")
        self.assertEqual(mcp_server.required_scope("aletheia_revert"), "admin")
        self.assertEqual(mcp_server.required_scope("get_digest"), "read")


if __name__ == "__main__":
    unittest.main()
