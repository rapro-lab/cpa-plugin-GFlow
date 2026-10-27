"""验证扩展到真实账号管理器/SQLite 的链路；上游请求全部使用模拟对象。"""
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, main
from unittest.mock import AsyncMock
from send2trash import send2trash

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python"), str(ROOT / "python/flow2api")]

from extension_sync import sync_extension_account
from src.core.database import Database
from src.services.token_manager import TokenManager
from src.services.proxy_manager import ProxyManager
from worker import GFlowService


class ExtensionPipelineTest(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = ROOT / ".tools/test-sync" / uuid.uuid4().hex
        self.directory.mkdir(parents=True)
        self.db = Database(str(self.directory / "flow.db"))
        await self.db.init_db()
        self.client = SimpleNamespace(
            st_to_at=AsyncMock(return_value={
                "access_token": "synthetic-at",
                "credits": 12,
                "user": {"email": "example@example.test"},
            }),
            get_credits=AsyncMock(side_effect=AssertionError("the verification already carries credits; it must not query again")),
            create_project=AsyncMock(return_value="11111111-1111-1111-1111-111111111111"),
        )
        self.manager = TokenManager(self.db, self.client)
        self.manager._get_project_pool_size = lambda: 1
        self.service = SimpleNamespace(
            initialize=AsyncMock(), flow_client=self.client,
            token_manager=self.manager, db=self.db,
        )
        self.payload = {
            "login_account": "example@example.test",
            "google_cookies": [
                {"name": "SID", "value": "synthetic-account", "domain": ".google.com", "path": "/"},
                {"name": "__Secure-OSID", "value": "synthetic-flow", "domain": "flow.google.com", "path": "/", "hostOnly": True},
            ],
        }

    async def asyncTearDown(self):
        send2trash(str(self.directory))

    async def test_proxy_settings_saved_through_real_database_signature(self):
        service = GFlowService(self.directory)
        service.db = self.db
        service.proxy_manager = ProxyManager(self.db)
        service.initialize = AsyncMock()
        result = await service.handle_update_config({
            "proxy": {"enabled": True, "url": "127.0.0.1:7890"}
        })
        self.assertTrue(result["success"])
        proxy = await self.db.get_proxy_config()
        self.assertTrue(proxy.enabled)
        self.assertEqual(proxy.proxy_url, "http://127.0.0.1:7890")

    async def test_add_then_update_without_revalidating_or_reenabling(self):
        added = await sync_extension_account(self.service, self.payload)
        self.assertEqual(added["action"], "added")
        account = await self.db.get_token(added["token_id"])
        self.assertEqual(account.credits, 12)
        self.assertEqual(account.email, "example@example.test")
        self.assertIn("hostOnly", account.google_cookies)
        self.assertEqual(self.client.st_to_at.await_count, 1)
        self.client.get_credits.assert_not_awaited()
        self.client.create_project.assert_awaited_once()

        await self.db.update_token(account.id, is_active=False)
        updated = await sync_extension_account(self.service, self.payload)
        self.assertEqual(updated["action"], "updated")
        self.assertEqual(updated["token_id"], added["token_id"])
        self.assertFalse((await self.db.get_token(account.id)).is_active)
        self.assertEqual(len(await self.db.get_all_tokens()), 1)
        self.assertEqual(self.client.st_to_at.await_count, 2)
        self.client.create_project.assert_awaited_once()


if __name__ == "__main__":
    main()
