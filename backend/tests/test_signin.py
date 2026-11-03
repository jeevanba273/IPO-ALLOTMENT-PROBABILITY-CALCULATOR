"""The sign-in page's parts: session tokens, the cap on wrong passwords, safe redirects and the page itself."""

import re
from datetime import UTC, datetime, timedelta

import pytest

from ipo_intel.api.signin import Sessions, Throttle, render_page, safe_next
from ipo_intel.passwords import hash_password
from tests.fakes import FixedClock

HASH = hash_password("Tulip@456")


@pytest.fixture
def clock():
    return FixedClock(datetime(2026, 11, 4, 9, 0, tzinfo=UTC))


def test_a_session_lasts_as_long_as_it_was_issued_for(clock):
    sessions = Sessions("admin", HASH, clock)
    token = sessions.issue(timedelta(hours=12))
    assert sessions.ends(token) == clock.now() + timedelta(hours=12)
    clock.advance(hours=11, minutes=59)
    assert sessions.ends(token) is not None
    clock.advance(minutes=1)
    assert sessions.ends(token) is None


def test_a_session_is_only_good_for_its_own_user_and_password(clock):
    token = Sessions("admin", HASH, clock).issue(timedelta(days=30))
    assert Sessions("admin", hash_password("Tulip@456"), clock).ends(token) is None  # a new password ends it
    assert Sessions("someone", HASH, clock).ends(token) is None


@pytest.mark.parametrize("token", [None, "", "x", "a.b", "..", "a.b.c", "é.1.ü", "YWRtaW4.99999999999.sig"])
def test_tokens_that_were_not_issued_here_are_refused(clock, token):
    assert Sessions("admin", HASH, clock).ends(token) is None


def test_a_token_cannot_be_stretched(clock):
    sessions = Sessions("admin", HASH, clock)
    user, expires, signature = sessions.issue(timedelta(hours=1)).split(".")
    assert sessions.ends(f"{user}.{int(expires) + 86_400}.{signature}") is None


def test_the_password_check_needs_both_the_user_and_the_password(clock):
    sessions = Sessions("admin", HASH, clock)
    assert sessions.check_password("admin", "Tulip@456")
    assert not sessions.check_password("admin", "tulip@456")
    assert not sessions.check_password("Admin", "Tulip@456")
    assert not sessions.check_password("", "")


def test_one_client_is_paused_after_its_own_wrong_passwords(clock):
    throttle = Throttle(clock, per_client=3, overall=100, window=timedelta(minutes=15))
    for _ in range(3):
        assert throttle.wait("a") is None
        throttle.attempt("a")
        clock.advance(minutes=1)
    assert throttle.wait("a") == timedelta(minutes=12)  # until the first of the three is 15 minutes old
    assert throttle.wait("b") is None
    clock.advance(minutes=12)
    assert throttle.wait("a") is None


def test_everyone_is_paused_when_guesses_come_from_many_addresses(clock):
    throttle = Throttle(clock, per_client=3, overall=5, window=timedelta(minutes=15))
    for n in range(5):
        throttle.attempt(f"client-{n}")
    assert throttle.wait("someone-new") == timedelta(minutes=15)
    clock.advance(minutes=15)
    assert throttle.wait("someone-new") is None


def test_a_right_password_clears_that_clients_count(clock):
    throttle = Throttle(clock, per_client=3, overall=100)
    for _ in range(2):
        throttle.attempt("a")
    throttle.attempt("b")
    throttle.forgive("a")
    throttle.attempt("a")
    throttle.attempt("a")
    assert throttle.wait("a") is None


@pytest.mark.parametrize("value", [
    None, "", "calendar", "https://evil.example/", "//evil.example", "/\\evil.example", "\\\\evil.example",
    "/ok\r\nSet-Cookie: x=1", "/\tevil", "javascript:alert(1)", "/login", "/login?next=/x", "/logout",
])
def test_after_signing_in_nobody_is_sent_off_the_site(value):
    assert safe_next(value) == "/"


@pytest.mark.parametrize("value", ["/", "/calendar", "/ipo/abc?tab=gmp", "/logins"])
def test_after_signing_in_the_page_that_was_asked_for_opens(value):
    assert safe_next(value) == value


def test_the_page_fills_in_every_placeholder_and_escapes_what_it_shows():
    page = render_page(user='"><script>alert(1)</script>', next_path='/x?a="b"', message="<b>no</b>", nonce="n0nce")
    assert "$" not in page
    assert "<script>alert(1)" not in page and "&lt;script&gt;" in page
    assert 'value="/x?a=&quot;b&quot;"' in page
    assert "&lt;b&gt;no&lt;/b&gt;" in page
    assert page.count('nonce="n0nce"') == 2  # the style and the script


def _input(page, field_id):
    return re.search(rf'<input id="{field_id}"[^>]*>', page).group(0)


def test_a_wrong_password_marks_the_field_and_a_known_user_moves_focus_to_it():
    page = render_page(user="admin", message="That user name and password do not match.", wrong_password=True)
    assert 'class="card invalid"' in page
    assert 'aria-invalid="true"' in _input(page, "password") and "autofocus" in _input(page, "password")
    assert "autofocus" not in _input(page, "user")
    fresh = render_page()
    assert "autofocus" in _input(fresh, "user")
    assert "aria-invalid" not in _input(fresh, "password") and "autofocus" not in _input(fresh, "password")
