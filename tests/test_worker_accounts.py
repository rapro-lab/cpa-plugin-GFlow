"""查额与健康状态集成回归：模拟上游，不刷新会话或启动浏览器。"""
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python"), str(ROOT / "python/flow2api")]
# worker 运行时把日志转 stderr；导入后恢复测试框架的 stdout。
_saved_stdout = sys.stdout
try:
    from worker import GFlowService
finally:
    sys.stdout = _saved_stdout


class WorkerAccountsTest(unittest.IsolatedAsyncioTestCase):
    def make_service(self, *, balance=1050, error=None, exists=True):
        token = SimpleNamespace(
            id=1, at="synthetic", google_cookies="SID=fake", email="test@example.com",
            name="", remark="", is_active=True, credits=1050, ban_reason=None,
            banned_at=None, created_at=None, last_used_at=None, use_count=0,
            user_paygate_tier="FREE", current_project_id="", current_project_name="",
            image_enabled=True, video_enabled=True,
            at_expires=datetime.now(timezone.utc) + timedelta(hours=1),
            last_st_refresh_at=None, last_st_refresh_result="",
        )
        cache = {}
        manager = SimpleNamespace(
            refresh_credits=AsyncMock(side_effect=AssertionError(
                "manual credit lookup must not refresh login")),
            get_all_tokens=AsyncMock(return_value=[token] if exists else []),
            _at_validation_cache=cache,
            _mark_at_valid=Mock(side_effect=lambda tid: cache.__setitem__(
                tid, time.monotonic() + 300)),
            _clear_at_validation_cache=Mock(side_effect=lambda tid: cache.pop(tid, None)),
        )
        service = GFlowService.__new__(GFlowService)
        service.initialize = AsyncMock()
        service.initialized = True
        service.db_path = str(ROOT / "data/flow.db")
        service.tmp_dir = ROOT / "data/tmp"
        service.account_validation_failures = {}
        service.flow_client = SimpleNamespace(get_credits=AsyncMock(
            return_value={"credits": balance, "userPaygateTier": "synthetic"},
            side_effect=error,
        ))
        service.db = SimpleNamespace(
            get_token=AsyncMock(return_value=token if exists else None),
            update_token=AsyncMock(),
        )
        service.token_manager = manager
        return service, GFlowService.handle_refresh_credits

    async def test_real_zero_and_integer_balance_are_successful(self):
        for balance in [1050, 0]:
            with self.subTest(balance=balance):
                service, method = self.make_service(balance=balance)
                service.account_validation_failures[1] = "previous_failure"
                self.assertEqual(await method(service, {"token_id": 1}),
                                 {"success": True, "credits": balance})
                self.assertEqual(service.db.update_token.await_args.kwargs["credits"], balance)
                service.token_manager.refresh_credits.assert_not_awaited()
                service.token_manager._mark_at_valid.assert_called_once_with(1)
                self.assertGreater(service.token_manager._at_validation_cache[1], time.monotonic())
                self.assertNotIn(1, service.account_validation_failures)
                service.token_manager._clear_at_validation_cache.assert_not_called()

    async def test_rejected_session_does_not_report_zero_or_overwrite_balance(self):
        service, method = self.make_service(
            error=RuntimeError("Flow login authentication failed (rpc=nzlxg, code=401)"))
        service.token_manager._at_validation_cache[1] = time.monotonic() + 300
        result = await method(service, {"token_id": 1})
        self.assertFalse(result["success"])
        self.assertEqual(result["status_code"], 401)
        self.assertEqual(result["error"]["code"], "gflow_session_expired")
        self.assertNotIn("credits", result)
        service.db.update_token.assert_not_awaited()
        service.token_manager.refresh_credits.assert_not_awaited()
        service.token_manager._clear_at_validation_cache.assert_called_once_with(1)
        service.token_manager._mark_at_valid.assert_not_called()
        self.assertNotIn(1, service.token_manager._at_validation_cache)
        self.assertEqual(service.account_validation_failures[1], "gflow_google_auth_rejected")

    async def test_network_failure_does_not_report_zero(self):
        service, method = self.make_service(error=TimeoutError("synthetic timeout"))
        service.token_manager._at_validation_cache[1] = time.monotonic() + 300
        result = await method(service, {"token_id": 1})
        self.assertFalse(result["success"])
        self.assertEqual(result["status_code"], 504)
        service.db.update_token.assert_not_awaited()
        service.token_manager.refresh_credits.assert_not_awaited()
        service.token_manager._clear_at_validation_cache.assert_called_once_with(1)
        service.token_manager._mark_at_valid.assert_not_called()
        self.assertNotIn(1, service.token_manager._at_validation_cache)
        self.assertTrue(service.account_validation_failures[1])

    async def test_unknown_account_does_not_call_upstream(self):
        service, method = self.make_service(exists=False)
        result = await method(service, {"token_id": 999})
        self.assertEqual(result["status_code"], 404)
        service.flow_client.get_credits.assert_not_awaited()
        service.token_manager._mark_at_valid.assert_not_called()
        service.token_manager._clear_at_validation_cache.assert_not_called()
        self.assertEqual(service.account_validation_failures, {})

    async def test_status_and_token_list_follow_live_login_validation_evidence(self):
        service, method = self.make_service()
        # 缓存余额与启用开关不能在状态 API 中伪装成已经验证。
        before = await service.handle_status({})
        self.assertEqual(before["enabled_tokens"], 1)
        self.assertEqual(before["verified_tokens"], 0)
        self.assertEqual(before["cached_credits"], 1050)
        listing = await service.handle_list_tokens({})
        self.assertEqual(listing["tokens"][0]["health"]["code"], "unverified")
        self.assertNotIn("test@example.com", str(listing))
        # 查额成功只验证登录额度接口，状态不承诺生成成功。
        await method(service, {"token_id": 1})
        after = await service.handle_status({})
        self.assertEqual(after["verified_tokens"], 1)
        health = (await service.handle_list_tokens({}))["tokens"][0]["health"]
        self.assertEqual(health["code"], "verified")
        self.assertIn("captcha", health["detail"])
        # 最新网络错误清掉旧成功 TTL，账号仍启用，历史余额也保留。
        service.flow_client.get_credits.side_effect = TimeoutError("synthetic timeout")
        await method(service, {"token_id": 1})
        failed = await service.handle_status({})
        self.assertEqual(failed["enabled_tokens"], 1)
        self.assertEqual(failed["verified_tokens"], 0)
        self.assertEqual(failed["cached_credits"], 1050)
        health = (await service.handle_list_tokens({}))["tokens"][0]["health"]
        self.assertEqual(health["code"], "refresh_failed")
        self.assertTrue(health["enabled"])
        self.assertFalse(health["verified"])


if __name__ == "__main__":
    unittest.main()
