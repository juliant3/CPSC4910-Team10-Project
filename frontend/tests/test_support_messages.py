"""Admin support messages: sponsors and drivers each have one conversation
with the admins, shared by every admin."""

from datetime import datetime

import pytest


def queries(conn):
    return conn.cursor().executed


def sql(conn):
    return [q for q, _ in queries(conn)]


PARTY = {"name": "Acme"}


@pytest.mark.parametrize("role,extra", [
    ("Sponsor", {"sponsor_id": 9}),
    ("Driver", {}),
])
def test_sponsors_and_drivers_open_support(client, set_session, fake_db, role, extra):
    set_session(role=role, user_id=9, **extra)
    fake_db(
        fetchone_values=[PARTY],
        fetchall_values=[[{"message_id": 1, "body": "My points look wrong",
                           "created_at": datetime(2026, 11, 1), "from_admin": 1,
                           "first_name": "Ana", "last_name": "Lee"}]],
    )

    html = client.get("/support").get_data(as_text=True)

    assert "Admin Support" in html and "My points look wrong" in html and "Ana Lee (Admin)" in html


def test_sponsor_message_goes_to_its_conversation_and_notifies_admins(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[PARTY, {"c": 1}])
    set_session(role="Sponsor", user_id=9, sponsor_id=9)

    client.post("/support", data={"body": "Hello admins"})

    query, params = next((q, p) for q, p in queries(conn) if "INSERT INTO Admin_Messages" in q)
    assert "(sponsor_id," in query and params == (9, 9, False, "Hello admins")
    assert any("role = 'Admin'" in q for q in sql(conn) if "INSERT INTO Notifications" in q)
    assert conn.committed


def test_driver_message_goes_to_their_own_conversation(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[PARTY, {"c": 1}])
    set_session(role="Driver", user_id=3)

    client.post("/support", data={"body": "I can't check out"})

    query, params = next((q, p) for q, p in queries(conn) if "INSERT INTO Admin_Messages" in q)
    assert "(driver_id," in query and params == (3, 3, False, "I can't check out")


def test_admin_reply_to_driver_notifies_that_driver(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[PARTY, {"c": 1}])
    set_session(role="Admin", user_id=1)

    client.post("/admin/messages/driver/3", data={"body": "Fixed it"})

    query, params = next((q, p) for q, p in queries(conn) if "INSERT INTO Admin_Messages" in q)
    assert "(driver_id," in query and params == (3, 1, True, "Fixed it")
    notify = next(p for q, p in queries(conn) if "INSERT INTO Notifications" in q)
    assert notify[0] == 3


def test_admin_reply_to_sponsor_notifies_whole_team(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[PARTY, {"c": 1}])
    set_session(role="Admin", user_id=1)

    client.post("/admin/messages/sponsor/9", data={"body": "Done!"})

    assert any("Sponsor_Users" in q for q in sql(conn) if "INSERT INTO Notifications" in q)


def test_admin_inbox_lists_sponsors_and_drivers(client, set_session, fake_db):
    set_session(role="Admin", user_id=1)
    fake_db(fetchall_values=[[
        {"kind": "driver", "other_id": 3, "name": "Dana Ray", "last_body": "I can't check out",
         "last_id": 5, "unread": 1},
        {"kind": "sponsor", "other_id": 9, "name": "Acme", "last_body": None, "last_id": None, "unread": 0},
    ]])

    html = client.get("/admin/messages").get_data(as_text=True)

    assert "Dana Ray (Driver)" in html and "Acme (Sponsor)" in html and "1 new" in html
    assert "/admin/messages/driver/3" in html and "/admin/messages/sponsor/9" in html


def test_reading_marks_only_the_other_sides_messages(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[PARTY])
    set_session(role="Admin", user_id=1)

    client.get("/admin/messages/sponsor/9")

    mark = next(q for q in sql(conn) if q.strip().startswith("UPDATE Admin_Messages"))
    assert "read_by_admin_at = NOW()" in mark and "from_admin = FALSE" in mark and "sponsor_id" in mark


def test_empty_message_is_not_sent(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[PARTY])
    set_session(role="Driver", user_id=3)

    client.post("/support", data={"body": "   "})

    assert not any("INSERT" in q for q in sql(conn))


@pytest.mark.parametrize("path", ["/admin/messages", "/admin/messages/sponsor/9", "/admin/messages/driver/3/new"])
@pytest.mark.parametrize("role", ["Sponsor", "Driver"])
def test_only_admins_open_admin_inbox(client, set_session, fake_db, path, role):
    conn = fake_db()
    set_session(role=role, user_id=9, sponsor_id=9)

    assert client.get(path).status_code == 403
    assert sql(conn) == []


def test_admins_use_their_inbox_not_support_page(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Admin", user_id=1)

    assert client.get("/support").status_code == 302
    assert sql(conn) == []


@pytest.mark.parametrize("path", ["/admin/messages/admin/1", "/admin/messages/driver/999"])
def test_unknown_conversation_is_404(client, set_session, fake_db, path):
    fake_db(fetchone_values=[None])
    set_session(role="Admin", user_id=1)

    assert client.get(path).status_code == 404
