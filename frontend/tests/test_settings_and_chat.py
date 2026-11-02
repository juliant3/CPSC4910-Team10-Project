"""Settings (notifications, name, password) and sponsor/driver chat."""

from datetime import datetime

import pytest
from werkzeug.security import check_password_hash, generate_password_hash


def sql(conn):
    return conn.cursor().executed


def flashes(client):
    with client.session_transaction() as sess:
        return [message for _, message in sess.get("_flashes", [])]


# ------------------------------------------------------------------
# Notification settings
# ------------------------------------------------------------------

def test_driver_sees_only_driver_toggles(client, set_session, fake_db):
    fake_db(fetchone_values=[{"user_id": 20, "notify_points": 0}])
    set_session(role="Driver", user_id=20)

    html = client.get("/settings").data

    assert b'name="notify_points"' in html and b'name="notify_messages"' in html
    assert b'name="notify_applications"' not in html
    assert b'id="notify_points" name="notify_points" value="1" >' in html  # saved as off
    assert b'id="notify_orders" name="notify_orders" value="1" checked' in html  # default on


def test_saving_toggles_only_writes_the_roles_columns(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/settings", data={"action": "notifications", "notify_messages": "1",
                                   "notify_points": "1"})  # not a sponsor toggle

    query, params = sql(conn)[0]
    assert "INSERT INTO User_Settings" in query
    assert "notify_points" not in query
    assert params == (50, 0, 1)  # notify_applications off, notify_messages on
    assert conn.committed


def test_bell_hides_muted_types_and_treats_missing_settings_as_on(app_module):
    muted = app_module.MUTED_NOTIFICATIONS_SQL
    assert "COALESCE(User_Settings.notify_points, 1) = 0" in muted
    assert "'Point Change'" in muted and "'Message'" in muted


def test_bell_query_uses_the_mute_filter(client, set_session, fake_db, app_module):
    conn = fake_db(
        fetchone_values=[{"company_name": "Acme"}, {"c": 0}],
        fetchall_values=[[], [], []],
    )
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.get("/sponsor/team")

    bell = [q for q, _ in sql(conn) if "FROM Notifications" in q]
    assert len(bell) == 2
    assert all(app_module.MUTED_NOTIFICATIONS_SQL.split(" OR ")[0] in q for q in bell)


# ------------------------------------------------------------------
# Name and password
# ------------------------------------------------------------------

def test_name_change_updates_user_and_header(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Driver", user_id=20, first_name="Dana", last_name="Diaz")

    client.post("/settings", data={"action": "profile", "first_name": "  Dana  ", "last_name": "Diaz-Ruiz"})

    assert sql(conn)[0][1] == ("Dana", "Diaz-Ruiz", 20)
    with client.session_transaction() as sess:
        assert sess["last_name"] == "Diaz-Ruiz"


def test_blank_name_is_rejected(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Driver", user_id=20)
    client.post("/settings", data={"action": "profile", "first_name": " ", "last_name": "x"})
    assert sql(conn) == []


@pytest.mark.parametrize("form, message", [
    ({"current_password": "wrong", "new_password": "NewPassword1!", "confirm_password": "NewPassword1!"}, "incorrect"),
    ({"current_password": "OldPassword1!", "new_password": "short", "confirm_password": "short"}, "at least 8"),
    ({"current_password": "OldPassword1!", "new_password": "NewPassword1!", "confirm_password": "Other1234!"}, "do not match"),
    ({"current_password": "OldPassword1!", "new_password": "OldPassword1!", "confirm_password": "OldPassword1!"}, "different"),
])
def test_bad_password_changes_are_rejected(client, set_session, fake_db, form, message):
    conn = fake_db(fetchone_values=[{"password_hash": generate_password_hash("OldPassword1!")}])
    set_session(role="Driver", user_id=20)

    client.post("/settings", data={"action": "password", **form})

    assert not any(q.startswith("UPDATE Password ") for q, _ in sql(conn))
    assert not conn.committed
    assert any(message in m for m in flashes(client))


def test_password_change_saves_hash_and_cancels_reset_links(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[{"password_hash": generate_password_hash("OldPassword1!")}])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/settings", data={"action": "password", "current_password": "OldPassword1!",
                                   "new_password": "NewPassword1!", "confirm_password": "NewPassword1!"})

    update = next(p for q, p in sql(conn) if q.startswith("UPDATE Password SET"))
    assert check_password_hash(update[0], "NewPassword1!") and update[1] == 50
    assert any(q.startswith("UPDATE Password_Tokens") for q, _ in sql(conn))
    assert any("INSERT INTO Audit_Logs" in q for q, _ in sql(conn))
    assert conn.committed


def test_settings_require_login(client):
    assert client.get("/settings").status_code == 302


# ------------------------------------------------------------------
# Chat
# ------------------------------------------------------------------

MEMBERSHIP = {"status": "Active", "first_name": "Dana", "last_name": "Diaz", "company_name": "Acme"}


def message(message_id, sender, body="Hi"):
    return {"message_id": message_id, "body": body, "created_at": datetime(2026, 11, 2, 15, 0),
            "sender_user_id": sender, "first_name": "Sam" if sender != 20 else "Dana", "last_name": "Lee"}


def test_driver_sends_message_to_sponsor_and_sponsor_team_is_notified(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[MEMBERSHIP, {"c": 1}])
    set_session(role="Driver", user_id=20, first_name="Dana", last_name="Diaz")

    response = client.post("/messages/3", data={"body": "  When do points post?  "})

    assert response.status_code == 302
    insert = next(p for q, p in sql(conn) if q.startswith("INSERT INTO Messages"))
    assert insert == (20, 3, 20, "When do points post?")
    note_query, note_params = next((q, p) for q, p in sql(conn) if "INSERT INTO Notifications" in q)
    assert "Sponsor_Users" in note_query and note_params[2:] == (3, 3)
    assert conn.committed


def test_no_new_notification_while_one_is_still_unread(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[MEMBERSHIP, {"c": 2}])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/messages/20", data={"body": "Following up"})

    insert = next(p for q, p in sql(conn) if q.startswith("INSERT INTO Messages"))
    assert insert == (20, 3, 50, "Following up")
    assert not any("INSERT INTO Notifications" in q for q, _ in sql(conn))


@pytest.mark.parametrize("body", ["", "   ", "x" * 2001])
def test_empty_or_long_messages_are_rejected(client, set_session, fake_db, body):
    conn = fake_db(fetchone_values=[MEMBERSHIP])
    set_session(role="Driver", user_id=20)
    client.post("/messages/3", data={"body": body})
    assert not any(q.startswith("INSERT INTO Messages") for q, _ in sql(conn))


def test_cannot_message_outside_a_program(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[None])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    response = client.post("/messages/99", data={"body": "hi"})

    assert response.status_code == 404
    assert sql(conn)[0][1] == (99, 3)
    assert not any(q.startswith("INSERT INTO Messages") for q, _ in sql(conn))


def test_inactive_program_can_be_read_but_not_written(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[dict(MEMBERSHIP, status="Dropped")])
    set_session(role="Driver", user_id=20)
    client.post("/messages/3", data={"body": "hi"})
    assert not any(q.startswith("INSERT INTO Messages") for q, _ in sql(conn))

    fake_db(fetchone_values=[dict(MEMBERSHIP, status="Dropped")], fetchall_values=[[message(1, 50)]])
    html = client.get("/messages/3").data
    assert b"isn&#39;t active" in html or b"isn't active" in html


def test_opening_conversation_shows_messages_and_marks_them_read(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[MEMBERSHIP],
                   fetchall_values=[[message(2, 50, "<b>hello</b>"), message(1, 20)]])
    set_session(role="Driver", user_id=20)

    html = client.get("/messages/3").data

    assert b"&lt;b&gt;hello&lt;/b&gt;" in html  # escaped, not rendered
    assert b'data-last-id="2"' in html
    mark = next(q for q, _ in sql(conn) if q.startswith("UPDATE Messages"))
    assert "sender_user_id <> Messages.driver_id" in mark


def test_support_view_never_marks_messages_read(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[MEMBERSHIP], fetchall_values=[[message(1, 50)]])
    set_session(role="Driver", user_id=20, assumed_driver_id=20, assumed_driver_by="admin",
                _assume_driver_original={"user_id": 1})

    html = client.get("/messages/3").data

    assert b"Read-only" in html and b'id="chat-form"' not in html
    assert not any(q.startswith("UPDATE Messages") for q, _ in sql(conn))


def test_sponsor_driver_view_only_sees_its_own_conversation(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Driver", user_id=20, assumed_driver_id=20, assumed_driver_sponsor_id=3,
                _assume_driver_original={"user_id": 50})

    assert client.get("/messages/8").status_code == 404
    assert sql(conn) == []


def test_poll_returns_only_newer_messages_as_json(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[MEMBERSHIP], fetchall_values=[[message(7, 50, "New!")]])
    set_session(role="Driver", user_id=20)

    response = client.get("/messages/3/new?after=6")

    assert response.is_json
    assert response.json["messages"] == [
        {"id": 7, "body": "New!", "mine": False, "sender": "Sam Lee", "time": "Nov 02, 10:00 AM"}]
    select = next(p for q, p in sql(conn) if "FROM Messages JOIN Users" in q)
    assert select[2] == 6


def test_poll_when_logged_out_never_touches_the_database(client, fake_db):
    conn = fake_db()
    assert client.get("/messages/3/new").status_code == 403
    assert sql(conn) == []


def test_admins_have_no_chat(client, set_session, fake_db):
    fake_db()
    set_session(role="Admin", user_id=1)
    assert client.get("/messages").status_code == 302


def test_inbox_lists_conversations_with_unread_counts(client, set_session, fake_db):
    conn = fake_db(fetchall_values=[[
        {"other_id": 20, "name": "Dana Diaz", "last_body": "See you Friday", "last_id": 9, "unread": 2},
        {"other_id": 21, "name": "Rex Ortiz", "last_body": None, "last_id": None, "unread": 0},
    ]])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    html = client.get("/messages").data

    assert b"See you Friday" in html and b"2 new" in html
    assert b"Start the conversation" in html
    assert b"/messages/20" in html
    inbox = next(q for q, _ in sql(conn) if "FROM Driver_Sponsors" in q)
    assert "Driver_Sponsors.status = 'Active'" in inbox


def test_unread_badge_counts_for_the_right_side(fake_db, app_module):
    import chat

    conn = fake_db(fetchone_values=[{"c": 4}])
    with app_module.app.test_request_context():
        from flask import session
        session.update({"user_id": 50, "role": "Sponsor", "sponsor_id": 3})
        with conn.cursor() as cursor:
            assert chat.count_unread_messages(cursor) == 4

    query, params = sql(conn)[0]
    assert "Messages.sponsor_id = %s" in query and "sender_user_id = Messages.driver_id" in query
    assert params == (3,)
