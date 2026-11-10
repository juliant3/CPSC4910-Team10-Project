"""Audit events are server-side, sanitized, and admin-only."""
import json


def audit_rows(conn):
    return [(q, params) for q, params in conn.cursor().executed
            if "INSERT INTO Audit_Logs" in q]


def test_successful_login_is_confirmed_and_password_not_logged(app, client, fake_db,
                                                                monkeypatch):
    from werkzeug.security import generate_password_hash
    monkeypatch.setitem(app.config, "AUDIT_LOGGING_ENABLED", True)
    conn = fake_db(fetchone_values=[{
        "user_id": 3, "first_name": "Lee", "last_name": "Test", "role": "Driver",
        "password_hash": generate_password_hash("DemoPass77!"),
    }])
    assert client.post("/login", data={"username": "lee@example.org",
                                       "password": "DemoPass77!"}).status_code == 302
    rows = audit_rows(conn)
    assert len(rows) == 1
    assert rows[0][1][3:5] == ("Login Attempt", "Login successful")
    assert rows[0][1][8] is True
    assert "DemoPass77!" not in str(rows)
    assert json.loads(rows[0][1][10])["login_method"] == "password"


def test_failed_login_creates_failure_event_without_password(app, client, fake_db,
                                                               monkeypatch):
    monkeypatch.setitem(app.config, "AUDIT_LOGGING_ENABLED", True)
    conn = fake_db(fetchone_values=[None])
    assert client.post("/login", data={"username": "bad@example.org",
                                       "password": "VerySecret!!"}).status_code == 200
    row = audit_rows(conn)[0][1]
    assert row[4] == "Login failed"
    assert row[8] is False
    assert "VerySecret!!" not in str(row)


def test_generic_post_logs_attempt_not_false_success(app, client, fake_db,
                                                       set_session, monkeypatch):
    monkeypatch.setitem(app.config, "AUDIT_LOGGING_ENABLED", True)
    set_session(role="Sponsor", user_id=8, sponsor_id=4)
    conn = fake_db(fetchone_values=[None])
    assert client.post("/sponsor/applications/99/delete").status_code == 302
    # Business action was rejected; no confirmed success recorded.
    rows = audit_rows(conn)
    assert len(rows) == 1
    assert rows[0][1][4].endswith(" attempted")
    assert rows[0][1][8] is None
    assert json.loads(rows[0][1][10])["target_id"] == 99


def test_logout_uses_original_actor_before_session_is_cleared(app, client,
                                                               fake_db, set_session,
                                                               monkeypatch):
    monkeypatch.setitem(app.config, "AUDIT_LOGGING_ENABLED", True)
    conn = fake_db()
    set_session(user_id=7)
    assert client.get("/logout").status_code == 302
    row = audit_rows(conn)[0][1]
    assert row[0] == 7
    assert row[4] == "Logout"


def test_admin_viewer_permissions(app, client, fake_db, set_session):
    fake_db(fetchall_values=[[]])
    set_session(role="Driver", user_id=4)
    assert client.get("/admin/audit-logs").status_code == 403
    set_session(role="Admin", user_id=1)
    response = client.get("/admin/audit-logs")
    assert response.status_code == 200
    assert b"Audit Logs" in response.data


def test_audit_failure_does_not_interrupt_request(app, client, set_session,
                                                  monkeypatch, caplog):
    monkeypatch.setitem(app.config, "AUDIT_LOGGING_ENABLED", True)
    # Deliberately omit fake_db: an audit-only logout will have no connection.
    set_session(role="Driver", user_id=9)
    assert client.get("/logout").status_code == 302
    assert "Audit log write failed" in caplog.text
