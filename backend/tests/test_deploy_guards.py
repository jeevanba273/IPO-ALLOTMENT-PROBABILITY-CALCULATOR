"""One schedule per database, sign-in for the dashboard, and no unprotected public serving."""

import time

import pytest
from fastapi.testclient import TestClient

from ipo_intel.api.app import create_app
from ipo_intel.cli import main
from ipo_intel.config import ConfigError
from ipo_intel.passwords import hash_password
from ipo_intel.scheduler import SchedulerLock
from tests.fakes import make_settings
from tests.indianapi_world import World


def test_only_one_process_runs_the_schedule(migrated_db_url):
    first, second = SchedulerLock(migrated_db_url), SchedulerLock(migrated_db_url)
    try:
        assert first.acquire() and first.held
        assert first.acquire()  # holding it already is fine
        assert not second.acquire() and not second.held
        first.release()
        assert second.acquire()
    finally:
        first.release()
        second.release()


@pytest.fixture
def world(database, migrated_db_url):
    w = World(database, migrated_db_url)
    yield w
    w.close()


class IdleRunner:
    """Stands in for the job runner so a schedule can start without any job touching the test data."""

    def run(self, *args, **kwargs):
        pass


def test_a_second_copy_serves_the_dashboard_and_takes_the_schedule_over_when_the_first_stops(world, tmp_path):
    holder = SchedulerLock(world.settings.database_url.get_secret_value())
    assert holder.acquire()
    try:
        app = create_app(world.settings, db=world.db, runner=IdleRunner(), clock=world.clock,
                         migrate=False, frontend_dist=tmp_path, lock_retry_seconds=0.05)
        with TestClient(app) as client:
            health = client.get("/api/health").json()
            assert health["scheduler_running"] is False and health["scheduler_elsewhere"] is True
            time.sleep(0.3)  # a few retries while the first copy still holds the lock
            assert client.get("/api/health").json()["scheduler_elsewhere"] is True
            holder.release()  # the first copy stops, as the old one does at the end of a deploy
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not health["scheduler_running"]:
                time.sleep(0.05)
                health = client.get("/api/health").json()
            assert health["scheduler_running"] is True and health["scheduler_elsewhere"] is False
            assert not holder.acquire()  # this copy holds the lock now
    finally:
        holder.release()


@pytest.fixture
def signed_app(world, tmp_path, migrated_db_url):
    settings = make_settings(indianapi_key="k", database_url=migrated_db_url,
                             dashboard_password_hash=hash_password("s3cret-pass"))
    return create_app(settings, db=world.db, runner=world.runner, clock=world.clock,
                      start_scheduler=False, migrate=False, frontend_dist=tmp_path)


def _sign_in(client, password="s3cret-pass", user="admin", headers=None, **form):
    return client.post("/login", data={"user": user, "password": password, **form}, headers=headers,
                       follow_redirects=False)


def test_sign_in_protects_everything_but_the_health_check_and_the_sign_in_page(signed_app):
    with TestClient(signed_app) as client:
        refused = client.get("/api/health")
        # No Basic challenge, so browsers never show their own sign-in box.
        assert refused.status_code == 401 and "www-authenticate" not in refused.headers
        assert client.post("/api/symbol", json={}).status_code == 401
        page = client.get("/ipo/abc?tab=gmp", follow_redirects=False)
        assert page.status_code == 303 and page.headers["location"] == "/login?next=/ipo/abc%3Ftab%3Dgmp"
        assert client.get("/", follow_redirects=False).headers["location"] == "/login"
        assert client.get("/healthz").json() == {"ok": True}
        login = client.get("/login?next=/calendar")
        assert login.status_code == 200 and 'action="/login"' in login.text and 'value="/calendar"' in login.text
        assert login.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in login.headers["content-security-policy"]


def test_signing_in_opens_the_dashboard_until_signing_out(signed_app):
    with TestClient(signed_app) as client:
        wrong = _sign_in(client, "wrong", next="/calendar")
        assert wrong.status_code == 401 and "do not match" in wrong.text and "set-cookie" not in wrong.headers
        assert 'value="admin"' in wrong.text and 'value="/calendar"' in wrong.text
        assert _sign_in(client, user="someone").status_code == 401
        done = _sign_in(client, next="/calendar", remember="1")
        assert done.status_code == 303 and done.headers["location"] == "/calendar"
        cookie = done.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie and "max-age=2592000" in cookie
        assert "secure" not in cookie  # plain http here; Railway's https is below
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/session").json()["user"] == "admin"
        assert client.get("/login", follow_redirects=False).headers["location"] == "/"
        out = client.post("/logout", follow_redirects=False)
        assert out.status_code == 303 and out.headers["location"] == "/login?signed_out=1"
        assert client.get("/api/health").status_code == 401
        assert "You have signed out" in client.get("/login?signed_out=1").text


def test_a_short_session_ends_with_the_browser_and_https_keeps_the_cookie_secure(signed_app):
    with TestClient(signed_app) as client:
        cookie = _sign_in(client).headers["set-cookie"].lower()
        assert "max-age" not in cookie and "expires" not in cookie
        behind_https = _sign_in(client, headers={"X-Forwarded-Proto": "https"}).headers["set-cookie"].lower()
        assert "secure" in behind_https


def test_a_session_ends_on_time_and_cannot_be_forged(signed_app, world):
    with TestClient(signed_app) as client:
        _sign_in(client)  # 12 hours
        token = client.cookies["ipo_session"]
        client.cookies.clear()
        user, expires, signature = token.split(".")
        forged = f"{user}.{int(expires) + 86_400}.{signature}"
        assert client.get("/api/health", headers={"Cookie": f"ipo_session={forged}"}).status_code == 401
        assert client.get("/api/health", headers={"Cookie": f"ipo_session={token}"}).status_code == 200
        world.clock.advance(hours=12, seconds=1)
        assert client.get("/api/health", headers={"Cookie": f"ipo_session={token}"}).status_code == 401


def test_after_signing_in_the_dashboard_never_sends_you_elsewhere(signed_app):
    with TestClient(signed_app) as client:
        assert _sign_in(client, next="//evil.example/").headers["location"] == "/"
        assert _sign_in(client, next="https://evil.example/").headers["location"] == "/"


def test_too_many_wrong_passwords_pause_sign_in(signed_app, world):
    with TestClient(signed_app) as client:
        for _ in range(10):
            assert _sign_in(client, "nope").status_code == 401
        paused = _sign_in(client)  # the right password, but too late
        assert paused.status_code == 429 and "Try again in 15 minutes" in paused.text
        assert "set-cookie" not in paused.headers
        assert _sign_in(client, headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 303
        world.clock.advance(minutes=15)
        assert _sign_in(client).status_code == 303


def test_an_oversized_sign_in_form_is_refused(signed_app):
    with TestClient(signed_app) as client:
        assert _sign_in(client, "x" * 5000).status_code == 413


def test_without_a_password_hash_there_is_nothing_to_sign_in_to(world, tmp_path):
    app = create_app(world.settings, db=world.db, runner=world.runner, clock=world.clock,
                     start_scheduler=False, migrate=False, frontend_dist=tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/session").json() == {"sign_in": False, "user": None, "expires_at": None}
        assert client.get("/login", follow_redirects=False).headers["location"] == "/"
        assert client.get("/api/health").status_code == 200


def test_a_malformed_password_hash_stops_the_app_without_showing_it():
    settings = make_settings(indianapi_key="k", database_url="postgresql://x/y",
                             dashboard_password_hash="a-plain-text-password")
    with pytest.raises(ConfigError) as caught:
        create_app(settings, start_scheduler=False, migrate=False)
    assert "DASHBOARD_PASSWORD_HASH" in str(caught.value)
    assert "a-plain-text-password" not in str(caught.value)


def test_serving_beyond_this_machine_needs_a_password(capsys):
    assert main(["serve", "--host", "0.0.0.0"], settings=make_settings(indianapi_key="k", database_url="postgresql://x/y")) == 2
    assert "set DASHBOARD_PASSWORD_HASH" in capsys.readouterr().err
