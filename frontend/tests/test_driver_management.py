"""Sponsor driver management: quick point awards, pausing redemption, and viewing as a driver."""

import pytest


def sql(conn):
    return conn.cursor().executed


# ------------------------------------------------------------------
# Quick point awards
# ------------------------------------------------------------------

@pytest.mark.parametrize("preset, amount, reason", [
    ("weekly", 100, "Good weekly driving"),
    ("above_beyond", 500, "Above and beyond skills"),
])
def test_quick_award_adds_the_preset_amount_and_reason(client, set_session, fake_db, preset, amount, reason):
    conn = fake_db(fetchone_values=[{"points_balance": 0}, {"company_name": "Acme"}])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/sponsor/drivers/20/points", data={"preset": preset})

    update = next(p for q, p in sql(conn) if q.startswith("UPDATE Driver_Sponsors"))
    assert update == (amount, 20, 3)
    tx = next(p for q, p in sql(conn) if "INSERT INTO Point_Transactions" in q)
    assert tx == (20, 3, 50, amount, reason)
    assert conn.committed


def test_quick_award_ignores_amount_and_reason_sent_with_it(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[{"points_balance": 0}, {"company_name": "Acme"}])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/sponsor/drivers/20/points",
                data={"preset": "weekly", "action": "deduct", "amount": "99999", "reason": "hack"})

    tx = next(p for q, p in sql(conn) if "INSERT INTO Point_Transactions" in q)
    assert tx[3:] == (100, "Good weekly driving")


def test_unknown_preset_falls_back_to_the_normal_form_checks(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/sponsor/drivers/20/points", data={"preset": "million"})

    assert sql(conn) == []


def test_drivers_page_shows_quick_awards_and_matching_form_fields(client, set_session, fake_db):
    fake_db(fetchall_values=[[{
        "driver_id": 20, "points_balance": 40, "status": "Active",
        "redeem_suspended": 0, "suspend_reason": None,
        "first_name": "Dana", "last_name": "Diaz", "email": "dana@example.com",
    }]])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    html = client.get("/sponsor/drivers").data

    assert b'name="preset" value="weekly"' in html
    assert b"Good weekly driving" in html and b"Above and beyond skills" in html
    # The custom form posts the fields the points route reads.
    assert b'name="amount"' in html
    assert b'name="action" value="add"' in html and b'name="action" value="deduct"' in html
    assert b'value="points_high"' in html
    assert b"Suspend redemption" in html


# ------------------------------------------------------------------
# Pausing point redemption
# ------------------------------------------------------------------

def test_suspending_redemption_needs_a_reason(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/sponsor/drivers/20/redemption", data={"action": "suspend", "reason": "  "})

    assert sql(conn) == []


def test_suspend_and_resume_only_touch_this_sponsors_driver(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[{"company_name": "Acme"}])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/sponsor/drivers/20/redemption", data={"action": "suspend", "reason": "Disputed trip log"})

    update = next(p for q, p in sql(conn) if q.startswith("UPDATE Driver_Sponsors"))
    assert update == (1, "Disputed trip log", 20, 3)
    note = next(p for q, p in sql(conn) if "INSERT INTO Notifications" in q)
    assert note[0] == 20 and "paused" in note[1]
    assert conn.committed

    conn = fake_db(fetchone_values=[{"company_name": "Acme"}])
    client.post("/sponsor/drivers/20/redemption", data={"action": "resume"})
    update = next(p for q, p in sql(conn) if q.startswith("UPDATE Driver_Sponsors"))
    assert update == (0, None, 20, 3)


def test_suspending_a_driver_outside_the_program_does_nothing(client, set_session, fake_db):
    conn = fake_db()
    conn.cursor().rowcount = 0
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/sponsor/drivers/99/redemption", data={"action": "suspend", "reason": "x"})

    assert not conn.committed
    assert not any("INSERT INTO Notifications" in q for q, _ in sql(conn))


def test_drivers_cannot_change_redemption(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Driver", user_id=20)

    client.post("/sponsor/drivers/20/redemption", data={"action": "resume"})

    assert sql(conn) == []


# ------------------------------------------------------------------
# Viewing the site as a driver
# ------------------------------------------------------------------

DRIVER_ROW = {"user_id": 20, "first_name": "Dana", "last_name": "Diaz"}


def start_driver_view(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[DRIVER_ROW])
    set_session(role="Sponsor", user_id=50, sponsor_id=3, first_name="Sam", last_name="Lee")
    response = client.post("/sponsor/assume-driver/20")
    return conn, response


def test_sponsor_can_view_as_their_driver(client, set_session, fake_db):
    conn, response = start_driver_view(client, set_session, fake_db)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/driver/dashboard")
    query, params = sql(conn)[0]
    assert "Driver_Sponsors.sponsor_id = %s" in query and params == (20, 3)
    assert "FROM Drivers" not in query and "d.user_id" not in query  # Drivers has no user_id column

    with client.session_transaction() as sess:
        assert sess["user_id"] == 20
        assert sess["role"] == "Driver"
        assert sess["driver_sponsor_id"] == 3
        assert sess["assumed_driver_sponsor_id"] == 3
        # Not the admin's view-as-sponsor marker, which changes the banner.
        assert "assumed_sponsor_id" not in sess
        assert sess["_assume_driver_original"]["user_id"] == 50


def test_cannot_view_a_driver_outside_the_program(client, set_session, fake_db):
    fake_db(fetchone_values=[None])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    response = client.post("/sponsor/assume-driver/99")

    assert response.status_code == 404
    with client.session_transaction() as sess:
        assert sess["user_id"] == 50


def test_driver_view_blocks_changes_but_allows_shopping_and_keep_alive(client, set_session, fake_db):
    start_driver_view(client, set_session, fake_db)

    assert client.post("/driver/profile/edit", data={}).status_code == 403
    assert client.post("/notifications/read").status_code == 403
    assert client.post("/session/keep-alive").status_code != 403
    # Shopping routes get past the guard (then fail CSRF here, not the guard).
    assert client.post("/driver/cart/add", data={"item_id": "v1|123456789|0"}).status_code == 403
    response = client.post("/driver/cart/add", data={"item_id": "v1|123456789|0", "catalog_csrf": "x"})
    assert b"read-only" not in response.data.lower()


def test_exit_driver_view_restores_the_sponsor(client, set_session, fake_db):
    start_driver_view(client, set_session, fake_db)

    response = client.post("/sponsor/exit-assumed-driver")

    assert response.status_code == 302
    with client.session_transaction() as sess:
        assert sess["user_id"] == 50
        assert sess["role"] == "Sponsor"
        assert "assumed_driver_id" not in sess
        assert sess["last_activity"]


def test_navbar_only_lists_the_viewing_sponsors_program(fake_db, app_module, monkeypatch):
    fake_db()
    monkeypatch.setattr(app_module, "get_driver_sponsorships", lambda cursor, driver_id: [
        {"sponsor_id": 3, "company_name": "Acme"},
        {"sponsor_id": 8, "company_name": "Other Fleet"},
    ])

    with app_module.app.test_request_context():
        from flask import session
        session.update({"user_id": 20, "role": "Driver",
                        "assumed_driver_id": 20, "assumed_driver_sponsor_id": 3})
        context = app_module.inject_driver_sponsorships()

    assert [s["company_name"] for s in context["driver_sponsorships"]] == ["Acme"]
