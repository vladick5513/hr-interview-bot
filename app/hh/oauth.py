import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

HH_AUTHORIZE_URL = "https://hh.ru/oauth/authorize"
STATE_TTL = timedelta(minutes=10)


@dataclass(frozen=True)
class PendingAuth:
    hr_tg_id: int
    code_verifier: str
    created_at: datetime


class HHAuthFlow:
    def __init__(self, client_id: str, redirect_uri: str):
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self._pending: dict[str, PendingAuth] = {}

    def start(self, hr_tg_id: int) -> str:
        self._drop_expired()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        self._pending[state] = PendingAuth(hr_tg_id, verifier, datetime.now(UTC))
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "state": state,
            "redirect_uri": self.redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "role": "employer",
            "force_role": "true",
            "skip_choose_account": "true",
        }
        return f"{HH_AUTHORIZE_URL}?{urlencode(params)}"

    def pop(self, state: str) -> PendingAuth | None:
        self._drop_expired()
        return self._pending.pop(state, None)

    def _drop_expired(self) -> None:
        deadline = datetime.now(UTC) - STATE_TTL
        for state in [s for s, p in self._pending.items() if p.created_at < deadline]:
            del self._pending[state]
