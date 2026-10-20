from datetime import datetime, timezone

import pytest


@pytest.fixture
def sent_emails(monkeypatch, app_module):
    """Capture outgoing sponsor-request emails instead of sending them."""
    sent = []
    for name in (
        "send_sponsor_request_link",
        "send_sponsor_approved_email",
        "send_sponsor_rejected_email",
    ):
        monkeypatch.setattr(
            app_module, name,
            lambda *args, _name=name: sent.append((_name, args)) or True
        )
    return sent


def login_as(client, role, user_id=1):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["first_name"] = "Test"
        sess["role"] = role
        sess["last_activity"] = datetime.now(timezone.utc).isoformat()


def executed_sql(connection):
    return [query for query, _ in connection.cursor().executed]


# ------------------------------------------------------------------
# Step 1: request a link
# ------------------------------------------------------------------

def test_login_page_links_to_sponsor_request(client):
    response = client.get("/login")
    assert b"/sponsor-request" in response.data


def test_sponsor_request_page_loads(client):
    response = client.get("/sponsor-request")
    assert response.status_code == 200
    assert b"Become a Sponsor" in response.data


def test_sponsor_request_creates_request_and_emails_link(client, fake_db, sent_emails):
    conn = fake_db(fetchone_values=[None, None])

    response = client.post("/sponsor-request", data={
        "first_name": "Sam",
        "last_name": "Fleet",
        "email": "Sam@Fleet.com",
    })

    assert b"Check Your Email" in response.data
    assert any("INSERT INTO Sponsor_Requests" in q for q in executed_sql(conn))
    assert conn.committed
    assert sent_emails[0][0] == "send_sponsor_request_link"
    assert sent_emails[0][1][0] == "sam@fleet.com"


def test_sponsor_request_rejects_existing_account(client, fake_db, sent_emails):
    conn = fake_db(fetchone_values=[{"user_id": 5}])

    response = client.post("/sponsor-request", data={
        "first_name": "Sam", "last_name": "Fleet", "email": "sam@fleet.com",
    })

    assert b"already exists" in response.data
    assert not any("INSERT" in q for q in executed_sql(conn))
    assert sent_emails == []


def test_sponsor_request_already_submitted_shows_under_review(client, fake_db, sent_emails):
    fake_db(fetchone_values=[None, {"request_id": 1, "token": "t", "status": "Submitted"}])

    response = client.post("/sponsor-request", data={
        "first_name": "Sam", "last_name": "Fleet", "email": "sam@fleet.com",
    })

    assert b"Already Submitted" in response.data
    assert sent_emails == []


def test_sponsor_request_resends_open_link(client, fake_db, sent_emails):
    conn = fake_db(fetchone_values=[None, {"request_id": 1, "token": "abc", "status": "Started"}])

    client.post("/sponsor-request", data={
        "first_name": "Sam", "last_name": "Fleet", "email": "sam@fleet.com",
    })

    assert not any("INSERT" in q for q in executed_sql(conn))
    assert sent_emails[0][1][2] == "abc"


# ------------------------------------------------------------------
# Step 2: the emailed application link
# ------------------------------------------------------------------

STARTED = {
    "request_id": 7,
    "email": "sam@fleet.com",
    "first_name": "Sam",
    "last_name": "Fleet",
    "status": "Started",
}


def test_sponsor_apply_invalid_token(client, fake_db):
    fake_db(fetchone_values=[None])
    response = client.get("/sponsor-apply/nope")
    assert b"Link Not Valid" in response.data


def test_sponsor_apply_submitted_link_cannot_be_reused(client, fake_db):
    fake_db(fetchone_values=[dict(STARTED, status="Submitted")])
    response = client.post("/sponsor-apply/used", data={"company_name": "Fleet Co"})
    assert b"Under Review" in response.data


def test_sponsor_apply_submits_application(client, fake_db):
    conn = fake_db(fetchone_values=[dict(STARTED), None], fetchall_values=[[{"user_id": 99}]])

    response = client.post("/sponsor-apply/good", data={
        "company_name": "Fleet Co",
        "phone": "555-0100",
        "message": "20 trucks",
    })

    assert b"Application Submitted" in response.data
    queries = executed_sql(conn)
    assert any("UPDATE Sponsor_Requests" in q and "'Submitted'" in q for q in queries)
    assert any("INSERT INTO Notifications" in q for q in queries)


def test_sponsor_apply_rejects_existing_company_name(client, fake_db):
    conn = fake_db(fetchone_values=[dict(STARTED), {"sponsor_id": 3}])

    response = client.post("/sponsor-apply/good", data={"company_name": "Acme"})

    assert b"already exists" in response.data
    assert not any("UPDATE Sponsor_Requests" in q for q in executed_sql(conn))


# ------------------------------------------------------------------
# Step 3: admin review
# ------------------------------------------------------------------

def test_non_admin_cannot_review_sponsor_requests(client):
    login_as(client, "Sponsor")
    response = client.get("/admin/sponsor-requests", follow_redirects=False)
    assert response.status_code == 302


def test_non_admin_cannot_approve(client, fake_db):
    conn = fake_db()
    login_as(client, "Driver")
    client.post("/admin/sponsor-requests/7/approve")
    assert executed_sql(conn) == []


def test_admin_approval_creates_sponsor_account(client, fake_db, sent_emails):
    request_row = {
        "request_id": 7,
        "email": "sam@fleet.com",
        "first_name": "Sam",
        "last_name": "Fleet",
        "company_name": "Fleet Co",
        "phone": None,
    }
    conn = fake_db(fetchone_values=[request_row, None], lastrowid=500)
    login_as(client, "Admin", user_id=1)

    response = client.post("/admin/sponsor-requests/7/approve", follow_redirects=False)

    assert response.status_code == 302
    queries = executed_sql(conn)
    for table in ("INSERT INTO Users", "INSERT INTO Password", "INSERT INTO Sponsors", "INSERT INTO Sponsor_Users"):
        assert any(table in q for q in queries), table
    assert any("UPDATE Sponsor_Requests" in q and "'Approved'" in q for q in queries)
    assert conn.committed

    name, args = sent_emails[0]
    assert name == "send_sponsor_approved_email"
    assert args[0] == "sam@fleet.com"
    assert args[3]  # temporary password


def test_admin_approval_skips_when_account_exists(client, fake_db, sent_emails):
    request_row = {
        "request_id": 7, "email": "sam@fleet.com", "first_name": "Sam",
        "last_name": "Fleet", "company_name": "Fleet Co", "phone": None,
    }
    conn = fake_db(fetchone_values=[request_row, {"user_id": 12}])
    login_as(client, "Admin")

    client.post("/admin/sponsor-requests/7/approve")

    assert not any("INSERT INTO Users" in q for q in executed_sql(conn))
    assert sent_emails == []


def test_admin_reject_requires_reason(client, fake_db, sent_emails):
    conn = fake_db()
    login_as(client, "Admin")

    client.post("/admin/sponsor-requests/7/reject", data={"reason": "  "})

    assert executed_sql(conn) == []
    assert sent_emails == []


def test_admin_reject_updates_request_and_emails(client, fake_db, sent_emails):
    conn = fake_db(fetchone_values=[{"email": "sam@fleet.com", "first_name": "Sam", "company_name": "Fleet Co"}])
    login_as(client, "Admin")

    client.post("/admin/sponsor-requests/7/reject", data={"reason": "Not a trucking company"})

    assert any("UPDATE Sponsor_Requests" in q and "'Rejected'" in q for q in executed_sql(conn))
    assert sent_emails[0][0] == "send_sponsor_rejected_email"


# ------------------------------------------------------------------
# Sponsor teams: one sponsor invites another into their organization
# ------------------------------------------------------------------

def login_as_sponsor(client, user_id, sponsor_id):
    login_as(client, "Sponsor", user_id=user_id)
    with client.session_transaction() as sess:
        sess["sponsor_id"] = sponsor_id


def test_sponsor_pages_use_organization_id_not_user_id(client, fake_db):
    conn = fake_db(fetchall_values=[[]])
    login_as_sponsor(client, user_id=42, sponsor_id=7)

    client.get("/sponsor/drivers")

    query, params = next(
        (q, p) for q, p in conn.cursor().executed if "FROM Drivers" in q
    )
    assert params[0] == 7


def test_login_stores_sponsor_organization(client, fake_db, monkeypatch, app_module):
    from werkzeug.security import generate_password_hash

    fake_db(fetchone_values=[{
        "user_id": 42, "first_name": "Tia", "last_name": "Teammate",
        "role": "Sponsor", "password_hash": generate_password_hash("password123"),
    }])
    monkeypatch.setattr(app_module, "get_sponsor_id_for_user", lambda user_id: 7)

    client.post("/login", data={"username": "tia@fleet.com", "password": "password123"})

    with client.session_transaction() as sess:
        assert sess["user_id"] == 42
        assert sess["sponsor_id"] == 7


def test_team_page_requires_sponsor(client):
    login_as(client, "Driver")
    response = client.get("/sponsor/team", follow_redirects=False)
    assert response.status_code == 302


def test_sponsor_can_invite_teammate(client, fake_db, sent_emails, monkeypatch, app_module):
    invites = []
    monkeypatch.setattr(app_module, "send_sponsor_team_invite",
                        lambda *args: invites.append(args) or True)
    conn = fake_db(fetchone_values=[None, None, {"company_name": "Fleet Co"}])
    login_as_sponsor(client, user_id=42, sponsor_id=7)

    client.post("/sponsor/team", data={
        "action": "invite", "first_name": "New", "last_name": "Person", "email": "New@Fleet.com",
    })

    insert = next(p for q, p in conn.cursor().executed if "INSERT INTO Sponsor_Requests" in q)
    assert "'Join'" in next(q for q, _ in conn.cursor().executed if "INSERT INTO Sponsor_Requests" in q)
    assert insert[0] == "new@fleet.com"
    assert insert[4] == 7          # target organization
    assert insert[6] == 42         # who sent the invite
    assert invites[0][0] == "new@fleet.com"
    assert invites[0][2] == "Fleet Co"


def test_invite_rejected_when_email_has_account(client, fake_db, monkeypatch, app_module):
    invites = []
    monkeypatch.setattr(app_module, "send_sponsor_team_invite",
                        lambda *args: invites.append(args) or True)
    conn = fake_db(fetchone_values=[{"user_id": 3}])
    login_as_sponsor(client, user_id=42, sponsor_id=7)

    client.post("/sponsor/team", data={
        "action": "invite", "first_name": "A", "last_name": "B", "email": "a@b.com",
    })

    assert not any("INSERT" in q for q in executed_sql(conn))
    assert invites == []


JOIN_INVITE = {
    "request_id": 9, "email": "new@fleet.com", "first_name": "New", "last_name": "Person",
    "status": "Started", "request_type": "Join", "target_sponsor_id": 7, "company_name": "Fleet Co",
}


def test_join_link_shows_create_account_form(client, fake_db):
    fake_db(fetchone_values=[dict(JOIN_INVITE)])
    response = client.get("/sponsor-apply/join-token")
    assert b"Join Fleet Co" in response.data
    assert b"Create Account" in response.data


def test_join_link_creates_account_in_organization(client, fake_db):
    conn = fake_db(fetchone_values=[dict(JOIN_INVITE), None], lastrowid=77)

    response = client.post("/sponsor-apply/join-token", data={
        "first_name": "New", "last_name": "Person",
        "password": "password123", "confirm_password": "password123",
    })

    assert b"You're All Set" in response.data or b"You&#39;re All Set" in response.data
    executed = conn.cursor().executed
    link = next(p for q, p in executed if "INSERT INTO Sponsor_Users" in q)
    assert link == (77, 7)
    assert not any("INSERT INTO Sponsors " in q for q, _ in executed)
    assert any("'Approved'" in q for q, _ in executed if "UPDATE Sponsor_Requests" in q)
    assert conn.committed


def test_join_link_checks_password(client, fake_db):
    conn = fake_db(fetchone_values=[dict(JOIN_INVITE)])

    response = client.post("/sponsor-apply/join-token", data={
        "first_name": "New", "last_name": "Person",
        "password": "short", "confirm_password": "short",
    })

    assert b"at least 8 characters" in response.data
    assert not any("INSERT" in q for q in executed_sql(conn))


def test_cancelled_invite_link_is_closed(client, fake_db):
    fake_db(fetchone_values=[dict(JOIN_INVITE, status="Rejected")])
    response = client.get("/sponsor-apply/join-token")
    assert b"Invite No Longer Valid" in response.data
