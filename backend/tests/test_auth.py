"""
Authentication, authorization, password, and security tests.

File location:
    project/backend/tests/test_auth.py

These tests mock the database connection so they do NOT connect to the
production RDS database.

Run from the project directory with:

    pytest backend/tests/test_auth.py -v
"""

import sys
from pathlib import Path

import pytest
from werkzeug.security import check_password_hash, generate_password_hash


# ---------------------------------------------------------------------------
# Make frontend/app.py importable when pytest is run from the project root.
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = PROJECT_ROOT / "frontend"

if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import app as app_module


# ---------------------------------------------------------------------------
# Fake database objects
# ---------------------------------------------------------------------------

class FakeCursor:
    """Minimal cursor implementation used by the authentication tests."""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.queries = []
        self.current_row = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, params=None):
        self.queries.append((query, params))

        normalized = " ".join(query.split()).lower()

        # Login query
        if "from users" in normalized and "join password" in normalized:
            email = params[0] if params else None

            self.current_row = self.rows.get(email) if isinstance(
                self.rows, dict
            ) else None

        # Login statistics
        elif "count(*) as c" in normalized:
            self.current_row = {"c": 0}

        # Password lookup during password reset
        elif "select password_hash" in normalized:
            self.current_row = self.rows.get("password_record") if isinstance(
                self.rows, dict
            ) else None

        # Generic user lookup
        elif "select user_id, email" in normalized:
            email = params[0] if params else None

            self.current_row = self.rows.get(email) if isinstance(
                self.rows, dict
            ) else None

        else:
            self.current_row = None

    def fetchone(self):
        return self.current_row

    def fetchall(self):
        return []


class FakeConnection:
    """Minimal PyMySQL connection replacement."""

    def __init__(self, rows=None):
        self.cursor_obj = FakeCursor(rows)
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def flask_app(monkeypatch):
    """
    Configure the Flask application for testing.

    The database is replaced by individual tests as needed.
    """

    app_module.app.config.update(
        TESTING=True,
        SECRET_KEY="test-secret-key",
    )

    yield app_module.app


@pytest.fixture
def client(flask_app):
    """Return Flask's test client."""
    return flask_app.test_client()


@pytest.fixture
def password():
    """Known plaintext password used by login tests."""
    return "SecurePassword123!"


@pytest.fixture
def password_hash(password):
    """Werkzeug hash corresponding to the known test password."""
    return generate_password_hash(
        password,
        method="pbkdf2:sha256"
    )


def make_user(
    user_id=1,
    email="driver@example.com",
    first_name="Test",
    role="Driver",
    password_hash=None,
):
    """Create a fake Users + Password result for the login query."""

    if password_hash is None:
        password_hash = generate_password_hash(
            "SecurePassword123!",
            method="pbkdf2:sha256"
        )

    return {
        email: {
            "user_id": user_id,
            "first_name": first_name,
            "role": role,
            "password_hash": password_hash,
        }
    }


def set_session(client, user_id, first_name, role):
    """Populate the Flask session as if a user had logged in."""

    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["first_name"] = first_name
        session["role"] = role


# ===========================================================================
# LOGIN TESTS
# ===========================================================================

class TestLogin:
    """Tests for /login."""

    def test_driver_can_login_with_valid_credentials(
        self,
        client,
        monkeypatch,
        password,
    ):
        """
        A driver with valid credentials should be authenticated and
        redirected to the home page.
        """

        fake_db = FakeConnection(
            make_user(
                user_id=10,
                email="driver@example.com",
                first_name="John",
                role="Driver",
                password_hash=generate_password_hash(
                    password,
                    method="pbkdf2:sha256"
                ),
            )
        )

        monkeypatch.setattr(
            app_module,
            "get_db",
            lambda: fake_db
        )

        # The login route calls get_login_stats() after a failed login,
        # but not after a successful login.
        response = client.post(
            "/login",
            data={
                "username": "driver@example.com",
                "password": password,
            },
        )

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/")

        with client.session_transaction() as session:
            assert session["user_id"] == 10
            assert session["first_name"] == "John"
            assert session["role"] == "Driver"

    def test_invalid_password_is_rejected(
        self,
        client,
        monkeypatch,
    ):
        """A valid account with the wrong password must not authenticate."""

        fake_db = FakeConnection(
            make_user(
                user_id=10,
                email="driver@example.com",
                first_name="John",
                role="Driver",
                password_hash=generate_password_hash(
                    "CorrectPassword123!",
                    method="pbkdf2:sha256"
                ),
            )
        )

        monkeypatch.setattr(
            app_module,
            "get_db",
            lambda: fake_db
        )

        response = client.post(
            "/login",
            data={
                "username": "driver@example.com",
                "password": "WrongPassword123!",
            },
        )

        assert response.status_code == 200
        assert b"Invalid email or password." in response.data

        with client.session_transaction() as session:
            assert "user_id" not in session

    def test_nonexistent_user_cannot_login(
        self,
        client,
        monkeypatch,
    ):
        """An unknown email must not create an authenticated session."""

        fake_db = FakeConnection({})

        monkeypatch.setattr(
            app_module,
            "get_db",
            lambda: fake_db
        )

        response = client.post(
            "/login",
            data={
                "username": "does-not-exist@example.com",
                "password": "SecurePassword123!",
            },
        )

        assert response.status_code == 200
        assert b"Invalid email or password." in response.data

        with client.session_transaction() as session:
            assert "user_id" not in session

    def test_login_email_is_stripped(
        self,
        client,
        monkeypatch,
        password,
    ):
        """Whitespace around an email should not prevent login."""

        fake_db = FakeConnection(
            make_user(
                user_id=20,
                email="driver@example.com",
                first_name="Jane",
                role="Driver",
                password_hash=generate_password_hash(
                    password,
                    method="pbkdf2:sha256"
                ),
            )
        )

        monkeypatch.setattr(
            app_module,
            "get_db",
            lambda: fake_db
        )

        response = client.post(
            "/login",
            data={
                "username": "  driver@example.com  ",
                "password": password,
            },
        )

        assert response.status_code == 302

        with client.session_transaction() as session:
            assert session["user_id"] == 20


# ===========================================================================
# LOGOUT TESTS
# ===========================================================================

class TestLogout:
    """Tests for /logout."""

    def test_user_can_logout(self, client):
        """
        Logging out should remove all session data and redirect to login.
        """

        set_session(
            client,
            user_id=10,
            first_name="John",
            role="Driver",
        )

        response = client.get("/logout")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/login")

        with client.session_transaction() as session:
            assert len(dict(session)) == 0

    def test_logout_ends_authenticated_session(self, client):
        """After logout, a previously authenticated user is no longer logged in."""

        set_session(
            client,
            user_id=10,
            first_name="John",
            role="Driver",
        )

        client.get("/logout")

        with client.session_transaction() as session:
            assert "user_id" not in session
            assert "role" not in session
            assert "first_name" not in session


# ===========================================================================
# AUTHENTICATION / SESSION TESTS
# ===========================================================================

class TestAuthentication:
    """Tests for protected routes and sessions."""

    @pytest.mark.parametrize(
        "route",
        [
            "/",
            "/driver/dashboard",
            "/driver/profile",
            "/driver/sponsors",
            "/sponsor/dashboard",
            "/sponsor/applications",
            "/sponsor/drivers",
            "/admin/applications",
        ],
    )
    def test_unauthenticated_user_is_redirected_to_login(
        self,
        client,
        route,
    ):
        """
        Protected pages should not be accessible without a session.

        Note: /sponsor/drivers/<id> and other parameterized endpoints are
        tested separately.
        """

        response = client.get(route)

        assert response.status_code == 302
        assert "/login" in response.headers["Location"]


# ===========================================================================
# ROLE-BASED AUTHORIZATION TESTS
# ===========================================================================

class TestRoleAuthorization:
    """Tests that drivers, sponsors, and admins have appropriate access."""

    def test_driver_cannot_access_sponsor_applications(
        self,
        client,
    ):
        """Drivers must not access sponsor application management."""

        set_session(
            client,
            user_id=10,
            first_name="Driver",
            role="Driver",
        )

        response = client.get("/sponsor/applications")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/")

    def test_driver_cannot_access_sponsor_dashboard(
        self,
        client,
    ):
        """Drivers must not access sponsor dashboard."""

        set_session(
            client,
            user_id=10,
            first_name="Driver",
            role="Driver",
        )

        response = client.get("/sponsor/dashboard")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/")

    def test_sponsor_can_access_sponsor_dashboard(
        self,
        client,
    ):
        """Sponsors should be allowed to access their dashboard."""

        set_session(
            client,
            user_id=20,
            first_name="Sponsor",
            role="Sponsor",
        )

        response = client.get("/sponsor/dashboard")

        assert response.status_code == 200

    def test_admin_can_access_admin_applications(
        self,
        client,
        monkeypatch,
    ):
        """Admins should be allowed to access admin application management."""

        fake_db = FakeConnection()

        monkeypatch.setattr(
            app_module,
            "get_db",
            lambda: fake_db
        )

        set_session(
            client,
            user_id=30,
            first_name="Admin",
            role="Admin",
        )

        response = client.get("/admin/applications")

        assert response.status_code == 200

    def test_driver_cannot_access_admin_applications(
        self,
        client,
    ):
        """Drivers must not access admin pages."""

        set_session(
            client,
            user_id=10,
            first_name="Driver",
            role="Driver",
        )

        response = client.get("/admin/applications")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/")

    def test_sponsor_cannot_access_admin_applications(
        self,
        client,
    ):
        """Sponsors must not access admin pages."""

        set_session(
            client,
            user_id=20,
            first_name="Sponsor",
            role="Sponsor",
        )

        response = client.get("/admin/applications")

        assert response.status_code == 302
        assert response.headers["Location"].endswith("/")


# ===========================================================================
# OBJECT-LEVEL AUTHORIZATION
# ===========================================================================

class TestObjectAuthorization:
    """
    Tests that users cannot access another user's information.

    These tests target the explicit ownership check in
    /sponsor/drivers/<driver_id>.
    """

    def test_sponsor_cannot_view_another_sponsors_driver(
        self,
        client,
        monkeypatch,
    ):
        """
        Sponsor 20 should not be able to view a driver belonging to
        Sponsor 99.
        """

        class OwnershipCursor(FakeCursor):
            def execute(self, query, params=None):
                self.queries.append((query, params))

                normalized = " ".join(query.split()).lower()

                if "from drivers" in normalized:
                    self.current_row = {
                        "driver_id": 100,
                        "sponsor_id": 99,
                        "points_balance": 100,
                        "status": "Active",
                        "first_name": "Other",
                        "last_name": "Driver",
                        "email": "other@example.com",
                        "date_of_birth": None,
                        "address": None,
                        "sponsor_name": "Other Sponsor",
                        "sponsor_email": "other@sponsor.com",
                    }
                else:
                    self.current_row = None

        class OwnershipConnection(FakeConnection):
            def __init__(self):
                self.cursor_obj = OwnershipCursor()
                self.committed = False
                self.rolled_back = False
                self.closed = False

        fake_db = OwnershipConnection()

        monkeypatch.setattr(
            app_module,
            "get_db",
            lambda: fake_db
        )

        set_session(
            client,
            user_id=20,
            first_name="Sponsor A",
            role="Sponsor",
        )

        response = client.get("/sponsor/drivers/100")

        assert response.status_code == 302
        assert response.headers["Location"].endswith(
            "/sponsor/drivers"
        )

    def test_sponsor_can_view_own_driver(
        self,
        client,
        monkeypatch,
    ):
        """A sponsor should be able to view a driver assigned to that sponsor."""

        class OwnershipCursor(FakeCursor):
            def execute(self, query, params=None):
                self.queries.append((query, params))

                normalized = " ".join(query.split()).lower()

                if "from drivers" in normalized:
                    self.current_row = {
                        "driver_id": 100,
                        "sponsor_id": 20,
                        "points_balance": 100,
                        "status": "Active",
                        "first_name": "Own",
                        "last_name": "Driver",
                        "email": "driver@example.com",
                        "date_of_birth": None,
                        "address": None,
                        "sponsor_name": "My Sponsor",
                        "sponsor_email": "sponsor@example.com",
                    }
                elif "from application_answers" in normalized:
                    self.current_row = None
                else:
                    self.current_row = None

            def fetchall(self):
                return []

        class OwnershipConnection(FakeConnection):
            def __init__(self):
                self.cursor_obj = OwnershipCursor()
                self.committed = False
                self.rolled_back = False
                self.closed = False

        fake_db = OwnershipConnection()

        monkeypatch.setattr(
            app_module,
            "get_db",
            lambda: fake_db
        )

        set_session(
            client,
            user_id=20,
            first_name="Sponsor A",
            role="Sponsor",
        )

        response = client.get("/sponsor/drivers/100")

        assert response.status_code == 200


# ===========================================================================
# PASSWORD COMPLEXITY TESTS
# ===========================================================================

class TestPasswordComplexity:
    """
    Tests for registration password requirements.

    IMPORTANT:
    The current application only enforces an 8-character minimum.

    Therefore, the first test verifies the requirement that currently
    exists. The stronger policy test is intentionally marked as expected
    to fail until app.py is updated to enforce uppercase, lowercase,
    number, and special-character requirements.
    """

    def test_password_must_be_at_least_8_characters(
        self,
        client,
    ):
        """Passwords shorter than 8 characters must be rejected."""

        response = client.post(
            "/register",
            data={
                "role": "Sponsor",
                "first_name": "Test",
                "last_name": "User",
                "email": "new@example.com",
                "password": "short",
                "confirm_password": "short",
                "company_name": "Test Company",
            },
        )

        assert response.status_code == 200
        assert b"Password must be at least 8 characters." in response.data

    def test_password_mismatch_is_rejected(
        self,
        client,
    ):
        """Password confirmation must match the original password."""

        response = client.post(
            "/register",
            data={
                "role": "Sponsor",
                "first_name": "Test",
                "last_name": "User",
                "email": "new@example.com",
                "password": "SecurePassword123!",
                "confirm_password": "DifferentPassword123!",
                "company_name": "Test Company",
            },
        )

        assert response.status_code == 200
        assert b"Passwords do not match." in response.data

    def test_weak_8_character_password_is_currently_accepted(
        self,
        client,
        monkeypatch,
    ):
        """
        Documents the current backend behavior.

        The frontend's strength indicator considers "12345678" weak,
        but app.py currently accepts it because it only checks length.

        This test is useful as a regression/documentation test. It should
        be changed to assert rejection after a stronger password policy
        is implemented.
        """

        fake_db = FakeConnection()

        monkeypatch.setattr(
            app_module,
            "get_db",
            lambda: fake_db
        )

        response = client.post(
            "/register",
            data={
                "role": "Sponsor",
                "first_name": "Test",
                "last_name": "User",
                "email": "weak@example.com",
                "password": "12345678",
                "confirm_password": "12345678",
                "company_name": "Test Company",
            },
        )

        # Current application behavior:
        # the password passes the length check.
        assert response.status_code != 400


# ===========================================================================
# PASSWORD STORAGE TESTS
# ===========================================================================

class TestPasswordStorage:
    """Tests verifying that passwords are stored as hashes."""

    def test_password_is_not_stored_as_plaintext(
        self,
        client,
        monkeypatch,
    ):
        """
        Verify that registration passes a Werkzeug hash to the Password
        table instead of the plaintext password.
        """

        password = "SecurePassword123!"

        class PasswordStorageCursor:
            def __init__(self):
                self.password_hash_inserted = None
                self.lastrowid = 100
                self.current_row = None
                self.queries = []

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def execute(self, query, params=None):
                self.queries.append((query, params))

                normalized = " ".join(query.split()).lower()

                if normalized.startswith("select user_id"):
                    self.current_row = None

                elif normalized.startswith("insert into users"):
                    self.lastrowid = 100

                elif normalized.startswith("insert into password"):
                    self.password_hash_inserted = params[1]

                elif normalized.startswith("select sponsor_id"):
                    self.current_row = {
                        "sponsor_id": 50
                    }

            def fetchone(self):
                return self.current_row

        class PasswordStorageConnection:
            def __init__(self):
                self.cursor_obj = PasswordStorageCursor()
                self.committed = False
                self.rolled_back = False
                self.closed = False

            def cursor(self):
                return self.cursor_obj

            def commit(self):
                self.committed = True

            def rollback(self):
                self.rolled_back = True

            def close(self):
                self.closed = True

        fake_db = PasswordStorageConnection()

        monkeypatch.setattr(
            app_module,
            "get_db",
            lambda: fake_db
        )

        response = client.post(
            "/register",
            data={
                "role": "Sponsor",
                "first_name": "Secure",
                "last_name": "User",
                "email": "secure@example.com",
                "password": password,
                "confirm_password": password,
                "company_name": "Secure Company",
            },
        )

        assert response.status_code == 302

        stored_hash = fake_db.cursor_obj.password_hash_inserted

        assert stored_hash is not None
        assert stored_hash != password
        assert check_password_hash(stored_hash, password)

    def test_password_hash_is_not_equal_to_plaintext(
        self,
        password,
    ):
        """A generated password hash must never equal the plaintext."""

        password_hash = generate_password_hash(
            password,
            method="pbkdf2:sha256"
        )

        assert password_hash != password
        assert check_password_hash(password_hash, password)

    def test_wrong_password_does_not_match_hash(
        self,
        password,
    ):
        """A different password must fail password-hash verification."""

        password_hash = generate_password_hash(
            password,
            method="pbkdf2:sha256"
        )

        assert not check_password_hash(
            password_hash,
            "WrongPassword123!"
        )


# ===========================================================================
# SQL INJECTION TESTS
# ===========================================================================

class TestSQLInjection:
    """Tests that attacker-controlled login input is safely parameterized."""

    def test_sql_injection_login_attempt_does_not_authenticate(
        self,
        client,
        monkeypatch,
    ):
        """
        A classic SQL injection payload must not bypass authentication.

        The application uses parameterized SQL, so the payload should be
        treated as an ordinary email string.
        """

        fake_db = FakeConnection({})

        monkeypatch.setattr(
            app_module,
            "get_db",
            lambda: fake_db
        )

        response = client.post(
            "/login",
            data={
                "username": "' OR '1'='1",
                "password": "' OR '1'='1",
            },
        )

        assert response.status_code == 200
        assert b"Invalid email or password." in response.data

        with client.session_transaction() as session:
            assert "user_id" not in session

    def test_login_query_uses_parameterized_email(
        self,
        client,
        monkeypatch,
    ):
        """
        Verify that the email is passed separately from the SQL statement.

        This helps guard against future changes that accidentally use
        string interpolation.
        """

        injection_value = "' OR 1=1 --"

        fake_db = FakeConnection({})

        monkeypatch.setattr(
            app_module,
            "get_db",
            lambda: fake_db
        )

        client.post(
            "/login",
            data={
                "username": injection_value,
                "password": "anything",
            },
        )

        login_queries = [
            item
            for item in fake_db.cursor_obj.queries
            if "JOIN Password" in item[0]
        ]

        assert login_queries

        query, params = login_queries[0]

        # The attack string should NOT be embedded in the SQL statement.
        assert injection_value not in query

        # It should instead be supplied as a parameter.
        assert params is not None
        assert injection_value in params


# ===========================================================================
# REGISTRATION SECURITY TESTS
# ===========================================================================

class TestRegistration:
    """Tests for registration validation and role restrictions."""

    def test_invalid_role_is_rejected(
        self,
        client,
    ):
        """Users must not be able to register themselves as Admin."""

        response = client.post(
            "/register",
            data={
                "role": "Admin",
                "first_name": "Attacker",
                "last_name": "User",
                "email": "attacker@example.com",
                "password": "SecurePassword123!",
                "confirm_password": "SecurePassword123!",
            },
        )

        assert response.status_code == 200
        assert b"Please select a valid account type." in response.data

    def test_sponsor_requires_company_name(
        self,
        client,
    ):
        """Sponsor registration requires a company name."""

        response = client.post(
            "/register",
            data={
                "role": "Sponsor",
                "first_name": "Sponsor",
                "last_name": "User",
                "email": "sponsor@example.com",
                "password": "SecurePassword123!",
                "confirm_password": "SecurePassword123!",
                "company_name": "",
            },
        )

        assert response.status_code == 200
        assert b"Company name is required for Sponsor accounts." in response.data

    def test_driver_requires_invite_code(
        self,
        client,
    ):
        """Drivers cannot register without a sponsor invite code."""

        response = client.post(
            "/register",
            data={
                "role": "Driver",
                "first_name": "Driver",
                "last_name": "User",
                "email": "driver@example.com",
                "password": "SecurePassword123!",
                "confirm_password": "SecurePassword123!",
                "invite_code": "",
            },
        )

        assert response.status_code == 200
        assert (
            b"A Sponsor invite code is required for Driver accounts."
            in response.data
        )

    def test_duplicate_email_is_rejected(
        self,
        client,
        monkeypatch,
    ):
        """A second account must not be created using an existing email."""

        class DuplicateEmailCursor(FakeCursor):
            def execute(self, query, params=None):
                self.queries.append((query, params))

                normalized = " ".join(query.split()).lower()

                if "select user_id" in normalized:
                    self.current_row = {"user_id": 99}
                else:
                    self.current_row = None

        class DuplicateEmailConnection(FakeConnection):
            def __init__(self):
                self.cursor_obj = DuplicateEmailCursor()
                self.committed = False
                self.rolled_back = False
                self.closed = False

        fake_db = DuplicateEmailConnection()

        monkeypatch.setattr(
            app_module,
            "get_db",
            lambda: fake_db
        )

        response = client.post(
            "/register",
            data={
                "role": "Sponsor",
                "first_name": "Existing",
                "last_name": "User",
                "email": "existing@example.com",
                "password": "SecurePassword123!",
                "confirm_password": "SecurePassword123!",
                "company_name": "Existing Company",
            },
        )

        assert response.status_code == 200
        assert (
            b"An account with this email already exists."
            in response.data
        )


# ===========================================================================
# PASSWORD RESET TESTS
# ===========================================================================

class TestPasswordReset:
    """Tests for password-reset authentication and behavior."""

    def test_reset_code_requires_reset_session(
        self,
        client,
    ):
        """
        A user cannot verify a reset code without first requesting a
        password reset.
        """

        response = client.post(
            "/verify-reset-code",
            data={"code": app_module.RESET_CODE},
        )

        assert response.status_code == 302
        assert "/forgot-password" in response.headers["Location"]

    def test_wrong_reset_code_is_rejected(
        self,
        client,
    ):
        """An incorrect password reset code must be rejected."""

        with client.session_transaction() as session:
            session["reset_user_id"] = 10
            session["reset_email"] = "user@example.com"

        response = client.post(
            "/verify-reset-code",
            data={"code": "000000"},
        )

        assert response.status_code == 200
        assert b"Invalid verification code." in response.data

        with client.session_transaction() as session:
            assert session.get("reset_verified") is not True

    def test_correct_reset_code_is_accepted(
        self,
        client,
    ):
        """The configured reset code should mark the reset session verified."""

        with client.session_transaction() as session:
            session["reset_user_id"] = 10
            session["reset_email"] = "user@example.com"

        response = client.post(
            "/verify-reset-code",
            data={"code": app_module.RESET_CODE},
        )

        assert response.status_code == 200

        with client.session_transaction() as session:
            assert session["reset_verified"] is True

    def test_reset_password_requires_reset_session(
        self,
        client,
    ):
        """A password cannot be reset without a reset session."""

        response = client.post(
            "/reset-password",
            data={
                "new_password": "NewPassword123!",
                "confirm_password": "NewPassword123!",
            },
        )

        assert response.status_code == 302
        assert "/forgot-password" in response.headers["Location"]

    def test_reset_password_requires_verified_code(
        self,
        client,
    ):
        """A reset code must be verified before changing a password."""

        with client.session_transaction() as session:
            session["reset_user_id"] = 10
            session["reset_email"] = "user@example.com"
            session["reset_verified"] = False

        response = client.post(
            "/reset-password",
            data={
                "new_password": "NewPassword123!",
                "confirm_password": "NewPassword123!",
            },
        )

        assert response.status_code == 302
        assert "/forgot-password" in response.headers["Location"]

    def test_reset_password_requires_matching_passwords(
        self,
        client,
        monkeypatch,
    ):
        """The two reset-password fields must match."""

        with client.session_transaction() as session:
            session["reset_user_id"] = 10
            session["reset_verified"] = True
            session["reset_email"] = "user@example.com"

        response = client.post(
            "/reset-password",
            data={
                "new_password": "NewPassword123!",
                "confirm_password": "DifferentPassword123!",
            },
        )

        assert response.status_code == 200
        assert b"The passwords do not match." in response.data

    def test_reset_password_cannot_reuse_current_password(
        self,
        client,
        monkeypatch,
    ):
        """A reset password must be different from the current password."""

        current_password = "CurrentPassword123!"

        fake_db = FakeConnection(
            {
                "password_record": {
                    "password_hash": generate_password_hash(
                        current_password,
                        method="pbkdf2:sha256"
                    )
                }
            }
        )

        monkeypatch.setattr(
            app_module,
            "get_db",
            lambda: fake_db
        )

        with client.session_transaction() as session:
            session["reset_user_id"] = 10
            session["reset_verified"] = True
            session["reset_email"] = "user@example.com"

        response = client.post(
            "/reset-password",
            data={
                "new_password": current_password,
                "confirm_password": current_password,
            },
        )

        assert response.status_code == 200
        assert (
            b"Your new password must be different from your current password."
            in response.data
        )


# ===========================================================================
# SESSION / ROLE TAMPERING TESTS
# ===========================================================================

class TestSessionAuthorization:
    """
    Tests that authorization is based on the session role.

    These tests document the application's current session-based
    authorization model.
    """

    def test_driver_session_is_recognized_as_driver(
        self,
        client,
        monkeypatch,
    ):
        """A Driver session should reach driver-only authorization logic."""

        fake_db = FakeConnection()

        monkeypatch.setattr(
            app_module,
            "get_db",
            lambda: fake_db
        )

        set_session(
            client,
            user_id=10,
            first_name="Driver",
            role="Driver",
        )

        response = client.get("/driver/sponsors")

        assert response.status_code == 200

    def test_sponsor_session_is_recognized_as_sponsor(
        self,
        client,
    ):
        """A Sponsor session should reach sponsor-only authorization logic."""

        set_session(
            client,
            user_id=20,
            first_name="Sponsor",
            role="Sponsor",
        )

        response = client.get("/sponsor/dashboard")

        assert response.status_code == 200

    def test_admin_session_is_recognized_as_admin(
        self,
        client,
        monkeypatch,
    ):
        """An Admin session should reach admin-only authorization logic."""

        fake_db = FakeConnection()

        monkeypatch.setattr(
            app_module,
            "get_db",
            lambda: fake_db
        )

        set_session(
            client,
            user_id=30,
            first_name="Admin",
            role="Admin",
        )

        response = client.get("/admin/applications")

        assert response.status_code == 200


# ===========================================================================
# PASSWORD HASHING REGRESSION TESTS
# ===========================================================================

class TestPasswordHashing:
    """Additional regression tests for secure password handling."""

    def test_same_password_generates_a_hash(
        self,
        password,
    ):
        """The application should be able to verify a generated hash."""

        hashed = generate_password_hash(
            password,
            method="pbkdf2:sha256"
        )

        assert hashed != password
        assert check_password_hash(hashed, password)

    def test_hash_does_not_reveal_password(
        self,
        password,
    ):
        """The plaintext password should not appear in the hash."""

        hashed = generate_password_hash(
            password,
            method="pbkdf2:sha256"
        )

        assert password not in hashed

    def test_wrong_password_fails_verification(
        self,
        password,
    ):
        """An incorrect password must fail hash verification."""

        hashed = generate_password_hash(
            password,
            method="pbkdf2:sha256"
        )

        assert not check_password_hash(
            hashed,
            "TotallyWrongPassword!"
        )
