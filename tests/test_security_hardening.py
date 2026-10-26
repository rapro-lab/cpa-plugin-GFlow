"""Security hardening regressions.

Covers the three approved items:
1. random admin/api secrets generated at startup (no shared defaults in git);
2. bcrypt admin passwords, login throttling and expiring session tokens;
3. removal of the write-only ``login_password`` column.
"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "python" / "flow2api"))

from src.core.auth import AuthManager
from src.core.config import config
from src.core.database import Database


def read_credentials(path):
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if " = " in line and not line.startswith("#"):
            key, _, value = line.partition(" = ")
            values[key.strip()] = value.strip()
    return values


class BootstrapSecretTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "flow.db")

    def tearDown(self):
        self.tmp.cleanup()

    def bootstrap(self, global_config):
        async def run():
            db = Database(self.db_path)
            await db.init_db()
            await db.init_config_from_toml(
                {"global": global_config}, is_first_startup=True
            )
            result = await db.ensure_bootstrap_credentials()
            return db, result, await db.get_admin_config()

        return asyncio.run(run())

    def test_first_boot_replaces_empty_secrets_with_random_values(self):
        _, result, admin = self.bootstrap(
            {"admin_username": "admin", "admin_password": "", "api_key": ""}
        )

        self.assertTrue(result["password_rotated"])
        self.assertTrue(result["api_key_rotated"])

        credentials = Path(result["credentials_path"])
        self.assertTrue(credentials.is_file())
        values = read_credentials(credentials)

        self.assertEqual(values["admin_username"], "admin")
        self.assertGreaterEqual(len(values["admin_password"]), 16)
        self.assertGreaterEqual(len(values["api_key"]), 32)
        self.assertGreaterEqual(len(values["connection_token"]), 32)
        self.assertNotIn("han1234", values["api_key"])

        # The database keeps only the bcrypt hash, never the plaintext.
        self.assertTrue(AuthManager.is_password_hashed(admin.password))
        self.assertNotIn(values["admin_password"], admin.password)
        self.assertNotIn("$2", credentials.read_text(encoding="utf-8"))
        self.assertTrue(
            AuthManager.verify_password(values["admin_password"], admin.password)
        )
        self.assertEqual(admin.api_key, values["api_key"])

    def test_legacy_shared_defaults_are_rotated(self):
        _, result, admin = self.bootstrap(
            {
                "admin_username": "admin",
                "admin_password": "admin",
                "api_key": "han1234",
            }
        )

        self.assertTrue(result["password_rotated"])
        self.assertTrue(result["api_key_rotated"])
        self.assertTrue(AuthManager.is_password_hashed(admin.password))
        self.assertFalse(AuthManager.verify_password("admin", admin.password))
        self.assertNotEqual(admin.api_key, "han1234")

    def test_second_boot_is_idempotent(self):
        db, first, admin = self.bootstrap(
            {"admin_username": "admin", "admin_password": "", "api_key": ""}
        )
        credentials = Path(first["credentials_path"])
        before = credentials.read_text(encoding="utf-8")

        async def second():
            result = await db.ensure_bootstrap_credentials()
            return result, await db.get_admin_config()

        result, after = asyncio.run(second())
        self.assertFalse(result["password_rotated"])
        self.assertFalse(result["api_key_rotated"])
        self.assertFalse(result["connection_token_generated"])
        self.assertIsNone(result["credentials_path"])
        self.assertEqual(after.password, admin.password)
        self.assertEqual(credentials.read_text(encoding="utf-8"), before)

    def test_custom_plaintext_password_is_hashed_without_rotation(self):
        _, result, admin = self.bootstrap(
            {
                "admin_username": "admin",
                "admin_password": "operator-chosen-pass",
                "api_key": "k" * 40,
            }
        )

        self.assertFalse(result["password_rotated"])
        self.assertFalse(result["api_key_rotated"])
        self.assertIsNone(result["credentials_path"])
        self.assertFalse(
            (Path(self.tmp.name) / Database.CREDENTIALS_FILENAME).exists()
        )
        self.assertTrue(AuthManager.is_password_hashed(admin.password))
        self.assertTrue(
            AuthManager.verify_password("operator-chosen-pass", admin.password)
        )


class AuthManagerTests(unittest.TestCase):
    def setUp(self):
        self.saved = (config.admin_username, config.admin_password, config.api_key)

    def tearDown(self):
        config.admin_username, config.admin_password, config.api_key = self.saved

    def test_empty_api_key_fails_closed(self):
        config.api_key = ""
        self.assertFalse(AuthManager.verify_api_key(""))
        self.assertFalse(AuthManager.verify_api_key("anything"))

        config.api_key = "s3cret"
        self.assertTrue(AuthManager.verify_api_key("s3cret"))
        self.assertFalse(AuthManager.verify_api_key("s3creT"))

    def test_verify_admin_supports_bcrypt_and_legacy_plaintext(self):
        config.admin_username = "admin"

        hashed = AuthManager.hash_password("correct-horse")
        config.admin_password = hashed
        self.assertTrue(AuthManager.verify_admin("admin", "correct-horse"))
        self.assertFalse(AuthManager.verify_admin("admin", "wrong"))
        self.assertFalse(AuthManager.verify_admin("root", "correct-horse"))

        config.admin_password = "legacy-plain"
        self.assertTrue(AuthManager.verify_admin("admin", "legacy-plain"))
        self.assertFalse(AuthManager.verify_admin("admin", "legacy-plain "))

    def test_empty_stored_password_never_authenticates(self):
        config.admin_username = "admin"
        config.admin_password = ""
        self.assertFalse(AuthManager.verify_admin("admin", ""))
        self.assertFalse(AuthManager.verify_admin("", ""))


class LoginPasswordColumnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / "flow.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def columns(self):
        async def run():
            async with self.db._connect() as conn:
                cursor = await conn.execute("PRAGMA table_info(tokens)")
                return [row[1] for row in await cursor.fetchall()]

        return asyncio.run(run())

    def test_fresh_schema_has_no_login_password(self):
        asyncio.run(self.db.init_db())
        self.assertNotIn("login_password", self.columns())

    def test_upgrade_drops_legacy_login_password_column(self):
        async def prepare():
            await self.db.init_db()
            async with self.db._connect(write=True) as conn:
                await conn.execute(
                    "ALTER TABLE tokens ADD COLUMN login_password TEXT DEFAULT ''"
                )
                await conn.commit()

        asyncio.run(prepare())
        self.assertIn("login_password", self.columns())

        asyncio.run(self.db.check_and_migrate_db({}))
        self.assertNotIn("login_password", self.columns())


class AdminSessionTests(unittest.TestCase):
    def setUp(self):
        from src.api import admin

        self.admin = admin
        admin.active_admin_tokens.clear()
        admin._login_failures.clear()

    def tearDown(self):
        self.admin.active_admin_tokens.clear()
        self.admin._login_failures.clear()

    def test_session_tokens_expire(self):
        admin = self.admin
        admin._register_admin_session("admin-token")
        self.assertTrue(admin._admin_session_is_active("admin-token"))
        self.assertTrue(admin.is_admin_session_token_valid("admin-token"))

        admin.active_admin_tokens["admin-token"] = 0
        self.assertFalse(admin._admin_session_is_active("admin-token"))
        self.assertNotIn("admin-token", admin.active_admin_tokens)

    def test_login_is_throttled_after_repeated_failures(self):
        from fastapi import HTTPException

        admin = self.admin
        key = "127.0.0.1|admin"
        for _ in range(admin.LOGIN_MAX_FAILURES):
            admin._record_login_failure(key)

        with self.assertRaises(HTTPException) as raised:
            admin._enforce_login_rate_limit(key)
        self.assertEqual(raised.exception.status_code, 429)
        self.assertIn("Retry-After", raised.exception.headers)

        admin._clear_login_failures(key)
        admin._enforce_login_rate_limit(key)  # no raise after a successful login

    def test_stale_failures_do_not_lock_the_account(self):
        admin = self.admin
        key = "127.0.0.1|admin"
        old = admin.time.monotonic() - admin.LOGIN_WINDOW_SECONDS - 1
        admin._login_failures[key] = [old] * admin.LOGIN_MAX_FAILURES
        admin._enforce_login_rate_limit(key)


class RepositorySecretTests(unittest.TestCase):
    def test_shipped_config_has_no_shared_secrets(self):
        for name in ("setting.toml", "setting_example.toml"):
            text = (
                ROOT / "python" / "flow2api" / "config" / name
            ).read_text(encoding="utf-8")
            with self.subTest(file=name):
                self.assertNotIn("han1234", text)
                self.assertNotRegex(text, r'admin_password\s*=\s*"admin"')


if __name__ == "__main__":
    unittest.main()
