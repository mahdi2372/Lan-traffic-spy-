"""Shared test helpers and fixtures."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture()
def db(tmp_path):
    from lanwatcher.db import Database

    database = Database(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture()
def repos(db):
    from lanwatcher.db import Repositories

    return Repositories(db)


@pytest.fixture()
def bus():
    from lanwatcher.events import EventBus

    return EventBus()


@pytest.fixture()
def config(tmp_path):
    from lanwatcher.config import Config

    os.environ["LANWATCHER_HOME"] = str(tmp_path / "home")
    return Config(tmp_path / "home" / "config.json")
