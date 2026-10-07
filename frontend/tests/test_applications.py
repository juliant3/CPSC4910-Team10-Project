# To run tests: 
# 1 - Install dependencies: python -m pip install -r frontend/requirements-test.txt
# 2 - Run the test suite: python -m pytest

def test_sponsor_applications_requires_login(client):
    response = client.get("/sponsor/applications", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


def test_driver_cannot_access_sponsor_applications(client, set_session):
    set_session(role="Driver")
    response = client.get("/sponsor/applications", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_invalid_application_token_is_rejected(client, fake_db):
    fake_db(fetchone_values=[None])
    response = client.get("/apply/not-a-real-token")
    assert response.status_code == 200
    assert b"invalid or has expired" in response.data


def test_used_application_token_cannot_be_reused(client, fake_db):
    fake_db(
        fetchone_values=[
            {
                "application_id": 1,
                "applicant_email": "driver@example.com",
                "driver_id": None,
                "sponsor_id": 2,
                "status": "Accepted",
            }
        ]
    )

    response = client.get("/apply/already-used")
    assert b"already been submitted or decided" in response.data


def test_valid_pending_application_link_loads_form(client, fake_db):
    fake_db(
        fetchone_values=[
            {
                "application_id": 1,
                "applicant_email": "driver@example.com",
                "driver_id": None,
                "sponsor_id": 2,
                "status": "Pending",
            }
        ],
        fetchall_values=[[]],
    )

    response = client.get("/apply/good-token")
    assert response.status_code == 200
    assert b"Submit Application" in response.data
    assert b"Complete Your Application" in response.data


def test_application_requires_all_core_fields(client, fake_db):
    fake_db(
        fetchone_values=[
            {
                "application_id": 1,
                "applicant_email": "driver@example.com",
                "driver_id": None,
                "sponsor_id": 2,
                "status": "Pending",
            }
        ],
        fetchall_values=[[]],
    )

    response = client.post(
        "/apply/good-token",
        data={
            "first_name": "Test",
            "last_name": "Driver",
            "date_of_birth": "",
            "address": "123 Main St",
        },
    )

    assert b"Please fill out all required fields" in response.data
