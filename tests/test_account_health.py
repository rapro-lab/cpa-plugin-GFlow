"""账号健康展示回归：启用、余额与真实登录校验证据分别处理。"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from account_health import account_health, summarize_account_health

NOW = datetime(2026, 10, 17, 4, 0, tzinfo=timezone.utc)


def token(**changes):
    values = dict(id=1, is_active=True, credits=1050, ban_reason=None,
                  at="test", at_expires=NOW + timedelta(hours=1),
                  last_st_refresh_at=None, last_st_refresh_result="")
    values.update(changes)
    return SimpleNamespace(**values)


class AccountHealthTests(unittest.TestCase):
    def health(self, value=None, **kwargs):
        return account_health(value or token(), now=NOW, monotonic_now=100.0, **kwargs)

    def test_enabled_and_cached_balance_are_not_validation(self):
        value = self.health()
        self.assertEqual(value["code"], "unverified")
        self.assertFalse(value["verified"])
        self.assertEqual(value["level"], "warning")
        self.assertIn("cached credits", value["detail"])

    def test_manual_disable_is_distinct_from_expired_session(self):
        value = self.health(token(is_active=False))
        self.assertEqual(value["code"], "disabled")
        self.assertFalse(value["enabled"])
        self.assertEqual(value["label"], "Manually disabled")

    def test_explicit_session_expiry_beats_recent_success(self):
        value = self.health(token(ban_reason="session_expired"), recent_validation_expires=200)
        self.assertEqual(value["code"], "session_expired")
        self.assertFalse(value["verified"])

    def test_rate_limit_is_not_session_expiry(self):
        value = self.health(token(is_active=False, ban_reason="429_rate_limit"))
        self.assertEqual(value["code"], "rate_limited")

    def test_generic_refresh_failure_is_not_expiry(self):
        for error in ("503 Flow frontend RPC rejected", "browser unavailable",
                      "PUBLIC_ERROR_UNUSUAL_ACTIVITY", "401 ambiguous proxy response"):
            with self.subTest(error=error):
                value = self.health(token(last_st_refresh_result=error))
                self.assertEqual(value["code"], "refresh_failed")
                self.assertTrue(value["enabled"])
                self.assertNotIn(error, json.dumps(value, ensure_ascii=False))

    def test_latest_failure_overrides_old_success_ttl(self):
        value = self.health(recent_validation_expires=200, validation_failure="temporary failure")
        self.assertEqual(value["code"], "refresh_failed")
        self.assertFalse(value["verified"])

    def test_recent_validation_proves_login_only(self):
        value = self.health(token(last_st_refresh_result="old browser failure"),
                            recent_validation_expires=101)
        self.assertEqual(value["code"], "verified")
        self.assertTrue(value["verified"])
        self.assertIn("captcha", value["detail"])

    def test_expired_validation_cache_never_shows_green(self):
        for expires in (0, 99, 100, None, "not a number"):
            with self.subTest(expires=expires):
                self.assertFalse(self.health(recent_validation_expires=expires)["verified"])

    def test_validation_due_is_not_proof_of_expired_cookie(self):
        for expiry in (NOW - timedelta(seconds=1), "2026-10-17T03:59:00Z",
                       "2026-10-17T03:59:00"):
            with self.subTest(expiry=expiry):
                value = self.health(token(at_expires=expiry))
                self.assertEqual(value["code"], "verification_due")
                self.assertTrue(value["enabled"])

    def test_saved_success_without_live_validation_is_unverified(self):
        value = self.health(token(last_st_refresh_result="success", last_st_refresh_at=NOW))
        self.assertEqual(value["code"], "unverified")
        self.assertEqual(value["checked_at"], NOW.isoformat())

    def test_mapping_input_and_invalid_optional_timestamp_are_supported(self):
        value = self.health(vars(token(at_expires="invalid", last_st_refresh_at="invalid")))
        self.assertEqual(value["code"], "unverified")
        self.assertIsNone(value["checked_at"])

    def test_public_health_output_contains_no_secret_or_raw_failure(self):
        value = self.health(token(st="secret-cookie", google_cookies="secret-cookie"),
                            validation_failure="https://signed.example/private?key=secret")
        rendered = json.dumps(value)
        self.assertNotIn("secret", rendered)
        self.assertNotIn("signed.example", rendered)

    def test_summary_separates_enabled_verified_and_cached(self):
        values = [token(), token(id=2, is_active=False), token(id=3, credits=20),
                  token(id=4, credits="invalid")]
        health = {1: self.health(values[0]),
                  2: self.health(values[1]),
                  3: self.health(values[2], recent_validation_expires=200),
                  4: self.health(values[3])}
        result = summarize_account_health(values, health)
        self.assertEqual(result["total_tokens"], 4)
        self.assertEqual(result["enabled_tokens"], 3)
        self.assertEqual(result["verified_tokens"], 1)
        self.assertEqual(result["active_tokens"], 1)
        self.assertEqual(result["cached_credits"], 1070)
        self.assertTrue(result["credits_are_cached"])


class AccountHealthUITests(unittest.TestCase):
    def test_badges_use_health_evidence_and_escape_details(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for the page logic regression")
        page = (ROOT / "static/manage.html").read_text(encoding="utf-8")
        script = page.split("<script>", 1)[1].split("</script>", 1)[0]
        checks = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
const context = vm.createContext({window:{addEventListener(){}}});
vm.runInContext(SCRIPT, context);
let badge = context.accountHealthBadge({is_active:true, credits:1050});
assert.match(badge,/badge-warning/);
assert.match(badge,/not verified/);
assert.ok(!badge.includes('badge-success'));
badge = context.accountHealthBadge({is_active:false,ban_reason:'session_expired'});
assert.match(badge,/Session expired/);
badge = context.accountHealthBadge({is_active:true,health:{
  code:'refresh_failed',label:'Re-validation due',level:'warning',detail:'<secret>&"'
}});
assert.match(badge,/Re-validation due/);
assert.ok(!badge.includes('<secret>'));
assert.ok(badge.includes('&lt;secret&gt;&amp;&quot;'));
badge = context.accountHealthBadge({is_active:true,health:{
  code:'verified',label:'Login verified',level:'success',detail:'Recently validated'
}});
assert.match(badge,/badge-success/);
assert.match(badge,/Login verified/);
console.log('PASS truthful account health badges');
"""
        result = subprocess.run(
            [node, "-"], input="const SCRIPT = " + json.dumps(script) + ";\n" + checks,
            capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Cached credits for enabled accounts", page)
        self.assertIn("res.verified_tokens ??", script)


if __name__ == "__main__":
    unittest.main()
