def test_login_page_loads(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert b"Log In" in response.data


def test_public_registration_is_closed(client, fake_db):
    conn = fake_db()

    get_response = client.get("/register", follow_redirects=False)
    post_response = client.post("/register", data={
        "role": "Sponsor",
        "first_name": "Sneaky",
        "last_name": "Sponsor",
        "email": "sneaky@example.com",
        "password": "password123",
        "confirm_password": "password123",
        "company_name": "Sneaky Co",
    }, follow_redirects=False)

    for response in (get_response, post_response):
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/sponsor-request")
    assert conn.cursor().executed == []


def test_forgot_password_page_loads(client):
    response = client.get("/forgot-password")
    assert response.status_code == 200
    assert b"Forgot Password" in response.data


def test_home_requires_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_core_routes_are_registered(app):
    endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}
    expected = {
        "home",
        "login",
        "logout",
        "register",
        "forgot_password",
        "verify_reset_code",
        "reset_password",
        "apply",
        "sponsor_applications",
        "sponsor_drivers",
        "sponsor_dashboard",
        "driver_profile",
        "driver_dashboard",
        "admin_pending_applications",
    }
    assert expected <= endpoints


def test_sponsor_home_renders_dashboard(client, set_session, fake_db):
    set_session(role="Sponsor", user_id=12, first_name="Sam")
    fake_db(
        fetchone_values=[
            {"active_drivers": 3, "points_held": 1200},
            {"c": 250},
            {"company_name": "Acme Freight"},
        ],
        fetchall_values=[[], []],
    )

    response = client.get("/")

    assert response.status_code == 200
    assert b"Welcome back, Sam" in response.data
    assert b"Acme Freight" in response.data
    assert b"Manage your drivers, applications, and rewards" in response.data


def test_driver_home_redirects_to_driver_dashboard(client, set_session):
    set_session(role="Driver", user_id=13, first_name="Dana")
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/driver/dashboard")


def test_database_times_are_shown_in_eastern_time(app_module):
    from datetime import datetime

    # 00:12 UTC on Oct 21 is still Tuesday evening, Oct 20, in Clemson.
    shown = app_module.to_local_time(datetime(2026, 10, 21, 0, 12))
    assert (shown.month, shown.day, shown.hour) == (10, 20, 20)
    assert shown.strftime("%A") == "Tuesday"
