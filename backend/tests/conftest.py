import sys
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

# Add frontend directory to Python path
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import app as app_module


@pytest.fixture
def flask_app():
    """Create a Flask application configured for testing."""
    app = app_module.app

    app.config.update(
        TESTING=True,
        SECRET_KEY="test-secret-key",
        WTF_CSRF_ENABLED=False,
    )

    yield app


@pytest.fixture
def client(flask_app):
    """Create a test client for making HTTP requests."""
    return flask_app.test_client()


@pytest.fixture
def password():
    """Provide a test password."""
    return "TestPassword123!"


@pytest.fixture
def password_hash(password):
    """Generate a hashed test password."""
    return generate_password_hash(password)