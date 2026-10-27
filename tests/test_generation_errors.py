"""Failure propagation tests: no accounts, browser, network or generation."""
import contextlib
import importlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from generation_errors import generation_error, account_pool_error


class ErrorTests(unittest.TestCase):
    def test_error_inside_sse_is_not_success(self):
        result = generation_error('data: ' + json.dumps({"error": {
            "message": "no usable account", "status_code": 503}}) + "\n\n")
        self.assertEqual(result["status_code"], 503)

    def test_normal_stream_and_done_are_not_errors(self):
        for text in ["data: [DONE]\n\n", 'data: {"choices": []}\n\n']:
            self.assertIsNone(generation_error(text))

    def test_upstream_rejection_is_actionable_and_not_login_expiry(self):
        error = generation_error({"error": {"message": "PUBLIC_ERROR_UNUSUAL_ACTIVITY"}})
        self.assertEqual(error["code"], "gflow_unusual_activity")
        self.assertEqual(error["status_code"], 403)
        self.assertIn("not retried", error["message"])

    def test_empty_disabled_and_expired_accounts_are_distinct(self):
        self.assertEqual(account_pool_error([])["code"], "gflow_empty_account_pool")
        self.assertEqual(account_pool_error([SimpleNamespace(ban_reason=None)])["code"], "gflow_accounts_disabled")
        self.assertEqual(account_pool_error([SimpleNamespace(ban_reason="session_expired")])["code"], "gflow_session_expired")


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_error_uses_native_error_envelope_without_success_done(self):
        # Importing the worker redirects its RPC stdout; keep test output intact.
        with contextlib.redirect_stdout(io.StringIO()):
            worker = importlib.import_module("worker")
        service = worker.GFlowService.__new__(worker.GFlowService)
        service.initialize = AsyncMock()
        service.token_manager = SimpleNamespace(get_active_tokens=AsyncMock(return_value=[object()]))
        service.tmp_dir = ROOT / "data/tmp"
        messages = []
        service.send_line = messages.append
        routes = ModuleType("src.api.routes")
        routes.ChatCompletionRequest = lambda **kw: SimpleNamespace(**kw)
        routes._normalize_openai_request = AsyncMock(return_value=SimpleNamespace(model='gemini-3.1-flash-image-landscape', images=[]))
        routes._collect_non_stream_result = AsyncMock()
        routes._parse_handler_result = json.loads
        async def stream(*args):
            yield 'data: {"error":{"message":"PUBLIC_ERROR_UNUSUAL_ACTIVITY","status_code":500}}\n\n'
            yield "data: [DONE]\n\n"
        routes._iterate_openai_stream = stream
        with patch.dict(sys.modules, {"src.api.routes": routes}):
            await service.handle_chat_completion("test", {"model": "gflow-nano-banana-2.1", "stream": True})
        self.assertEqual(len(messages), 1)
        self.assertTrue(messages[0]["done"])
        self.assertEqual(messages[0]["error"]["code"], "gflow_unusual_activity")
        self.assertNotIn("chunk", messages[0])


if __name__ == "__main__":
    unittest.main()
