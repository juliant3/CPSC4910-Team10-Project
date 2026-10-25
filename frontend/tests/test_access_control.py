"""
Every protected page must turn away people who shouldn't see it:
logged-out visitors go to /login, and logged-in users with the wrong
role go back home. None of these checks may touch the database.
"""

import pytest


SPONSOR_PAGES = [
    "/sponsor/dashboard",
    "/sponsor/drivers",
    "/sponsor/drivers/5",
    "/sponsor/drivers/5/edit",
    "/sponsor/applications",
    "/sponsor/recurring",
    "/sponsor/team",
    "/sponsor/catalog",
]

DRIVER_PAGES = [
    "/driver/dashboard",
    "/driver/points",
    "/driver/profile",
    "/driver/profile/edit",
    "/driver/sponsors",
    "/driver/catalog",
    "/driver/cart",
]

ADMIN_PAGES = [
    "/admin/applications",
    "/admin/sponsor-requests",
]

SPONSOR_ACTIONS = [
    "/sponsor/questions",
    "/sponsor/questions/1/delete",
    "/sponsor/applications/1/accept",
    "/sponsor/applications/1/reject",
    "/sponsor/drivers/1/points",
]

ADMIN_ACTIONS = [
    "/admin/sponsor-requests/1/approve",
    "/admin/sponsor-requests/1/reject",
]

DRIVER_ACTIONS = [
    "/driver/switch-sponsor",
    "/driver/cart/add",
    "/driver/cart/remove",
    "/driver/cart/checkout",
]

ALL_PAGES = SPONSOR_PAGES + DRIVER_PAGES + ADMIN_PAGES
ALL_ACTIONS = SPONSOR_ACTIONS + ADMIN_ACTIONS + DRIVER_ACTIONS


def wrong_roles(allowed):
    return [role for role in ("Driver", "Sponsor", "Admin") if role != allowed]


def cases(paths, allowed):
    return [(path, role) for path in paths for role in wrong_roles(allowed)]


# ------------------------------------------------------------------
# Logged out
# ------------------------------------------------------------------

@pytest.mark.parametrize("path", ALL_PAGES)
def test_logged_out_visitors_are_sent_to_login(client, path):
    response = client.get(path, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


@pytest.mark.parametrize("path", ALL_ACTIONS)
def test_logged_out_actions_are_sent_to_login(client, path):
    response = client.post(path, data={"reason": "x", "amount": "5", "action": "add"},
                           follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")


# ------------------------------------------------------------------
# Logged in with the wrong role
# ------------------------------------------------------------------

@pytest.mark.parametrize(
    "path, role",
    cases(SPONSOR_PAGES, "Sponsor") + cases(DRIVER_PAGES, "Driver") + cases(ADMIN_PAGES, "Admin"),
)
def test_wrong_role_cannot_open_page(client, set_session, path, role):
    set_session(role=role)

    response = client.get(path, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


@pytest.mark.parametrize(
    "path, role",
    cases(SPONSOR_ACTIONS, "Sponsor") + cases(ADMIN_ACTIONS, "Admin") + cases(DRIVER_ACTIONS, "Driver"),
)
def test_wrong_role_cannot_take_action(client, set_session, fake_db, path, role):
    conn = fake_db()
    set_session(role=role)

    response = client.post(path, data={"reason": "x", "amount": "5", "action": "add"},
                           follow_redirects=False)

    assert response.status_code == 302
    assert conn.cursor().executed == [], "a blocked action must not touch the database"


# ------------------------------------------------------------------
# Public pages
# ------------------------------------------------------------------

def test_about_page_is_public(client, fake_db):
    fake_db(fetchone_values=[
        {"sprint_version": "Sprint 6", "product_name": "Good Driver Program",
         "description": "Rewards for safe driving", "team_number": 10,
         "release_date": "2026-10-20"},
        {"count": 12}, {"count": 4}, {"count": 30},
    ])

    response = client.get("/about")

    assert response.status_code == 200
    assert b"Good Driver Program" in response.data


def test_login_page_is_public(client):
    assert client.get("/login").status_code == 200


def test_sponsor_request_page_is_public(client):
    assert client.get("/sponsor-request").status_code == 200
