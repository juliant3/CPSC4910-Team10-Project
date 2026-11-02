# To run tests:
# 1 - Install dependencies: python -m pip install -r frontend/requirements-test.txt
# 2 - Run the test suite: python -m pytest

import pytest
from werkzeug.security import check_password_hash, generate_password_hash


RESET_LINK = {"user_id": 10, "purpose": "Reset", "email": "driver@example.com", "first_name": "Dana"}
SETUP_LINK = {"user_id": 10, "purpose": "Setup", "email": "driver@example.com", "first_name": "Dana"}
TOKEN = "abcDEF123-_token"


def executed(connection):
    return connection._cursor.executed


@pytest.fixture
def sent_links(monkeypatch, app_module):
    sent = []
    monkeypatch.setattr(
        app_module, "send_password_reset_link",
        lambda email, first_name, token: sent.append((email, token)) or True,
    )
    return sent


# ------------------------------------------------------------------
# Requesting a link
# ------------------------------------------------------------------

def test_forgot_password_stores_only_a_hash_of_the_emailed_token(client, fake_db, app_module, sent_links):
    connection = fake_db(fetchone_values=[{"user_id": 10, "email": "driver@example.com", "first_name": "Dana"}])

    response = client.post("/forgot-password", data={"email": "Driver@Example.com"})

    assert b"Check Your Email" in response.data
    assert connection.committed is True
    email, token = sent_links[0]
    assert email == "driver@example.com"

    insert = next(params for query, params in executed(connection) if "INSERT INTO Password_Tokens" in query)
    assert insert[0] == app_module.hash_token(token)
    assert token not in insert
    assert insert[2] == "Reset"
    assert insert[3] == app_module.RESET_LINK_MINUTES


def test_new_link_cancels_older_unused_links(client, fake_db, sent_links):
    connection = fake_db(fetchone_values=[{"user_id": 10, "email": "driver@example.com", "first_name": "Dana"}])

    client.post("/forgot-password", data={"email": "driver@example.com"})

    queries = [query for query, _ in executed(connection)]
    cancel = queries.index(next(q for q in queries if q.startswith("UPDATE Password_Tokens")))
    insert = queries.index(next(q for q in queries if q.startswith("INSERT INTO Password_Tokens")))
    assert cancel < insert


def test_unknown_email_gets_the_same_answer_and_no_email(client, fake_db, sent_links):
    connection = fake_db(fetchone_values=[None])

    response = client.post("/forgot-password", data={"email": "nobody@example.com"})

    assert b"Check Your Email" in response.data
    assert sent_links == []
    assert not any("Password_Tokens" in query for query, _ in executed(connection))


def test_each_request_gets_a_different_token(client, fake_db, sent_links):
    user = {"user_id": 10, "email": "driver@example.com", "first_name": "Dana"}
    fake_db(fetchone_values=[user])
    client.post("/forgot-password", data={"email": "driver@example.com"})
    fake_db(fetchone_values=[user])
    client.post("/forgot-password", data={"email": "driver@example.com"})

    assert sent_links[0][1] != sent_links[1][1]
    assert len(sent_links[0][1]) >= 40


# ------------------------------------------------------------------
# Using a link
# ------------------------------------------------------------------

def test_link_lookup_requires_unused_and_unexpired(client, fake_db, app_module):
    connection = fake_db(fetchone_values=[RESET_LINK])

    response = client.get(f"/reset-password/{TOKEN}")

    assert b"Reset Password" in response.data
    query, params = executed(connection)[0]
    assert "used_at IS NULL" in query and "expires_at > NOW()" in query
    assert params == (app_module.hash_token(TOKEN),)


@pytest.mark.parametrize("method", ["get", "post"])
def test_used_or_expired_link_is_rejected(client, fake_db, method):
    connection = fake_db(fetchone_values=[None])

    response = getattr(client, method)(
        f"/reset-password/{TOKEN}",
        data={"new_password": "NewPassword1!", "confirm_password": "NewPassword1!"},
    )

    assert b"Link Expired" in response.data
    assert not any("UPDATE Password " in query or "INSERT INTO Password " in query for query, _ in executed(connection))
    assert connection.committed is False


def test_overlong_token_is_rejected_without_a_query(client, fake_db):
    connection = fake_db()

    response = client.get("/reset-password/" + "a" * 200)

    assert b"Link Expired" in response.data
    assert executed(connection) == []


def test_reset_password_requires_matching_passwords(client, fake_db):
    connection = fake_db(fetchone_values=[RESET_LINK])

    response = client.post(
        f"/reset-password/{TOKEN}",
        data={"new_password": "NewPassword1!", "confirm_password": "DifferentPassword1!"},
    )

    assert b"The passwords do not match" in response.data
    assert connection.committed is False


def test_reset_password_requires_minimum_length(client, fake_db):
    connection = fake_db(fetchone_values=[RESET_LINK])

    response = client.post(
        f"/reset-password/{TOKEN}",
        data={"new_password": "short", "confirm_password": "short"},
    )

    assert b"at least 8 characters" in response.data
    assert connection.committed is False


def test_reset_password_cannot_reuse_current_password(client, fake_db):
    connection = fake_db(fetchone_values=[
        RESET_LINK,
        {"password_hash": generate_password_hash("SamePassword1!")},
    ])

    response = client.post(
        f"/reset-password/{TOKEN}",
        data={"new_password": "SamePassword1!", "confirm_password": "SamePassword1!"},
    )

    assert b"must be different from your current password" in response.data
    assert connection.committed is False


def test_successful_reset_uses_up_the_link_and_saves_the_hash(client, fake_db, app_module):
    connection = fake_db(fetchone_values=[
        RESET_LINK,
        {"password_hash": generate_password_hash("OldPassword1!")},
    ])

    response = client.post(
        f"/reset-password/{TOKEN}",
        data={"new_password": "NewPassword1!", "confirm_password": "NewPassword1!"},
    )

    assert response.status_code == 200
    assert b"Your password has been saved" in response.data
    assert connection.committed is True

    calls = executed(connection)
    claim = next(i for i, (q, _) in enumerate(calls) if q.startswith("UPDATE Password_Tokens") and "token_hash" in q)
    save = next(i for i, (q, _) in enumerate(calls) if q.startswith("INSERT INTO Password ("))
    assert claim < save
    assert "used_at IS NULL" in calls[claim][0] and "expires_at > NOW()" in calls[claim][0]
    assert calls[claim][1] == (app_module.hash_token(TOKEN),)
    assert check_password_hash(calls[save][1][1], "NewPassword1!")

    # Every other outstanding link for the user is cancelled too.
    assert any(q.startswith("UPDATE Password_Tokens") and "WHERE user_id" in q for q, _ in calls)


def test_link_already_claimed_by_another_request_changes_nothing(client, fake_db):
    connection = fake_db(fetchone_values=[
        RESET_LINK,
        {"password_hash": generate_password_hash("OldPassword1!")},
    ])
    connection._cursor.rowcount = 0  # the claiming UPDATE matched no row

    response = client.post(
        f"/reset-password/{TOKEN}",
        data={"new_password": "NewPassword1!", "confirm_password": "NewPassword1!"},
    )

    assert b"Link Expired" in response.data
    assert connection.rolled_back is True
    assert connection.committed is False
    assert not any(q.startswith("INSERT INTO Password (") for q, _ in executed(connection))


# ------------------------------------------------------------------
# First-time password setup
# ------------------------------------------------------------------

def test_setup_link_shows_set_password_form(client, fake_db):
    fake_db(fetchone_values=[SETUP_LINK])

    response = client.get(f"/set-password/{TOKEN}")

    assert b"Set Your Password" in response.data


def test_setup_link_saves_password_without_comparing_to_old_one(client, fake_db):
    connection = fake_db(fetchone_values=[SETUP_LINK])

    response = client.post(
        f"/set-password/{TOKEN}",
        data={"new_password": "FirstPassword1!", "confirm_password": "FirstPassword1!"},
    )

    assert b"Your password has been saved" in response.data
    assert connection.committed is True
    assert not any("SELECT password_hash" in q for q, _ in executed(connection))


def test_password_links_point_at_the_right_page(app_module):
    assert app_module.password_link("tok", "Reset").endswith("/reset-password/tok")
    assert app_module.password_link("tok", "Setup").endswith("/set-password/tok")


def test_new_accounts_get_an_unguessable_password(app_module):
    first, second = app_module.unusable_password_hash(), app_module.unusable_password_hash()
    assert first != second
    assert not check_password_hash(first, "")
