"""Sponsor <-> admin support messages: one shared conversation per sponsor."""

from datetime import datetime

import pytest


def queries(conn):
    return conn.cursor().executed


def sql(conn):
    return [q for q, _ in queries(conn)]


SPONSOR = {"company_name": "Acme"}


def test_sponsor_opens_support_conversation(client, set_session, fake_db):
    set_session(role="Sponsor", user_id=9, sponsor_id=9)
    fake_db(
        fetchone_values=[SPONSOR],
        fetchall_values=[[{"message_id": 1, "body": "Can you raise our point value?",
                           "created_at": datetime(2026, 11, 1), "from_admin": 0,
                           "first_name": "Sam", "last_name": "Lee"}]],
    )

    html = client.get("/support").get_data(as_text=True)

    assert "Admin Support" in html and "Can you raise our point value?" in html


def test_sponsor_message_goes_to_own_conversation_and_notifies_admins(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[SPONSOR, {"c": 1}])
    set_session(role="Sponsor", user_id=9, sponsor_id=9)

    client.post("/support", data={"body": "Hello admins"})

    insert = next(p for q, p in queries(conn) if "INSERT INTO Support_Messages" in q)
    assert insert == (9, 9, False, "Hello admins")
    assert any("role = 'Admin'" in q for q in sql(conn) if "INSERT INTO Notifications" in q)
    assert conn.committed


def test_admin_reply_notifies_sponsor_team(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[SPONSOR, {"c": 1}])
    set_session(role="Admin", user_id=1)

    client.post("/admin/messages/9", data={"body": "Done!"})

    insert = next(p for q, p in queries(conn) if "INSERT INTO Support_Messages" in q)
    assert insert == (9, 1, True, "Done!")
    assert any("Sponsor_Users" in q for q in sql(conn) if "INSERT INTO Notifications" in q)


def test_admin_inbox_lists_every_sponsor(client, set_session, fake_db):
    set_session(role="Admin", user_id=1)
    fake_db(fetchall_values=[[
        {"other_id": 9, "name": "Acme", "last_body": "Hello admins", "last_id": 3, "unread": 1},
        {"other_id": 10, "name": "Beta", "last_body": None, "last_id": None, "unread": 0},
    ]])

    html = client.get("/admin/messages").get_data(as_text=True)

    assert "Acme" in html and "Beta" in html and "1 new" in html
    assert "/admin/messages/9" in html


def test_reading_marks_only_the_other_sides_messages(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[SPONSOR])
    set_session(role="Admin", user_id=1)

    client.get("/admin/messages/9")

    mark = next(q for q in sql(conn) if q.strip().startswith("UPDATE Support_Messages"))
    assert "read_by_admin_at = NOW()" in mark and "from_admin = FALSE" in mark


def test_empty_message_is_not_sent(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[SPONSOR])
    set_session(role="Sponsor", user_id=9, sponsor_id=9)

    client.post("/support", data={"body": "   "})

    assert not any("INSERT" in q for q in sql(conn))


@pytest.mark.parametrize("path", ["/admin/messages", "/admin/messages/9", "/admin/messages/9/new"])
def test_sponsors_cannot_open_admin_inbox(client, set_session, fake_db, path):
    conn = fake_db()
    set_session(role="Sponsor", user_id=9, sponsor_id=9)

    assert client.get(path).status_code == 403
    assert sql(conn) == []


@pytest.mark.parametrize("role", ["Driver", "Admin"])
def test_only_sponsors_use_support_page(client, set_session, fake_db, role):
    conn = fake_db()
    set_session(role=role, user_id=5)

    assert client.get("/support").status_code == 302
    assert sql(conn) == []


def test_unknown_sponsor_is_404(client, set_session, fake_db):
    fake_db(fetchone_values=[None])
    set_session(role="Admin", user_id=1)

    assert client.get("/admin/messages/999").status_code == 404
