# To run tests: 
# 1 - Install dependencies: python -m pip install -r frontend/requirements-test.txt
# 2 - Run the test suite: python -m pytest

from werkzeug.security import generate_password_hash

def test_valid_login_sets_session_and_redirects(client, fake_db):
    fake_db(
        fetchone_values=[
            {
                "user_id": 7,
                "first_name": "Driver",
                "role": "Driver",
                "password_hash": generate_password_hash("CorrectPass1!"),
            }
        ]
    )

    response = client.post(
        "/login",
        data={"username": "driver@example.com", "password": "CorrectPass1!"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")

    with client.session_transaction() as sess:
        assert sess["user_id"] == 7
        assert sess["role"] == "Driver"
        assert sess["first_name"] == "Driver"


def test_invalid_login_shows_generic_error(client, fake_db):
    fake_db(fetchone_values=[None])

    response = client.post(
        "/login",
        data={"username": "nobody@example.com", "password": "wrong"},
    )

    assert response.status_code == 200
    assert b"Invalid email or password" in response.data


def test_logout_clears_session(client, set_session):
    set_session(role="Driver", user_id=8)

    response = client.get("/logout", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")

    with client.session_transaction() as sess:
        assert "user_id" not in sess
        assert "role" not in sess


def test_driver_cannot_open_sponsor_dashboard(client, set_session):
    set_session(role="Driver")
    response = client.get("/sponsor/dashboard", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_sponsor_cannot_open_driver_dashboard(client, set_session):
    set_session(role="Sponsor")
    response = client.get("/driver/dashboard", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_unauthenticated_user_cannot_open_sponsor_dashboard(client):
    response = client.get("/sponsor/dashboard", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
