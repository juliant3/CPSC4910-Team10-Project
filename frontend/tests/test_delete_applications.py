"""Sponsors and admins removing applications that have waited too long."""

import pytest


OPEN_APPLICATION = {
    "application_id": 9, "sponsor_id": 3, "driver_id": None,
    "status": "Pending", "applicant_email": "slow@example.com",
}


def sql(conn):
    return conn.cursor().executed


def flashes(client):
    with client.session_transaction() as sess:
        return [message for _, message in sess.get("_flashes", [])]


def test_sponsor_deletes_their_open_application_and_its_answers(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[OPEN_APPLICATION])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    response = client.post("/sponsor/applications/9/delete")

    assert response.status_code == 302
    select, params = sql(conn)[0]
    assert "status IN ('Pending', 'Submitted')" in select
    assert params == (9, 3, 3)
    queries = [q for q, _ in sql(conn)]
    assert any(q.startswith("DELETE FROM Application_Answers") for q in queries)
    assert any(q.startswith("DELETE FROM Driver_Applications") for q in queries)
    log = next(p for q, p in sql(conn) if "INSERT INTO Audit_Logs" in q)
    assert log == (50, 3, "Pending", "slow@example.com")
    assert conn.committed
    assert "Application deleted." in flashes(client)


def test_sponsor_cannot_delete_another_sponsors_or_decided_application(client, set_session, fake_db):
    # The scoped SELECT finds nothing: wrong sponsor, or already Accepted/Rejected.
    conn = fake_db(fetchone_values=[None])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/sponsor/applications/9/delete")

    assert not any(q.startswith("DELETE") for q, _ in sql(conn))
    assert any("Only applications still waiting" in m for m in flashes(client))


def test_admin_can_delete_any_open_application(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[dict(OPEN_APPLICATION, status="Submitted")])
    set_session(role="Admin", user_id=1)

    response = client.post("/admin/applications/9/delete")

    assert response.headers["Location"].endswith("/admin/applications")
    assert sql(conn)[0][1] == (9, None, None)  # not limited to one sponsor
    assert any(q.startswith("DELETE FROM Driver_Applications") for q, _ in sql(conn))
    assert conn.committed


@pytest.mark.parametrize("role, path", [
    ("Driver", "/sponsor/applications/9/delete"),
    ("Driver", "/admin/applications/9/delete"),
    ("Sponsor", "/admin/applications/9/delete"),
])
def test_other_roles_cannot_delete(client, set_session, fake_db, role, path):
    conn = fake_db(fetchone_values=[OPEN_APPLICATION])
    set_session(role=role, user_id=20, sponsor_id=3)

    client.post(path)

    assert sql(conn) == []


def test_delete_needs_post(client, set_session, fake_db):
    fake_db()
    set_session(role="Sponsor", user_id=50, sponsor_id=3)
    assert client.get("/sponsor/applications/9/delete").status_code == 405


def test_admin_list_shows_delete_buttons(client, set_session, fake_db):
    fake_db(fetchall_values=[[{
        "application_id": 9, "application_date": None, "status": "Pending",
        "applicant_email": "slow@example.com", "applicant_first_name": None,
        "applicant_last_name": None, "sponsor_name": "Acme",
    }]])
    set_session(role="Admin", user_id=1)

    html = client.get("/admin/applications").data

    assert b"/admin/applications/9/delete" in html
