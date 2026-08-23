"""Application authentication: users from config.yml + HMAC-signed session tokens."""

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Optional

TOKEN_TTL_SECONDS = 30 * 24 * 3600  # 30 days


class AuthManager:
    def __init__(self, users: list, secret_key: str):
        # users: [{"username": ..., "password": ...}, ...]
        self._users = {
            str(u["username"]): str(u["password"]) for u in users if u.get("username")
        }
        self._secret = secret_key.encode("utf-8")

    def verify_credentials(self, username: str, password: str) -> bool:
        expected = self._users.get(username)
        if expected is None:
            # Constant-time-ish behaviour for unknown users.
            hmac.compare_digest(password.encode(), b"")
            return False
        return hmac.compare_digest(password.encode(), expected.encode())

    def issue_token(self, username: str) -> str:
        payload = {
            "u": username,
            "exp": int(time.time()) + TOKEN_TTL_SECONDS,
            "n": secrets.token_hex(8),
        }
        body = (
            base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        )
        sig = hmac.new(self._secret, body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{sig}"

    def verify_token(self, token: str) -> Optional[str]:
        try:
            body, sig = token.rsplit(".", 1)
            expected_sig = hmac.new(
                self._secret, body.encode(), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(sig, expected_sig):
                return None
            padding = "=" * (-len(body) % 4)
            payload = json.loads(base64.urlsafe_b64decode(body + padding))
            if int(payload.get("exp", 0)) < time.time():
                return None
            username = payload.get("u")
            if username not in self._users:
                return None
            return username
        except Exception:
            return None
