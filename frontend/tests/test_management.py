"""Admin and sponsor management: users, sponsors, applications, orders, and teams."""

from datetime import datetime
from decimal import Decimal

import pytest


def queries(conn):
    return conn.cursor().executed


def sql(conn):
    return [q for q, _ in queries(conn)]


def flashes(client):
    with client.session_transaction() as s:
        return s.get("_flashes", [])


# ------------------------------------------------------------------
# Admin: users (edit / deactivate any account)
# ------------------------------------------------------------------

def test_admin_users_page_lists_accounts(client, set_session, fake_db):
    set_session(role="Admin", user_id=1)
    fake_db(fetchall_values=[[{"user_id": 3, "first_name": "Dana", "last_name": "Ray", "email": "d@x.co",
                               "role": "Driver", "is_active": 1, "company_name": "Acme"}]])

    html = client.get("/admin/users?role=Driver").get_data(as_text=True)

    assert "Dana Ray" in html and "Deactivate" in html


def test_admin_edits_user(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[None])
    set_session(role="Admin", user_id=1)

    client.post("/admin/users/3/edit", data={"first_name": "Dana", "last_name": "Ray", "email": "d@x.co"})

    assert any(q.strip().startswith("UPDATE Users SET first_name") for q in sql(conn))
    assert conn.committed


def test_admin_edit_rejects_email_in_use(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[{"user_id": 9}])
    set_session(role="Admin", user_id=1)

    client.post("/admin/users/3/edit", data={"first_name": "Dana", "email": "taken@x.co"})

    assert not any(q.strip().startswith("UPDATE") for q in sql(conn))


def test_admin_deactivates_user(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Admin", user_id=1)

    client.post("/admin/users/3/active", data={"active": "0"})

    query, params = queries(conn)[0]
    assert "is_active" in query and params == (False, 3)


def test_admin_cannot_deactivate_self(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Admin", user_id=1)

    client.post("/admin/users/1/active", data={"active": "0"})

    assert sql(conn) == []


def test_admin_creates_admin(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[None])
    set_session(role="Admin", user_id=1)

    client.post("/admin/admins", data={"first_name": "Ana", "last_name": "Lee",
                                       "email": "ana@x.co", "password": "longenough"})

    assert any("'Admin'" in q for q in sql(conn))
    assert conn.committed


@pytest.mark.parametrize("role", ["Sponsor", "Driver"])
def test_only_admins_manage_users(client, set_session, fake_db, role):
    conn = fake_db()
    set_session(role=role, user_id=5)

    assert client.post("/admin/users/3/active", data={"active": "0"}).status_code == 403
    assert sql(conn) == []


# ------------------------------------------------------------------
# Admin: sponsors (info, edit, point value, suspend)
# ------------------------------------------------------------------

def test_admin_sponsor_info_page(client, set_session, fake_db):
    set_session(role="Admin", user_id=1)
    fake_db(
        fetchone_values=[
            {"sponsor_id": 14, "company_name": "Acme", "email": None, "phone": None,
             "point_value": Decimal("0.0100"), "is_active": 1},
            {"order_count": 2, "points_redeemed": Decimal("900")},
        ],
        fetchall_values=[[], [{"driver_id": 3, "first_name": "D", "last_name": "R", "email": "d@x.co",
                               "status": "Active", "points_balance": 250}]],
    )

    html = client.get("/admin/sponsors/14").get_data(as_text=True)

    assert "Acme" in html and "Suspend sponsor" in html and "250" in html


def test_admin_edits_sponsor(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Admin", user_id=1)

    client.post("/admin/sponsors/14/edit", data={"company_name": "Acme Freight", "org_email": "", "phone": ""})

    query, params = queries(conn)[0]
    assert query.startswith("UPDATE Sponsors") and params == ("Acme Freight", None, None, 14)


@pytest.mark.parametrize("value", ["abc", "0", "101"])
def test_bad_point_value_is_rejected(client, set_session, fake_db, value):
    conn = fake_db()
    set_session(role="Admin", user_id=1)

    client.post("/admin/sponsors/14/point-value", data={"point_value": value})

    assert sql(conn) == []


def test_suspending_sponsor_blocks_its_team(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Admin", user_id=1)

    client.post("/admin/sponsors/14/active", data={"active": "0"})

    statements = sql(conn)
    assert any("UPDATE Sponsors SET is_active" in q for q in statements)
    # Individual accounts are untouched, so reactivating can't undo a removal.
    assert not any("UPDATE Users" in q for q in statements)


def test_suspended_organization_blocks_login(client, fake_db, monkeypatch, app_module):
    from werkzeug.security import generate_password_hash
    monkeypatch.setattr(app_module, "get_login_stats", lambda: (0, 0))
    fake_db(fetchone_values=[{"user_id": 5, "first_name": "S", "last_name": "P", "role": "Sponsor",
                              "is_active": 1, "organization_active": 0,
                              "password_hash": generate_password_hash("pw12345678")}])

    html = client.post("/login", data={"username": "s@x.co", "password": "pw12345678"}).get_data(as_text=True)

    assert "deactivated" in html


def test_deactivated_account_cannot_log_in(client, fake_db, monkeypatch, app_module):
    from werkzeug.security import generate_password_hash
    monkeypatch.setattr(app_module, "get_login_stats", lambda: (0, 0))
    fake_db(fetchone_values=[{"user_id": 5, "first_name": "S", "last_name": "P", "role": "Sponsor",
                              "is_active": 0, "password_hash": generate_password_hash("pw12345678")}])

    html = client.post("/login", data={"username": "s@x.co", "password": "pw12345678"}).get_data(as_text=True)

    assert "deactivated" in html
    with client.session_transaction() as s:
        assert "user_id" not in s


# ------------------------------------------------------------------
# Applications: admin approve/reject, cancelling
# ------------------------------------------------------------------

def test_admin_approves_any_sponsors_application(client, set_session, fake_db, monkeypatch, app_module):
    monkeypatch.setattr(app_module, "send_acceptance_email", lambda *a, **k: True)
    conn = fake_db(fetchone_values=[{
        "application_id": 4, "sponsor_id": 7, "driver_id": 3, "status": "Submitted",
        "applicant_email": "d@x.co", "applicant_first_name": "D",
        "applicant_dob": None, "applicant_address": None,
    }])
    set_session(role="Admin", user_id=1)

    client.post("/admin/applications/4/approve")

    lookup_query, lookup_params = queries(conn)[0]
    assert "AND sponsor_id" not in lookup_query and lookup_params == (4,)
    assert any("INSERT INTO Driver_Sponsors" in q for q, _ in queries(conn))
    assert any(p == (3, 7) for q, p in queries(conn) if "INSERT INTO Driver_Sponsors" in q)


def test_admin_cancels_application(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Admin", user_id=1)

    client.post("/admin/applications/4/cancel")

    assert "status = 'Cancelled'" in sql(conn)[0]


def test_driver_can_only_withdraw_own_application(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Driver", user_id=3)

    client.post("/driver/applications/4/cancel")

    _, params = queries(conn)[0]
    assert params[3:5] == (3, 3)


# ------------------------------------------------------------------
# Orders: history and cancel with refund
# ------------------------------------------------------------------

ORDER_ROW = {"order_id": 7, "order_number": "GD-1", "order_date": datetime(2026, 11, 1), "status": "Placed",
             "total_points": 500, "driver_id": 3, "company_name": "Acme", "first_name": "D",
             "last_name": "R", "product_name": "Mug", "quantity": 2, "unit_price_points": 250}


def test_driver_sees_order_history(client, set_session, fake_db):
    set_session(role="Driver", user_id=3)
    fake_db(fetchall_values=[[ORDER_ROW]])

    html = client.get("/driver/orders").get_data(as_text=True)

    assert "GD-1" in html and "Mug" in html and "Cancel order" in html


def test_cancelling_order_refunds_points(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[{"order_id": 7, "order_number": "GD-1", "driver_id": 3,
                                     "sponsor_id": 9, "total_points": 500}])
    set_session(role="Driver", user_id=3)

    client.post("/driver/orders/7/cancel")

    statements = sql(conn)
    assert any("SET status = 'Cancelled'" in q for q in statements)
    assert any("points_balance = points_balance +" in q for q in statements)
    assert any("INSERT INTO Point_Transactions" in q for q in statements)
    assert conn.committed


def test_shipped_order_is_not_cancelled(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[None])
    set_session(role="Driver", user_id=3)

    client.post("/driver/orders/7/cancel")

    assert not any(q.strip().startswith("UPDATE") for q in sql(conn))


# ------------------------------------------------------------------
# Sponsor: drivers, team, organization
# ------------------------------------------------------------------

@pytest.mark.parametrize("action,new_status", [
    ("suspend", "Inactive"), ("remove", "Dropped"), ("reactivate", "Active"),
])
def test_sponsor_changes_driver_status(client, set_session, fake_db, action, new_status):
    conn = fake_db()
    set_session(role="Sponsor", user_id=9, sponsor_id=9)

    client.post("/sponsor/drivers/3/status", data={"action": action})

    _, params = queries(conn)[0]
    assert params[:3] == (new_status, 3, 9)


def test_sponsor_edits_organization(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Sponsor", user_id=9, sponsor_id=9)

    client.post("/sponsor/team", data={"action": "organization", "company_name": "Acme",
                                       "org_email": "hi@acme.co", "phone": "555"})

    query, params = queries(conn)[0]
    assert query.startswith("UPDATE Sponsors") and params == ("Acme", "hi@acme.co", "555", 9)


def test_sponsor_removes_teammate_in_own_org_only(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Sponsor", user_id=9, sponsor_id=9)

    client.post("/sponsor/team", data={"action": "remove_member", "user_id": "12"})

    query, params = queries(conn)[0]
    assert "Sponsor_Users.sponsor_id = %s" in query and params == (12, 9)


def test_sponsor_cannot_remove_self(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Sponsor", user_id=9, sponsor_id=9)

    client.post("/sponsor/team", data={"action": "remove_member", "user_id": "9"})

    assert sql(conn) == []


# ------------------------------------------------------------------
# Sponsor: driver requirements
# ------------------------------------------------------------------

def test_sponsor_adds_requirement(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Sponsor", user_id=9, sponsor_id=9)

    client.post("/sponsor/requirements/add", data={"requirement_text": "CDL Class A"})

    query, params = queries(conn)[0]
    assert "INSERT INTO Sponsor_Requirements" in query and params == (9, "CDL Class A")
    assert conn.committed


@pytest.mark.parametrize("text", ["", "   ", "x" * 256])
def test_bad_requirement_is_rejected(client, set_session, fake_db, text):
    conn = fake_db()
    set_session(role="Sponsor", user_id=9, sponsor_id=9)

    client.post("/sponsor/requirements/add", data={"requirement_text": text})

    assert sql(conn) == []


def test_sponsor_deletes_only_own_requirement(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Sponsor", user_id=9, sponsor_id=9)

    client.post("/sponsor/requirements/4/delete")

    query, params = queries(conn)[0]
    assert "AND sponsor_id = %s" in query and params == (4, 9)


@pytest.mark.parametrize("role", ["Driver", "Admin"])
def test_only_sponsors_manage_requirements(client, set_session, fake_db, role):
    conn = fake_db()
    set_session(role=role, user_id=5)

    client.post("/sponsor/requirements/add", data={"requirement_text": "CDL"})
    client.post("/sponsor/requirements/4/delete")

    assert sql(conn) == []


def test_drivers_see_each_sponsors_requirements(client, set_session, fake_db):
    set_session(role="Driver", user_id=3)
    fake_db(fetchall_values=[
        [{"sponsor_id": 1, "company_name": "Acme", "email": None, "phone": None,
          "point_value": Decimal("0.01")},
         {"sponsor_id": 2, "company_name": "Beta", "email": None, "phone": None,
          "point_value": Decimal("0.01")}],
        [{"sponsor_id": 1, "requirement_text": "CDL Class A"},
         {"sponsor_id": 2, "requirement_text": "2 years experience"}],
        [],
    ])

    html = client.get("/driver/sponsors").get_data(as_text=True)

    assert "CDL Class A" in html and "2 years experience" in html


# ------------------------------------------------------------------
# Admin deciding applications from View as Sponsor
# ------------------------------------------------------------------

def assume_sponsor(set_session):
    set_session(role="sponsor", user_id=14, sponsor_id=14, assumed_sponsor_id=14,
                _assume_sponsor_original={"user_id": 1, "role": "Admin"})


def test_admin_in_sponsor_view_can_accept(client, set_session, fake_db, monkeypatch, app_module):
    monkeypatch.setattr(app_module, "send_acceptance_email", lambda *a, **k: True)
    conn = fake_db(fetchone_values=[{
        "application_id": 4, "sponsor_id": 14, "driver_id": 3, "status": "Submitted",
        "applicant_email": "d@x.co", "applicant_first_name": "D",
        "applicant_dob": None, "applicant_address": None,
    }])
    assume_sponsor(set_session)

    response = client.post("/sponsor/applications/4/accept")

    assert response.status_code == 302
    decision = [p for q, p in queries(conn) if "status = 'Accepted'" in q]
    assert decision and decision[0][0] == 1  # recorded as the admin, not the sponsor user


def test_admin_in_sponsor_view_can_reject(client, set_session, fake_db, monkeypatch, app_module):
    monkeypatch.setattr(app_module, "send_rejection_email", lambda *a, **k: True)
    conn = fake_db(fetchone_values=[{
        "application_id": 4, "sponsor_id": 14, "status": "Submitted",
        "applicant_email": "d@x.co", "applicant_first_name": "D",
    }])
    assume_sponsor(set_session)

    client.post("/sponsor/applications/4/reject", data={"reason": "Not eligible"})

    decision = [p for q, p in queries(conn) if "status = 'Rejected'" in q]
    assert decision and decision[0][:2] == ("Not eligible", 1)


def test_sponsor_view_is_otherwise_read_only(client, set_session, fake_db):
    conn = fake_db()
    assume_sponsor(set_session)

    response = client.post("/sponsor/drivers/3/points", data={"action": "add", "amount": "10", "reason": "x"})

    assert response.status_code == 403
    assert sql(conn) == []
