"""Authentication module"""

import bcrypt
import hmac
from typing import Optional
from fastapi import Header, HTTPException, Query, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from .config import config

security = HTTPBearer()
optional_security = HTTPBearer(auto_error=False)

BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")


class AuthManager:
    """Authentication manager"""

    @staticmethod
    def is_password_hashed(value: str) -> bool:
        """Return True when the stored value is a bcrypt hash."""
        return str(value or "").startswith(BCRYPT_PREFIXES)

    @staticmethod
    def verify_api_key(api_key: str) -> bool:
        """Verify API key.

        An empty configured key never authenticates: fail closed instead of
        accepting an empty header when the secret was not generated yet.
        """
        expected = str(config.api_key or "")
        if not expected:
            return False
        return hmac.compare_digest(str(api_key or ""), expected)

    @staticmethod
    def verify_admin(username: str, password: str) -> bool:
        """Verify admin credentials.

        The password may be stored as a bcrypt hash (current format) or as
        plaintext (legacy rows before the automatic upgrade). Empty stored
        credentials never authenticate.
        """
        expected_username = str(config.admin_username or "")
        expected_password = str(config.admin_password or "")
        if not expected_username or not expected_password:
            return False
        if not hmac.compare_digest(str(username or ""), expected_username):
            return False
        if AuthManager.is_password_hashed(expected_password):
            return AuthManager.verify_password(str(password or ""), expected_password)
        return hmac.compare_digest(str(password or ""), expected_password)

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash password"""
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    @staticmethod
    def verify_password(password: str, hashed: str) -> bool:
        """Verify password against a bcrypt hash."""
        try:
            return bcrypt.checkpw(password.encode(), hashed.encode())
        except (ValueError, TypeError):
            return False


async def verify_api_key_header(
    credentials: HTTPAuthorizationCredentials = Security(security),
) -> str:
    """Verify API key from Authorization header"""
    api_key = credentials.credentials
    if not AuthManager.verify_api_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return api_key


async def verify_api_key_flexible(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(optional_security),
    x_goog_api_key: Optional[str] = Header(None, alias="x-goog-api-key"),
    key: Optional[str] = Query(None),
) -> str:
    """Verify API key from Authorization header, x-goog-api-key header, or key query param."""
    api_key = None

    if credentials is not None:
        api_key = credentials.credentials
    elif x_goog_api_key:
        api_key = x_goog_api_key
    elif key:
        api_key = key

    if not api_key or not AuthManager.verify_api_key(api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")

    return api_key
