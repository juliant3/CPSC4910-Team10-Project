# To run tests: 
# 1 - Install dependencies: python -m pip install -r frontend/requirements-test.txt
# 2 - Run the test suite: python -m pytest

import sys
from pathlib import Path

import pytest

FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import app as site  # noqa: E402


class FakeCursor:
    """Small PyMySQL-style cursor used by route tests without a real database."""

    def __init__(self, fetchone_values=None, fetchall_values=None, lastrowid=1001):
        self.fetchone_values = list(fetchone_values or [])
        self.fetchall_values = list(fetchall_values or [])
        self.lastrowid = lastrowid
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        normalized = " ".join(str(query).split())
        self.executed.append((normalized, params))
        return 1

    def fetchone(self):
        if not self.fetchone_values:
            return None
        return self.fetchone_values.pop(0)

    def fetchall(self):
        if not self.fetchall_values:
            return []
        return self.fetchall_values.pop(0)


class FakeConnection:
    """Small PyMySQL-style connection used by route tests without XAMPP/RDS."""

    def __init__(self, fetchone_values=None, fetchall_values=None, lastrowid=1001):
        self._cursor = FakeCursor(fetchone_values, fetchall_values, lastrowid)
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


@pytest.fixture
def app(monkeypatch):
    site.app.config.update(
        TESTING=True,
        SECRET_KEY="test-secret-key",
    )

    # Login GET/failed-login rendering calls this helper. Stub it so the
    # automated suite never touches XAMPP or AWS RDS by accident.
    monkeypatch.setattr(site, "get_login_stats", lambda: (0, 0))

    yield site.app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def app_module():
    return site


@pytest.fixture
def fake_db(monkeypatch, app_module):
    """Factory that replaces app.get_db() with a scripted fake connection."""

    created = []

    def _install(fetchone_values=None, fetchall_values=None, lastrowid=1001):
        connection = FakeConnection(
            fetchone_values=fetchone_values,
            fetchall_values=fetchall_values,
            lastrowid=lastrowid,
        )
        created.append(connection)
        monkeypatch.setattr(app_module, "get_db", lambda: connection)
        return connection

    _install.created = created
    return _install


@pytest.fixture
def set_session(client):
    def _set(role="Driver", user_id=1, first_name="Test"):
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
            sess["first_name"] = first_name
            sess["role"] = role

    return _set
