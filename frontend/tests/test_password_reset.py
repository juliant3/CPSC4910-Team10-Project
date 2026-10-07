# To run tests: 
# 1 - Install dependencies: python -m pip install -r frontend/requirements-test.txt
# 2 - Run the test suite: python -m pytest

from werkzeug.security import generate_password_hash


def test_verify_reset_code_requires_reset_session(client):
    response = client.post(
        "/verify-reset-code",
        data={"code": "123456"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/forgot-password")


def test_wrong_reset_code_is_rejected(client):
    with client.session_transaction() as sess:
        sess["reset_user_id"] = 10
        sess["reset_email"] = "driver@example.com"

    response = client.post("/verify-reset-code", data={"code": "000000"})
    assert b"Invalid verification code" in response.data


def test_correct_reset_code_marks_session_verified(client, app_module):
    with client.session_transaction() as sess:
        sess["reset_user_id"] = 10
        sess["reset_email"] = "driver@example.com"

    response = client.post(
        "/verify-reset-code",
        data={"code": app_module.RESET_CODE},
    )

    assert response.status_code == 200
    with client.session_transaction() as sess:
        assert sess["reset_verified"] is True


def test_reset_password_requires_matching_passwords(client):
    with client.session_transaction() as sess:
        sess["reset_user_id"] = 10
        sess["reset_verified"] = True

    response = client.post(
        "/reset-password",
        data={
            "new_password": "NewPassword1!",
            "confirm_password": "DifferentPassword1!",
        },
    )

    assert b"The passwords do not match" in response.data


def test_reset_password_cannot_reuse_current_password(client, fake_db):
    connection = fake_db(
        fetchone_values=[
            {"password_hash": generate_password_hash("SamePassword1!")}
        ]
    )

    with client.session_transaction() as sess:
        sess["reset_user_id"] = 10
        sess["reset_verified"] = True

    response = client.post(
        "/reset-password",
        data={
            "new_password": "SamePassword1!",
            "confirm_password": "SamePassword1!",
        },
    )

    assert b"must be different from your current password" in response.data
    assert connection.committed is False


def test_successful_password_reset_updates_hash_and_clears_reset_session(client, fake_db):
    connection = fake_db(
        fetchone_values=[
            {"password_hash": generate_password_hash("OldPassword1!")}
        ]
    )

    with client.session_transaction() as sess:
        sess["reset_user_id"] = 10
        sess["reset_email"] = "driver@example.com"
        sess["reset_verified"] = True

    response = client.post(
        "/reset-password",
        data={
            "new_password": "NewPassword1!",
            "confirm_password": "NewPassword1!",
        },
    )

    assert response.status_code == 200
    assert b"Password reset successfully" in response.data
    assert connection.committed is True
    assert any(
        "UPDATE Password" in query
        for query, _ in connection._cursor.executed
    )

    with client.session_transaction() as sess:
        assert "reset_user_id" not in sess
        assert "reset_email" not in sess
        assert "reset_verified" not in sess
