"""Signing in to the dashboard: a sign-in page, a signed session cookie, and a cap on wrong passwords.

The cookie holds the user name and an expiry, signed with a key derived from
DASHBOARD_PASSWORD_HASH. Nothing is stored on the server, and a new password
ends every session. The slow password hash only runs when the form is sent.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import math
import secrets
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from string import Template
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from ipo_intel.passwords import verify_password
from ipo_intel.timeutil import Clock

COOKIE = "ipo_session"
# "Keep me signed in" keeps the cookie for 30 days; otherwise it lasts until the
# browser closes, and at most 12 hours.
LONG_SESSION = timedelta(days=30)
SHORT_SESSION = timedelta(hours=12)
# Reachable without a session. Everything else needs one.
OPEN_PATHS = frozenset({"/healthz", "/login", "/logout"})
_MAX_FORM_BYTES = 4096

_PAGE = Template(Path(__file__).with_name("login.html").read_text(encoding="utf-8"))
_ICON_ALERT = '<path d="M12 8v5M12 16.5v.5M10.3 3.8L2.6 17a2 2 0 001.7 3h15.4a2 2 0 001.7-3L13.7 3.8a2 2 0 00-3.4 0z"/>'
_ICON_INFO = '<path d="M12 11v6M12 7.5v.5M12 21a9 9 0 110-18 9 9 0 010 18z"/>'


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class Sessions:
    """Checks the password and issues and reads the session cookie."""

    def __init__(self, user: str, password_hash: str, clock: Clock) -> None:
        self.user = user
        self._hash = password_hash
        self._key = hmac.new(password_hash.encode(), b"ipo-intel session cookie", hashlib.sha256).digest()
        self._clock = clock
        self._user_part = _b64(user.encode())

    def check_password(self, user: str, password: str) -> bool:
        user_ok = hmac.compare_digest(user.encode(), self.user.encode())
        # Always hashed, so a wrong user name takes as long as a wrong password.
        password_ok = verify_password(password, self._hash)
        return user_ok and password_ok

    def issue(self, lifetime: timedelta) -> str:
        expires = int((self._clock.now() + lifetime).timestamp())
        body = f"{self._user_part}.{expires}"
        return f"{body}.{self._sign(body)}"

    def ends(self, token: str | None) -> datetime | None:
        """When the session in `token` ends; None unless it is a current session of this user."""
        if not token:
            return None
        body, _, signature = token.rpartition(".")
        if not hmac.compare_digest(signature.encode(), self._sign(body).encode()):
            return None
        user_part, _, expires = body.partition(".")
        if user_part != self._user_part or not expires.isdigit():
            return None
        ends = datetime.fromtimestamp(int(expires), UTC)
        return ends if ends > self._clock.now() else None

    def _sign(self, body: str) -> str:
        return _b64(hmac.new(self._key, body.encode(), hashlib.sha256).digest())


class Throttle:
    """Pauses sign-in after too many wrong passwords from one client, or from everyone together.

    The client is told apart by its forwarded address, which it can fake, so the
    shared cap is what really bounds guessing. Sessions already open are not affected.
    """

    def __init__(self, clock: Clock, *, per_client: int = 10, overall: int = 30,
                 window: timedelta = timedelta(minutes=15)) -> None:
        self._clock = clock
        self._per_client = per_client
        self._overall = overall
        self._window = window
        self._attempts: deque[tuple[datetime, str]] = deque()

    def wait(self, client: str) -> timedelta | None:
        """How long `client` must wait before trying again; None if it may try now."""
        now = self._clock.now()
        while self._attempts and self._attempts[0][0] <= now - self._window:
            self._attempts.popleft()
        mine = [at for at, who in self._attempts if who == client]
        if len(mine) >= self._per_client:
            return mine[-self._per_client] + self._window - now
        if len(self._attempts) >= self._overall:
            return self._attempts[-self._overall][0] + self._window - now
        return None

    def attempt(self, client: str) -> None:
        """Counted before the password is checked, so guesses sent in parallel cannot slip past."""
        self._attempts.append((self._clock.now(), client))

    def forgive(self, client: str) -> None:
        self._attempts = deque(entry for entry in self._attempts if entry[1] != client)


def safe_next(value: str | None) -> str:
    """`value` if it is a path on this site, else "/": never a way to send someone elsewhere."""
    if (
        not value
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or any(ch < " " or ch == "\x7f" for ch in value)
        or value.split("?", 1)[0] in ("/login", "/logout")
    ):
        return "/"
    return value


def login_url(request: Request) -> str:
    """The sign-in page, set to come back to the page that was asked for."""
    here = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return "/login" if here == "/" else "/login?next=" + quote(here, safe="/")


def _client(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "")


def _is_https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    return request.url.scheme == "https" or proto == "https"


async def _read_form(request: Request) -> dict[str, str] | None:
    """The posted form's fields (first value of each); None if the body is too large."""
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > _MAX_FORM_BYTES:
            return None
    fields = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
    return {name: values[0] for name, values in fields.items()}


def _message(text: str, tone: str) -> str:
    icon = _ICON_ALERT if tone == "bad" else _ICON_INFO
    role = "alert" if tone == "bad" else "status"
    return (
        f'<div class="msg {tone}" role="{role}"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
        f'aria-hidden="true">{icon}</svg><span>{html.escape(text)}</span></div>'
    )


def render_page(*, next_path: str = "/", user: str = "", message: str = "", tone: str = "bad",
                wrong_password: bool = False, remember: bool = True, nonce: str = "") -> str:
    return _PAGE.substitute(
        nonce=nonce,
        next=html.escape(next_path),
        user=html.escape(user),
        message=_message(message, tone) if message else "",
        state="invalid" if wrong_password else "",
        invalid='aria-invalid="true"' if wrong_password else "",
        focus_user="" if user else "autofocus",
        focus_password="autofocus" if user else "",
        remember="checked" if remember else "",
    )


def _page_response(status_code: int = 200, **page: object) -> HTMLResponse:
    nonce = secrets.token_urlsafe(16)
    return HTMLResponse(
        render_page(nonce=nonce, **page),  # type: ignore[arg-type]
        status_code=status_code,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                f"default-src 'none'; script-src 'nonce-{nonce}'; "
                f"style-src 'nonce-{nonce}' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
                "img-src data:; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
            ),
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
        },
    )


def add_sign_in(app: FastAPI, user: str, password_hash: str | None, clock: Clock) -> None:
    """The sign-in page, sign-out, /api/session, and (with a password hash) the gate in front of the rest."""
    sessions = Sessions(user, password_hash, clock) if password_hash is not None else None
    throttle = Throttle(clock)

    if sessions is not None:

        @app.middleware("http")
        async def require_session(request: Request, call_next):
            path = request.url.path
            if path in OPEN_PATHS or sessions.ends(request.cookies.get(COOKIE)):
                return await call_next(request)
            if path == "/api" or path.startswith("/api/") or request.method not in ("GET", "HEAD"):
                return JSONResponse({"detail": "Sign in to use the dashboard."}, status_code=401)
            return RedirectResponse(login_url(request), status_code=303)

    @app.get("/login", include_in_schema=False)
    def sign_in_page(request: Request) -> Response:
        target = safe_next(request.query_params.get("next"))
        if sessions is None or sessions.ends(request.cookies.get(COOKIE)):
            return RedirectResponse(target, status_code=303)
        if "signed_out" in request.query_params:
            return _page_response(next_path=target, user=sessions.user, message="You have signed out.", tone="info")
        if "expired" in request.query_params:
            return _page_response(next_path=target, user=sessions.user,
                                  message="Your session ended. Sign in again to carry on.", tone="info")
        return _page_response(next_path=target)

    @app.post("/login", include_in_schema=False)
    async def sign_in(request: Request) -> Response:
        if sessions is None:
            return RedirectResponse("/", status_code=303)
        form = await _read_form(request)
        if form is None:
            return _page_response(413, message="That form was too large to read.")
        given_user, password = form.get("user", ""), form.get("password", "")
        target = safe_next(form.get("next"))
        remember = form.get("remember") == "1"
        client = _client(request)
        wait = throttle.wait(client)
        if wait is not None:
            minutes = max(1, math.ceil(wait.total_seconds() / 60))
            return _page_response(
                429, next_path=target, user=given_user, remember=remember,
                message=f"Too many wrong passwords. Try again in {minutes} minute{'s' if minutes != 1 else ''}.",
            )
        throttle.attempt(client)
        if not await run_in_threadpool(sessions.check_password, given_user, password):
            return _page_response(401, next_path=target, user=given_user, remember=remember, wrong_password=True,
                                  message="That user name and password do not match.")
        throttle.forgive(client)
        lifetime = LONG_SESSION if remember else SHORT_SESSION
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(
            COOKIE, sessions.issue(lifetime), max_age=int(lifetime.total_seconds()) if remember else None,
            path="/", secure=_is_https(request), httponly=True, samesite="lax",
        )
        return response

    @app.post("/logout", include_in_schema=False)
    def sign_out(request: Request) -> Response:
        if sessions is None:
            return RedirectResponse("/", status_code=303)
        response = RedirectResponse("/login?signed_out=1", status_code=303)
        response.delete_cookie(COOKIE, path="/", secure=_is_https(request), httponly=True, samesite="lax")
        return response

    @app.get("/api/session")
    def session(request: Request) -> dict:
        """Whether sign-in is on and, if so, who is signed in until when."""
        if sessions is None:
            return {"sign_in": False, "user": None, "expires_at": None}
        ends = sessions.ends(request.cookies.get(COOKIE))
        return {"sign_in": True, "user": sessions.user, "expires_at": ends.isoformat() if ends else None}
