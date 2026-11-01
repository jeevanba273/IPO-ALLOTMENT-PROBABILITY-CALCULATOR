"""One schedule per database, sign-in for the dashboard, and no unprotected public serving."""

import base64

import pytest
from fastapi.testclient import TestClient

from ipo_intel.api.app import create_app
from ipo_intel.cli import main
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


def test_a_second_copy_serves_the_dashboard_but_leaves_the_schedule_alone(world, tmp_path):
    holder = SchedulerLock(world.settings.database_url.get_secret_value())
    assert holder.acquire()
    try:
        app = create_app(world.settings, db=world.db, runner=world.runner, clock=world.clock,
                         migrate=False, frontend_dist=tmp_path)
        with TestClient(app) as client:
            health = client.get("/api/health").json()
        assert health["scheduler_running"] is False and health["scheduler_elsewhere"] is True
    finally:
        holder.release()


def _basic(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def test_sign_in_protects_everything_but_the_health_check(world, tmp_path, database, migrated_db_url):
    settings = make_settings(indianapi_key="k", database_url=migrated_db_url, dashboard_password="s3cret-pass")
    app = create_app(settings, db=world.db, runner=world.runner, clock=world.clock,
                     start_scheduler=False, migrate=False, frontend_dist=tmp_path)
    with TestClient(app) as client:
        refused = client.get("/api/health")
        assert refused.status_code == 401 and refused.headers["www-authenticate"].startswith("Basic")
        assert client.get("/").status_code == 401
        assert client.get("/api/health", headers=_basic("admin", "wrong")).status_code == 401
        assert client.get("/api/health", headers=_basic("someone", "s3cret-pass")).status_code == 401
        assert client.get("/api/health", headers={"Authorization": "Basic not-base64!"}).status_code == 401
        assert client.get("/api/health", headers=_basic("admin", "s3cret-pass")).status_code == 200
        assert client.get("/healthz").json() == {"ok": True}


def test_serving_beyond_this_machine_needs_a_password(capsys):
    assert main(["serve", "--host", "0.0.0.0"], settings=make_settings(indianapi_key="k", database_url="postgresql://x/y")) == 2
    assert "set DASHBOARD_PASSWORD" in capsys.readouterr().err
