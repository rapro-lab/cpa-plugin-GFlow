"""Refresh failure must not silently disable a healthy account."""
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python/flow2api"))
from src.services.token_manager import TokenManager


class RefreshStateTests(unittest.IsolatedAsyncioTestCase):
    async def run_failure(self, reason):
        token = SimpleNamespace(id=1, st="fake", google_cookies="fake",
                                last_st_refresh_result="RuntimeError: account browser tab unavailable")
        manager = TokenManager.__new__(TokenManager)
        manager._refresh_locks = {}
        manager._refresh_lock_guard = asyncio.Lock()
        manager._get_token_lock = AsyncMock(return_value=asyncio.Lock())
        manager.db = SimpleNamespace(get_token=AsyncMock(return_value=token),
                                     update_token=AsyncMock())
        manager._do_refresh_at = AsyncMock(return_value=False)
        manager._try_refresh_st = AsyncMock(return_value=None)
        manager._last_at_refresh_error = {1: reason}
        manager._clear_at_validation_cache = Mock()
        manager.flow_client = SimpleNamespace()
        self.assertFalse(await manager._refresh_at_inner(1))
        return manager

    async def test_browser_failure_keeps_account_enabled(self):
        manager = await self.run_failure("Flow frontend RPC rejected: PUBLIC_ERROR_UNUSUAL_ACTIVITY")
        manager.db.update_token.assert_not_awaited()

    async def test_expired_session_records_precise_reason(self):
        manager = await self.run_failure("FlowBootstrapError: Flow login authentication failed: Google Cookie expired")
        change = manager.db.update_token.await_args.kwargs
        self.assertFalse(change["is_active"])
        self.assertEqual(change["ban_reason"], "session_expired")
