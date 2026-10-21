"""RC#2: drivers can belong to multiple sponsors, and points never mix."""

from datetime import datetime, timezone

import pytest


def login(client, role, user_id, **extra):
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["first_name"] = "Test"
        sess["role"] = role
        sess["last_activity"] = datetime.now(timezone.utc).isoformat()
        sess.update(extra)


def queries(conn):
    return conn.cursor().executed


# ------------------------------------------------------------------
# Choosing which sponsor a driver is viewing
# ------------------------------------------------------------------

SPONSORSHIPS = [
    {"sponsor_id": 3, "status": "Active", "points_balance": 100, "company_name": "Acme", "point_value": 0.01},
    {"sponsor_id": 8, "status": "Active", "points_balance": 40, "company_name": "Blue Line", "point_value": 0.01},
]


def test_driver_views_first_sponsor_by_default(app, app_module):
    with app.test_request_context():
        assert app_module.pick_driver_sponsorship(SPONSORSHIPS)["sponsor_id"] == 3


def test_driver_can_pick_one_of_their_sponsors(app, app_module):
    with app.test_request_context():
        from flask import session
        session["driver_sponsor_id"] = 8
        assert app_module.pick_driver_sponsorship(SPONSORSHIPS)["sponsor_id"] == 8


def test_driver_cannot_pick_a_sponsor_they_dont_belong_to(app, app_module):
    with app.test_request_context():
        from flask import session
        session["driver_sponsor_id"] = 999
        assert app_module.pick_driver_sponsorship(SPONSORSHIPS)["sponsor_id"] == 3


def test_switch_sponsor_saves_choice_and_returns(client):
    login(client, "Driver", 20)

    response = client.post(
        "/driver/switch-sponsor",
        data={"sponsor_id": "8"},
        headers={"Referer": "http://localhost/driver/points"},
    )

    assert response.headers["Location"].endswith("/driver/points")
    with client.session_transaction() as sess:
        assert sess["driver_sponsor_id"] == 8


def test_switch_sponsor_never_redirects_off_site(client):
    login(client, "Driver", 20)

    response = client.post(
        "/driver/switch-sponsor",
        data={"sponsor_id": "8"},
        headers={"Referer": "https://evil.example.com/"},
    )

    assert response.headers["Location"].endswith("/driver/dashboard")


# ------------------------------------------------------------------
# Points never mix between sponsors
# ------------------------------------------------------------------

def test_driver_points_page_only_shows_viewed_sponsor(client, fake_db):
    conn = fake_db(
        fetchone_values=[{"driver_id": 20}, {"earned": 50, "deducted": 0}],
        fetchall_values=[SPONSORSHIPS, [], []],
    )
    login(client, "Driver", 20, driver_sponsor_id=8)

    response = client.get("/driver/points")

    assert response.status_code == 200
    assert b"Blue Line" in response.data
    point_queries = [p for q, p in queries(conn) if "FROM Point_Transactions" in q]
    assert point_queries and all(params[1] == 8 for params in point_queries)


def test_sponsor_point_change_only_touches_their_balance(client, fake_db):
    conn = fake_db(fetchone_values=[{"points_balance": 30}, {"company_name": "Acme"}])
    login(client, "Sponsor", 50, sponsor_id=3)

    client.post("/sponsor/drivers/20/points", data={"action": "add", "amount": "10", "reason": "Safe week"})

    update = next(p for q, p in queries(conn) if q.startswith("UPDATE Driver_Sponsors"))
    assert update == (10, 20, 3)
    assert not any(q.startswith("UPDATE Drivers") for q, _ in queries(conn))
    note = next(p for q, p in queries(conn) if "INSERT INTO Notifications" in q)
    assert "Acme added 10 points" in note[2]


def test_cannot_deduct_more_than_this_sponsors_balance(client, fake_db):
    conn = fake_db(fetchone_values=[{"points_balance": 5}])
    login(client, "Sponsor", 50, sponsor_id=3)

    client.post("/sponsor/drivers/20/points", data={"action": "deduct", "amount": "10", "reason": "Late"})

    assert not any(q.startswith("UPDATE") for q, _ in queries(conn))


# ------------------------------------------------------------------
# Sponsors only see their own relationship with a driver
# ------------------------------------------------------------------

def test_sponsor_driver_list_is_scoped_to_their_program(client, fake_db):
    conn = fake_db(fetchall_values=[[]])
    login(client, "Sponsor", 50, sponsor_id=3)

    client.get("/sponsor/drivers")

    query, params = next((q, p) for q, p in queries(conn) if "FROM Driver_Sponsors" in q)
    assert "WHERE Driver_Sponsors.sponsor_id = %s" in query
    assert params[0] == 3


def test_sponsor_profile_view_hides_other_sponsors_answers(client, fake_db):
    driver_row = {
        "driver_id": 20, "sponsor_id": 3, "points_balance": 30, "status": "Active",
        "first_name": "Dana", "last_name": "Driver", "email": "dana@x.com",
        "date_of_birth": None, "address": None, "sponsor_name": "Acme", "sponsor_email": None,
    }
    conn = fake_db(fetchone_values=[driver_row], fetchall_values=[[]])
    login(client, "Sponsor", 50, sponsor_id=3)

    response = client.get("/sponsor/drivers/20")

    assert response.status_code == 200
    answers = next((q, p) for q, p in queries(conn) if "FROM Application_Answers" in q)
    assert "Driver_Applications.sponsor_id = %s" in answers[0]
    assert answers[1] == (20, 3)


def test_sponsor_cannot_open_driver_outside_their_program(client, fake_db):
    # The driver exists but has no membership row with sponsor 3.
    driver_row = {
        "driver_id": 20, "sponsor_id": None, "points_balance": 0, "status": "Active",
        "first_name": "Dana", "last_name": "Driver", "email": "dana@x.com",
        "date_of_birth": None, "address": None, "sponsor_name": None, "sponsor_email": None,
    }
    fake_db(fetchone_values=[driver_row], fetchall_values=[[]])
    login(client, "Sponsor", 50, sponsor_id=3)

    response = client.get("/sponsor/drivers/20", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/sponsor/drivers")


# ------------------------------------------------------------------
# Accepting an existing driver adds a sponsor instead of replacing one
# ------------------------------------------------------------------

def test_accepting_existing_driver_adds_a_sponsorship(client, fake_db, monkeypatch, app_module):
    monkeypatch.setattr(app_module, "send_acceptance_email", lambda *a, **k: True)
    application = {
        "application_id": 4, "status": "Submitted", "driver_id": 20, "sponsor_id": 3,
        "applicant_email": "dana@x.com", "applicant_first_name": "Dana",
        "applicant_last_name": "Driver", "applicant_dob": None, "applicant_address": None,
    }
    conn = fake_db(fetchone_values=[application])
    login(client, "Sponsor", 50, sponsor_id=3)

    client.post("/sponsor/applications/4/accept")

    executed = queries(conn)
    link = next(p for q, p in executed if "INSERT INTO Driver_Sponsors" in q)
    assert link == (20, 3)
    assert not any("SET sponsor_id" in q for q, _ in executed)
