"""The OpenAI-compatible LLM path, end to end against a local fake server.

Enrichment is the only core step that calls an LLM. This keeps it honest
across `openai` SDK major versions without ever touching a real provider.
"""

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from memosyne import llm_client


class _FakeChatCompletions(BaseHTTPRequestHandler):
    seen: list[dict] = []

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).seen.append({"path": self.path, "auth": self.headers.get("authorization"),
                                "body": body})
        payload = json.dumps({
            "id": "cmpl-test", "object": "chat.completion", "created": 0,
            "model": body.get("model", "m"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant",
                                     "content": "pong:" + body["messages"][-1]["content"]}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # keep test output clean
        pass


class TestOpenAICompatiblePath(unittest.TestCase):
    def setUp(self):
        _FakeChatCompletions.seen = []
        self.server = HTTPServer(("127.0.0.1", 0), _FakeChatCompletions)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        port = self.server.server_address[1]
        self._env = mock.patch.dict(os.environ, {
            "PROXY_BASE_URL": f"http://127.0.0.1:{port}/v1",
            "PROXY_API_KEY": "test-key",
            "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
        })
        self._env.start()
        # the client and key are cached at module level; start each test clean
        self._caches = [mock.patch.object(llm_client, name, None, create=True)
                        for name in ("_proxy_client", "_proxy_key_cache")]
        for c in self._caches:
            c.start()

    def tearDown(self):
        for c in self._caches:
            c.stop()
        self._env.stop()
        self.server.shutdown()
        self.server.server_close()

    def test_chat_text_round_trip(self):
        reply = llm_client.chat_text("proxy:test-model", [{"role": "user", "content": "ping"}])
        self.assertEqual(reply, "pong:ping")
        (request,) = _FakeChatCompletions.seen
        self.assertTrue(request["path"].endswith("/chat/completions"))
        self.assertEqual(request["auth"], "Bearer test-key")
        self.assertEqual(request["body"]["model"], "test-model")

    def test_provider_is_resolved_from_the_model_prefix(self):
        self.assertEqual(llm_client.active_provider("proxy:anything"), "proxy")


if __name__ == "__main__":
    unittest.main()
