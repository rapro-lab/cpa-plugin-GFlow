"""GFlow 浏览器同步；仅返回可公开的诊断，不包含 Cookie 或连接密钥。"""
import asyncio
import hashlib
import re
from datetime import datetime

from credentials import normalize_account_input


class ExtensionSyncError(ValueError):
    def __init__(self, message, *, code, status_code=400, stage="input"):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.stage = stage

    def result(self):
        return {
            "success": False,
            "status_code": self.status_code,
            "error": {"code": self.code, "message": str(self), "stage": self.stage,
                      "diagnostic": getattr(self, "diagnostic", {})},
        }


def _classify_sync_failure(error, stage="session"):
    """只按已知错误特征分类；不把上游响应、Cookie 或异常原文返回浏览器。"""
    text = str(error).lower()
    curl_code = getattr(error, "code", None)
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)) or curl_code == 28 or "timed out" in text:
        return ExtensionSyncError(
            "Timed out connecting to Google Flow; check the GFlow proxy settings and retry.",
            code="gflow_upstream_timeout", status_code=504, stage=stage,
        )
    if any(term in text for term in ("code=401", "code=403", "public_error_user_not_authenticated", "login authentication failed")):
        return ExtensionSyncError(
            "Google did not accept the current session. Open Flow in the same browser, confirm the project page loads, refresh the page and click “Sync now”; use a separate browser profile for each Google account.",
            code="gflow_google_auth_rejected", status_code=422, stage=stage,
        )
    if any(term in text for term in ("bootstrap metadata", "returned no payload")):
        return ExtensionSyncError(
            "Flow returned an unrecognized page or API response. Open the Flow project page first to finish login or the welcome screen; if it still fails, the GFlow web adapter needs an update.",
            code="gflow_upstream_protocol", status_code=502, stage=stage,
        )
    if curl_code in (5, 6, 7, 35, 60) or any(term in text for term in ("could not resolve", "failed to connect", "proxy connection", "ssl connect")):
        return ExtensionSyncError(
            "The GFlow backend cannot reach Google Flow; check the proxy address and port in GFlow settings.",
            code="gflow_upstream_network", status_code=502, stage=stage,
        )
    if stage == "prepare":
        return ExtensionSyncError(
            "The session is valid but preparing the Flow project failed; the account was not added. Confirm the web UI can create a project, then retry.",
            code="gflow_project_prepare_failed", status_code=502, stage=stage,
        )
    if stage in ("initialize", "save"):
        return ExtensionSyncError(
            "GFlow could not save the local account data; restart the plugin and retry.",
            code="gflow_local_storage_failed", status_code=500, stage=stage,
        )
    return ExtensionSyncError(
        "Google session validation failed. In the same browser, confirm the Flow project page opens, then retry.",
        code="gflow_session_validation_failed", status_code=502, stage=stage,
    )


def classify_sync_failure(error, stage="session"):
    failure = _classify_sync_failure(error, stage)
    # 只提取固定枚举和数字，不保存异常原文、请求头或 Google 响应。
    text = str(error)
    diagnostic = {}
    rpc = re.search(r"\brpc=(nzlxg|jHPbke)\b", text)
    if rpc:
        diagnostic.update(phase="rpc", rpc=rpc.group(1))
    elif "bootstrap" in text.lower() or "Session redirected" in text:
        diagnostic["phase"] = "bootstrap"
    code = re.search(r"\bcode=(\d{1,3})\b", text)
    if code:
        diagnostic["rpc_code" if rpc else "http_status"] = int(code.group(1))
    details = getattr(error, "gflow_diagnostic", {})
    if isinstance(details, dict):
        if details.get("phase") in ("bootstrap", "rpc"):
            diagnostic["phase"] = details["phase"]
        if details.get("reason") in ("redirect", "http_auth", "missing_metadata"):
            diagnostic["reason"] = details["reason"]
        if type(details.get("http_status")) is int and 100 <= details["http_status"] < 600:
            diagnostic["http_status"] = details["http_status"]
        for key in ("bootstrap_identity", "xsrf_present", "account_matches"):
            if type(details.get(key)) is bool:
                diagnostic[key] = details[key]
        for key in ("rpc_cookie_count", "duplicate_cookie_names"):
            if type(details.get(key)) is int and 0 <= details[key] <= 1000:
                diagnostic[key] = details[key]
    failure.diagnostic = diagnostic
    return failure


async def sync_extension_account(service, params):
    email = str(params.get("login_account") or "").strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254:
        raise ExtensionSyncError("Enter the email of the current Google account in the GFlow login assistant", code="gflow_invalid_email")
    if not isinstance(params.get("google_cookies"), (str, list, dict)):
        raise ExtensionSyncError("The extension did not submit full cookies; sign in to Google Flow and retry", code="gflow_missing_cookies")
    try:
        session_token, cookies = normalize_account_input(None, params["google_cookies"])
        if not cookies or len(cookies.encode("utf-8")) > 262144:
            raise ValueError("Cookie content is empty or too large")
        from src.services.browser_cookie_utils import validate_flow_cookie_storage
        validate_flow_cookie_storage(cookies)
    except ValueError:
        raise ExtensionSyncError(
            "No complete Flow session was found; sign in to flow.google.com in the same browser, then sync.",
            code="gflow_incomplete_cookies",
        ) from None
    try:
        await service.initialize()
    except Exception as error:
        raise classify_sync_failure(error, "initialize") from None
    # 初始化后再绑定浏览器特征，且在成功、失败和取消时都恢复上次上下文。
    client = service.flow_client
    setter = getattr(client, "_set_request_fingerprint", None)
    getter = getattr(client, "get_request_fingerprint", None)
    previous = getter() if callable(getter) else None
    user_agent = str(params.get("user_agent") or "").strip()
    if len(user_agent) > 512 or any(ord(ch) < 32 or ord(ch) == 127 for ch in user_agent):
        raise ExtensionSyncError("Invalid browser request signature; update the login assistant and retry", code="gflow_invalid_browser")
    frontend = params.get("frontend")
    if frontend is not None:
        if not isinstance(frontend, dict) or not all(isinstance(frontend.get(key), str) for key in ("bl", "sid", "at", "source_path")):
            raise ExtensionSyncError("Invalid Flow page credentials; update the login assistant", code="gflow_invalid_browser")
        if not (re.fullmatch(r"boq_labs-ai-sandbox-frontend_[A-Za-z0-9_.-]{1,100}", frontend["bl"])
                and re.fullmatch(r"-?\d{1,32}", frontend["sid"])
                and re.fullmatch(r"[A-Za-z0-9_:+/=.\-]{1,4096}", frontend["at"])
                and re.fullmatch(r"/projects|/project/[a-fA-F0-9-]{36}", frontend["source_path"])):
            raise ExtensionSyncError("Invalid Flow page credentials; update the login assistant", code="gflow_invalid_browser")
    bound = bool(user_agent or frontend)
    try:
        if bound and callable(setter):
            setter({**(previous or {}), **({"user_agent": user_agent} if user_agent else {}),
                    "gflow_account_email": email, **({"gflow_frontend": dict(frontend)} if frontend else {})})
        try:
            return await _validate_and_save(service, email, session_token, cookies)
        except ExtensionSyncError as failure:
            # 浏览器诊断只是定位证据；绝不据此跳过后台校验或保存账号。
            check = params.get("browser_check")
            if isinstance(check, dict) and type(check.get("success")) is bool:
                failure.diagnostic = getattr(failure, "diagnostic", {})
                failure.diagnostic["browser_credit_ok"] = check["success"]
                for key in ("http_status", "rpc_code"):
                    value=check.get(key)
                    if type(value) is int and 0 <= value < 600:
                        failure.diagnostic["browser_" + key] = value
                if check["success"] and failure.code == "gflow_google_auth_rejected":
                    failure.args=("The browser read the quota, but Google rejected the GFlow backend login check (401). The account was not added; keep comparing browser and backend requests, no need to sign in repeatedly.",)
            if params.get("cookie_source") == "browser_request":
                failure.diagnostic = getattr(failure, "diagnostic", {})
                failure.diagnostic["observed_cookie_header"] = True
            raise
    finally:
        if bound and callable(setter):
            setter(previous)


async def _validate_and_save(service, email, session_token, cookies):
    try:
        session = await service.flow_client.st_to_at(session_token, google_cookies=cookies)
    except Exception as error:
        failure = classify_sync_failure(error)
        refresh = getattr(service, "refresh_extension_login", None)
        if failure.code != "gflow_google_auth_rejected" or not callable(refresh):
            raise failure from None
        # 只允许重试登录/额度读取；不重试新增账号或媒体生成。
        try:
            refreshed = await refresh(cookies, email)
        except Exception as refresh_error:
            raise classify_sync_failure(refresh_error) from None
        if refreshed.get("success") is not True:
            reason = str(refreshed.get("reason") or "")
            failure.diagnostic["cookie_refresh"] = (
                reason if reason in {"missing_identity", "account_mismatch"} else "rejected"
            )
            raise failure from None
        try:
            session_token, cookies = normalize_account_input(
                refreshed.get("session_token"), refreshed.get("google_cookies")
            )
            from src.services.browser_cookie_utils import validate_flow_cookie_storage
            validate_flow_cookie_storage(cookies)
            session = await service.flow_client.st_to_at(session_token, google_cookies=cookies)
        except Exception as retry_error:
            retry_failure = classify_sync_failure(retry_error)
            retry_failure.diagnostic["cookie_refresh"] = "validated"
            raise retry_failure from None
    observed_email = str((session.get("user") or {}).get("email") or "").strip().lower()
    if observed_email and observed_email != email:
        raise ExtensionSyncError(
            "The email entered in the extension does not match the Google account currently signed in",
            code="gflow_account_mismatch", status_code=409, stage="session",
        )
    try:
        existing = await service.db.get_token_by_email(email)
    except Exception as error:
        raise classify_sync_failure(error, "save") from None
    if existing:
        try:
            expires = session.get("expires")
            expires_at = datetime.fromisoformat(expires.replace("Z", "+00:00")) if expires else None
            await service.db.update_token(
                existing.id,
                google_cookies=cookies,
                st=session_token or "frontend-" + hashlib.sha256(cookies.encode()).hexdigest()[:32],
                at=session["access_token"],
                at_expires=expires_at,
                login_account=email,
                protocol_mode="protocol",
                **({"credits": session["credits"]} if "credits" in session else {}),
            )
        except Exception as error:
            raise classify_sync_failure(error, "save") from None
        return {"success": True, "action": "updated", "token_id": existing.id, "message": "GFlow account session updated; the existing enabled/disabled state was kept"}
    try:
        account = await service.token_manager.add_token(
            st=session_token,
            google_cookies=cookies,
            login_account=email,
            protocol_mode="protocol",
            remark="Added by the GFlow login assistant",
            validated_session=session,
        )
    except Exception as error:
        raise classify_sync_failure(error, "prepare") from None
    return {"success": True, "action": "added", "token_id": account.id, "message": "GFlow account added; go back to the account list and refresh"}
