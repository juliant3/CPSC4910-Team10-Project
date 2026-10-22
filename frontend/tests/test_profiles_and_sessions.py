"""Profile editing, login sessions, the notification bell, and shared page chrome."""

from datetime import date, datetime, timedelta, timezone

import pytest


def queries(conn):
    return conn.cursor().executed


def sql(conn):
    return [q for q, _ in queries(conn)]


DRIVER_ROW = {
    "driver_id": 20, "sponsor_id": 3, "points_balance": 40, "status": "Active",
    "first_name": "Dana", "last_name": "Driver", "email": "dana@x.com",
    "date_of_birth": date(1990, 4, 12), "address": "1 Main St",
    "sponsor_name": "Acme", "sponsor_email": None,
}

VALID_PROFILE = {
    "first_name": "Dana",
    "last_name": "Rivers",
    "date_of_birth": "1990-04-12",
    "address": "22 Oak Ave",
}


# ------------------------------------------------------------------
# Profile validation (shared by drivers and sponsors)
# ------------------------------------------------------------------

@pytest.mark.parametrize("change, message", [
    ({"first_name": ""}, "Please fill out every field."),
    ({"last_name": "  "}, "Please fill out every field."),
    ({"address": ""}, "Please fill out every field."),
    ({"date_of_birth": ""}, "Please fill out every field."),
    ({"date_of_birth": "04/12/1990"}, "Please enter a valid date of birth."),
    ({"date_of_birth": "1990-02-30"}, "Please enter a valid date of birth."),
])
def test_profile_validation_errors(app_module, fake_db, change, message):
    conn = fake_db()

    error = app_module.save_driver_profile(conn.cursor(), 20, {**VALID_PROFILE, **change})

    assert error == message
    assert sql(conn) == []


def test_valid_profile_updates_name_and_details(app_module, fake_db):
    conn = fake_db()

    error = app_module.save_driver_profile(conn.cursor(), 20, VALID_PROFILE)

    assert error is None
    name_update = next(p for q, p in queries(conn) if q.startswith("UPDATE Users"))
    assert name_update == ("Dana", "Rivers", 20)
    assert any("INSERT INTO Driver_Profiles" in q for q in sql(conn))


# ------------------------------------------------------------------
# Drivers editing their own profile
# ------------------------------------------------------------------

def test_driver_edit_updates_own_profile_and_header_name(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Driver", user_id=20, first_name="Dana")

    response = client.post("/driver/profile/edit", data=VALID_PROFILE, follow_redirects=False)

    assert response.headers["Location"].endswith("/driver/profile")
    assert next(p for q, p in queries(conn) if q.startswith("UPDATE Users"))[2] == 20
    assert conn.committed
    with client.session_transaction() as sess:
        assert sess["last_name"] == "Rivers"  # the header shows the new name right away


def test_driver_edit_with_errors_shows_message_and_saves_nothing(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[dict(DRIVER_ROW)], fetchall_values=[[], []])
    set_session(role="Driver", user_id=20)

    response = client.post("/driver/profile/edit", data={**VALID_PROFILE, "date_of_birth": "soon"})

    assert response.status_code == 200
    assert b"valid date of birth" in response.data
    assert not conn.committed


def test_driver_profile_page_shows_their_details(client, set_session, fake_db):
    fake_db(fetchone_values=[dict(DRIVER_ROW)], fetchall_values=[[], []])
    set_session(role="Driver", user_id=20)

    response = client.get("/driver/profile")

    assert response.status_code == 200
    assert b"Dana Driver" in response.data
    assert b"1 Main St" in response.data


# ------------------------------------------------------------------
# Sponsors editing their drivers
# ------------------------------------------------------------------

def test_sponsor_can_edit_their_driver(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[dict(DRIVER_ROW)], fetchall_values=[[]])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    response = client.post("/sponsor/drivers/20/edit", data=VALID_PROFILE, follow_redirects=False)

    assert response.headers["Location"].endswith("/sponsor/drivers/20")
    assert any(q.startswith("UPDATE Users") for q in sql(conn))
    assert conn.committed


def test_sponsor_cannot_edit_driver_outside_their_program(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[dict(DRIVER_ROW, sponsor_id=None)], fetchall_values=[[]])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    response = client.post("/sponsor/drivers/20/edit", data=VALID_PROFILE, follow_redirects=False)

    assert response.headers["Location"].endswith("/sponsor/drivers")
    assert not any(q.startswith("UPDATE") for q in sql(conn))


# ------------------------------------------------------------------
# Sessions: timeout, keep-alive, logout
# ------------------------------------------------------------------

def test_session_expires_after_five_minutes_of_inactivity(client):
    with client.session_transaction() as sess:
        sess["user_id"] = 20
        sess["role"] = "Driver"
        sess["first_name"] = "Dana"
        sess["last_activity"] = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()

    response = client.get("/driver/dashboard", follow_redirects=False)

    assert response.headers["Location"].endswith("/login")
    with client.session_transaction() as sess:
        assert "user_id" not in sess


def test_session_without_activity_time_is_logged_out(client):
    with client.session_transaction() as sess:
        sess["user_id"] = 20
        sess["role"] = "Driver"

    response = client.get("/driver/dashboard", follow_redirects=False)

    assert response.headers["Location"].endswith("/login")


def test_keep_alive_refreshes_activity_time(client):
    old = (datetime.now(timezone.utc) - timedelta(minutes=4)).isoformat()
    with client.session_transaction() as sess:
        sess["user_id"] = 20
        sess["role"] = "Driver"
        sess["last_activity"] = old

    response = client.post("/session/keep-alive")

    assert response.get_json() == {"success": True}
    with client.session_transaction() as sess:
        assert sess["last_activity"] > old


def test_keep_alive_requires_login(client):
    response = client.post("/session/keep-alive")
    assert response.status_code == 401


def test_logout_clears_session(client, set_session):
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    response = client.get("/logout", follow_redirects=False)

    assert response.headers["Location"].endswith("/login")
    with client.session_transaction() as sess:
        assert dict(sess) == {}


# ------------------------------------------------------------------
# Notification bell
# ------------------------------------------------------------------

def test_mark_notifications_read_only_affects_current_user(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Driver", user_id=20)

    response = client.post("/notifications/read")

    assert response.status_code == 204
    query, params = queries(conn)[0]
    assert query.startswith("UPDATE Notifications")
    assert params == (20,)
    assert conn.committed


def test_mark_notifications_read_requires_login(client, fake_db):
    conn = fake_db()

    response = client.post("/notifications/read")

    assert response.status_code == 401
    assert sql(conn) == []


def test_bell_shows_unread_count_and_recent_notifications(client, set_session, fake_db):
    # The Team page's own queries run first (members, invites, company name),
    # then the bell's (unread count, recent notifications).
    fake_db(
        fetchone_values=[{"company_name": "Acme"}, {"c": 3}],
        fetchall_values=[[], [], [{"notification_id": 1, "subject": "Points added",
                              "message": "Acme added 10 points", "is_read": 0,
                              "created_at": datetime(2026, 10, 21, 14, 0)}]],
    )
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    response = client.get("/sponsor/team")

    assert b'class="notif-count">3<' in response.data
    assert b"Acme added 10 points" in response.data


# ------------------------------------------------------------------
# Shared header / footer
# ------------------------------------------------------------------

def test_header_shows_full_name_and_role(client, set_session, fake_db):
    fake_db()
    set_session(role="Sponsor", user_id=50, first_name="Julia", last_name="Thompson", sponsor_id=3)

    response = client.get("/sponsor/team")

    assert b"Julia Thompson" in response.data
    assert b'class="user-role">Sponsor<' in response.data


@pytest.mark.parametrize("role, path, expected, not_expected", [
    ("Sponsor", "/sponsor/team", [b"/sponsor/drivers", b"/sponsor/catalog"], [b"/driver/points", b"/admin/"]),
    ("Driver", "/driver/sponsors", [b"/driver/points", b"/driver/catalog"], [b"/sponsor/drivers", b"/admin/"]),
])
def test_header_links_match_the_role(client, set_session, fake_db, role, path, expected, not_expected):
    fake_db()
    set_session(role=role, user_id=50, sponsor_id=3)

    html = client.get(path).data
    header = html[html.index(b'class="site-header"'):html.index(b"</header>")]

    for link in expected:
        assert link in header
    for link in not_expected:
        assert link not in header


def test_footer_is_on_logged_out_pages(client):
    html = client.get("/sponsor-request").data
    assert b'class="site-footer"' in html


# ------------------------------------------------------------------
# Display helpers
# ------------------------------------------------------------------

def test_local_time_leaves_plain_dates_and_blanks_alone(app_module):
    assert app_module.to_local_time(date(2026, 10, 22)) == date(2026, 10, 22)
    assert app_module.to_local_time(None) is None


@pytest.mark.parametrize("age, text, highlighted", [
    (timedelta(minutes=10), "just now", False),
    (timedelta(hours=5), "waiting 5 hrs", False),
    (timedelta(days=1, hours=2), "waiting 1 day", False),
    (timedelta(days=4), "waiting 4 days", True),
])
def test_waiting_label(app_module, age, text, highlighted):
    when = datetime.now(timezone.utc).replace(tzinfo=None) - age

    label = str(app_module.waiting_filter(when))

    assert text in label
    assert ("waiting long" in label) == highlighted
