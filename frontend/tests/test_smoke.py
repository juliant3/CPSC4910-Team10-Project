def test_login_page_loads(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert b"Log In" in response.data


def test_register_page_loads(client):
    response = client.get("/register")
    assert response.status_code == 200
    assert b"Create Account" in response.data


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


def test_sponsor_home_renders_dashboard(client, set_session):
    set_session(role="Sponsor", user_id=12, first_name="Sam")
    response = client.get("/")
    assert response.status_code == 200
    assert b"Manage your drivers and applications" in response.data


def test_driver_home_renders_with_database_stub(client, set_session, fake_db):
    set_session(role="Driver", user_id=13, first_name="Dana")
    fake_db(fetchone_values=[{"company_name": "Test Sponsor"}])
    response = client.get("/")
    assert response.status_code == 200
    assert b"Test Sponsor" in response.data
