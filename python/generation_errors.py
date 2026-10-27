"""Public generation failures shared by JSON and streaming worker responses."""
import json


def generation_error(payload):
    """Return a worker error envelope without treating failed SSE as success."""
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("data:"):
            text = text[5:].strip()
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            return None
    if not isinstance(payload, dict) or not payload.get("error"):
        return None
    raw = payload["error"]
    error = dict(raw) if isinstance(raw, dict) else {"message": str(raw)}
    message = str(error.get("message") or "Google Flow generation failed")
    status = error.get("status_code", 502)
    try:
        status = int(status)
    except (ValueError, TypeError):
        status = 502
    if not 400 <= status <= 599:
        status = 502
    code = error.get("code") or "gflow_generation_failed"
    if "PUBLIC_ERROR_UNUSUAL_ACTIVITY" in message:
        code, status = "gflow_unusual_activity", 403
        message = ("Google Flow rejected this request (unusual activity, PUBLIC_ERROR_UNUSUAL_ACTIVITY). Check the account state in the signed-in Flow web page; the request was not retried.")
    elif any(marker in message for marker in ("Google Cookie expired", "Flow login expired", "gflow_session_expired")):
        code, status = "gflow_session_expired", 401
        message = "The GFlow Google session expired; re-sync the Cookie with the login assistant from a browser signed in to Flow. Changing the EasyCPA URL or key alone will not fix it."
    return {"message": message, "status_code": status,
            "type": error.get("type") or "generation_error", "code": code}


def account_pool_error(tokens):
    """Keep an empty pool separate from a disabled or expired account."""
    if not tokens:
        return {"message": "No GFlow account yet; add or sync a Google Flow account in the GFlow plugin console first",
                "type": "no_active_tokens", "code": "gflow_empty_account_pool", "status_code": 503}
    if any(getattr(t, "ban_reason", None) == "session_expired" for t in tokens):
        return generation_error({"error": {"message": "gflow_session_expired"}})
    return {"message": "The GFlow account is disabled. Check its state in the plugin console; re-sync the Google Cookie if the session expired, or enable the account if it was disabled manually.",
            "type": "no_active_tokens", "code": "gflow_accounts_disabled", "status_code": 503}
