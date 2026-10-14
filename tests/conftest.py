from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import connect, init_db
from app.main import create_app
from app.sync import write_clients, write_networks
from app.unifi_adapter import parse_clients, parse_gear, parse_networks

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def networks():
    return parse_networks(load("legacy_networkconf.json"))


@pytest.fixture
def clients():
    return parse_clients(load("legacy_rest_user.json"), load("legacy_stat_sta.json"))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        unifi_url="",
        unifi_api_key="",
        unifi_site="default",
        sync_interval_seconds=0,  # no background sync in tests
        db_path=str(tmp_path / "test.db"),
        verify_tls=False,
        stale_days=14,
    )


@pytest.fixture
def conn(settings: Settings, networks, clients) -> sqlite3.Connection:
    """DB seeded from fixtures, exactly as a sync would write it."""
    init_db(settings.db_path)
    c = connect(settings.db_path)
    with c:
        write_networks(c, networks)
        write_clients(c, clients + parse_gear(load("legacy_stat_device.json")))  # as a sync does
    yield c
    c.close()


@pytest.fixture
def client(settings: Settings, conn: sqlite3.Connection) -> TestClient:
    app = create_app(settings)
    with TestClient(app, follow_redirects=False) as tc:
        yield tc
