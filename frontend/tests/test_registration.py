def registration_payload(**overrides):
    payload = {
        "role": "Driver",
        "first_name": "Test",
        "last_name": "Driver",
        "email": "driver@example.com",
        "password": "Password1!",
        "confirm_password": "Password1!",
        "invite_code": "ABCDE",
        "company_name": "",
        "phone": "",
    }
    payload.update(overrides)
    return payload


def test_registration_rejects_invalid_role(client):
    response = client.post(
        "/register",
        data=registration_payload(role="NotARole"),
    )
    assert b"Please select a valid account type" in response.data


def test_registration_requires_matching_passwords(client):
    response = client.post(
        "/register",
        data=registration_payload(confirm_password="Different1!"),
    )
    assert b"Passwords do not match" in response.data


def test_registration_requires_minimum_password_length(client):
    response = client.post(
        "/register",
        data=registration_payload(password="short", confirm_password="short"),
    )
    assert b"Password must be at least 8 characters" in response.data


def test_sponsor_registration_requires_company_name(client):
    response = client.post(
        "/register",
        data=registration_payload(
            role="Sponsor",
            company_name="",
            invite_code="",
        ),
    )
    assert b"Company name is required for Sponsor accounts" in response.data


def test_driver_registration_requires_invite_code(client, fake_db):
    fake_db()
    response = client.post(
        "/register",
        data=registration_payload(invite_code=""),
    )
    assert b"Sponsor invite code is required for Driver accounts" in response.data


def test_driver_registration_rejects_invalid_invite_code(client, fake_db):
    fake_db(fetchone_values=[None])
    response = client.post(
        "/register",
        data=registration_payload(invite_code="BAD99"),
    )
    assert b"Invalid sponsor invite code" in response.data


def test_duplicate_email_is_rejected(client, fake_db):
    # Sponsor registration skips invite lookup, so first fetchone() is duplicate-email lookup.
    fake_db(fetchone_values=[{"user_id": 55}])

    response = client.post(
        "/register",
        data=registration_payload(
            role="Sponsor",
            company_name="Test Trucking",
            invite_code="",
        ),
    )

    assert b"An account with this email already exists" in response.data


def test_successful_driver_registration_creates_user_password_and_driver(client, fake_db):
    connection = fake_db(
        fetchone_values=[
            {"sponsor_id": 22},  # invite-code lookup
            None,  # duplicate-email lookup
        ],
        lastrowid=101,
    )

    response = client.post(
        "/register",
        data=registration_payload(),
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
    assert connection.committed is True

    sql = "\n".join(query for query, _ in connection._cursor.executed)
    assert "INSERT INTO Users" in sql
    assert "INSERT INTO Password" in sql
    assert "INSERT INTO Drivers" in sql

    driver_insert = next(
        params
        for query, params in connection._cursor.executed
        if "INSERT INTO Drivers" in query
    )
    assert driver_insert == (101, 22)


def test_successful_sponsor_registration_creates_all_sponsor_records(client, fake_db):
    connection = fake_db(fetchone_values=[None], lastrowid=202)

    response = client.post(
        "/register",
        data=registration_payload(
            role="Sponsor",
            company_name="Test Trucking",
            invite_code="",
            email="sponsor@example.com",
        ),
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert connection.committed is True

    sql = "\n".join(query for query, _ in connection._cursor.executed)
    assert "INSERT INTO Users" in sql
    assert "INSERT INTO Password" in sql
    assert "INSERT INTO Sponsors" in sql
    assert "INSERT INTO Sponsor_Users" in sql
