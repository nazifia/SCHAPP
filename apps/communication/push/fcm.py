"""Firebase Cloud Messaging, HTTP v1.

The v1 API wants a Google OAuth2 access token, not an API key: sign a short
JWT with the service account's private key, swap it for an access token, cache
the token until it expires. That is the whole adapter — `cryptography` is
already a dependency (Fernet does the field encryption), so it costs nothing
extra.

Configure with:

    PUSH_BACKEND=apps.communication.push.fcm.FcmBackend
    FCM_SERVICE_ACCOUNT_FILE=/etc/schapp/fcm-service-account.json

A missing or unreadable file is reported as a failed send, never an exception:
a push that cannot go out must not take an announcement down with it.
"""

import base64
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path

from django.conf import settings

from .base import PushBackend, PushResult

logger = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
TIMEOUT = 15
#: Refresh a minute early rather than discover expiry mid-broadcast.
TOKEN_SKEW = 60


def _b64(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


@lru_cache(maxsize=4)
def _service_account(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class FcmBackend(PushBackend):
    name = "fcm"

    #: (access_token, expires_at) per service-account path.
    _tokens: dict[str, tuple[str, float]] = {}

    def __init__(self, *, service_account_file: str = "", **options):
        super().__init__(**options)
        self.path = service_account_file or getattr(settings, "FCM_SERVICE_ACCOUNT_FILE", "")

    # --- auth --------------------------------------------------------------
    def _access_token(self) -> str:
        cached = self._tokens.get(self.path)
        if cached and cached[1] > time.time() + TOKEN_SKEW:
            return cached[0]

        account = _service_account(self.path)
        now = int(time.time())
        claims = {
            "iss": account["client_email"],
            "scope": SCOPE,
            "aud": TOKEN_URL,
            "iat": now,
            "exp": now + 3600,
        }
        assertion = self._sign(account["private_key"], claims)
        body = urllib.parse.urlencode(
            {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}
        ).encode()
        request = urllib.request.Request(
            TOKEN_URL,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.loads(response.read().decode())

        token = payload["access_token"]
        self._tokens[self.path] = (token, time.time() + int(payload.get("expires_in", 3600)))
        return token

    @staticmethod
    def _sign(private_key_pem: str, claims: dict) -> str:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, rsa

        header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        payload = _b64(json.dumps(claims).encode())
        signing_input = header + b"." + payload
        key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
        # Google issues RSA service-account keys; anything else is a wrong file,
        # and RS256 is the only algorithm the token endpoint accepts here.
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ValueError("The FCM service account key is not an RSA key.")
        signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return (signing_input + b"." + _b64(signature)).decode()

    # --- send --------------------------------------------------------------
    def send(self, token: str, title: str, body: str, *, data: dict | None = None) -> PushResult:
        if not self.path:
            return PushResult(ok=False, provider=self.name, error="No FCM service account set.")
        try:
            access_token = self._access_token()
            project_id = _service_account(self.path)["project_id"]
        except Exception as exc:
            logger.warning("fcm auth failed", extra={"error": exc.__class__.__name__})
            return PushResult(ok=False, provider=self.name, error="FCM authentication failed.")

        request = urllib.request.Request(
            f"https://fcm.googleapis.com/v1/projects/{project_id}/messages:send",
            data=json.dumps(
                {
                    "message": {
                        "token": token,
                        "notification": {"title": title, "body": body},
                        # FCM data values must be strings, all the way down.
                        "data": {k: str(v) for k, v in (data or {}).items()},
                    }
                }
            ).encode(),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                payload = json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            # 404 UNREGISTERED / 400 INVALID_ARGUMENT on the token: it is dead.
            return PushResult(
                ok=False,
                provider=self.name,
                error=f"HTTP {exc.code}",
                invalid_token=exc.code in {400, 404},
            )
        except Exception as exc:
            return PushResult(ok=False, provider=self.name, error=exc.__class__.__name__)

        return PushResult(
            ok=True, provider=self.name, message_id=payload.get("name", ""), raw=payload
        )
