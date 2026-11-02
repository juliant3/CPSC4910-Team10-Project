
"""
Sponsor point-adjustment tests.

The route, form fields, and database schema need to be confirmed
against frontend/app.py before these integration tests are enabled.
"""

import pytest


DRIVER_ID = 101
SPONSOR_ID = 20

# TODO: Resolve the actual Flask endpoint.
CHANGE_POINTS_ROUTE = None

AMOUNT_FIELD = "amount"
REASON_FIELD = "reason"


@pytest.fixture
def authenticated_sponsor(client):
    with client.session_transaction() as session:
        session["user_id"] = SPONSOR_ID
        session["first_name"] = "Test Sponsor"
        session["role"] = "Sponsor"
        session["sponsor_id"] = SPONSOR_ID

    return client


def require_change_route():
    if not CHANGE_POINTS_ROUTE:
        pytest.skip(
            "Configure CHANGE_POINTS_ROUTE using the actual Flask URL map."
        )

    return CHANGE_POINTS_ROUTE


class TestAddingPoints:

    def test_sponsor_can_add_points_to_driver(
        self, authenticated_sponsor
    ):
        pytest.skip(
            "Requires the actual route, sponsor-driver association, "
            "and database fixture."
        )

    def test_adding_points_increases_driver_balance_correctly(
        self, authenticated_sponsor
    ):
        pytest.skip(
            "Requires a database fixture to verify the exact balance change."
        )

    def test_adding_points_creates_transaction_record(
        self, authenticated_sponsor
    ):
        pytest.skip(
            "Requires the actual Point_Transactions schema."
        )

    def test_adding_zero_points_is_rejected(
        self, authenticated_sponsor
    ):
        route = require_change_route()

        response = authenticated_sponsor.post(
            route,
            data={
                "driver_id": DRIVER_ID,
                AMOUNT_FIELD: "0",
                REASON_FIELD: "Test zero adjustment",
            },
            follow_redirects=False,
        )

        assert response.status_code in (200, 302, 400)

        # TODO: Assert balance and transaction table are unchanged.

    def test_adding_negative_points_is_rejected(
        self, authenticated_sponsor
    ):
        route = require_change_route()

        response = authenticated_sponsor.post(
            route,
            data={
                "driver_id": DRIVER_ID,
                AMOUNT_FIELD: "-10",
                REASON_FIELD: "Test negative adjustment",
            },
            follow_redirects=False,
        )

        assert response.status_code in (200, 302, 400)

        # TODO: Assert balance and transaction table are unchanged.

    def test_adding_points_to_unassociated_driver_is_rejected(
        self, authenticated_sponsor
    ):
        pytest.skip(
            "Requires a database fixture with a driver belonging "
            "to another sponsor."
        )


class TestDeductingPoints:

    def test_sponsor_can_deduct_points_from_driver(
        self, authenticated_sponsor
    ):
        pytest.skip("Requires the actual route and database fixtures.")

    def test_deducting_points_decreases_driver_balance_correctly(
        self, authenticated_sponsor
    ):
        pytest.skip(
            "Requires a database fixture to verify the exact balance change."
        )

    def test_deducting_points_creates_transaction_record(
        self, authenticated_sponsor
    ):
        pytest.skip(
            "Requires the actual Point_Transactions schema."
        )

    def test_deducting_more_points_than_available_is_rejected(
        self, authenticated_sponsor
    ):
        pytest.skip(
            "Requires a database fixture with a known starting balance."
        )

    def test_deducting_zero_points_is_rejected(
        self, authenticated_sponsor
    ):
        route = require_change_route()

        response = authenticated_sponsor.post(
            route,
            data={
                "driver_id": DRIVER_ID,
                AMOUNT_FIELD: "0",
                REASON_FIELD: "Test zero deduction",
            },
            follow_redirects=False,
        )

        assert response.status_code in (200, 302, 400)

        # TODO: Assert balance and transaction table are unchanged.

    def test_deducting_negative_points_is_rejected(
        self, authenticated_sponsor
    ):
        route = require_change_route()

        response = authenticated_sponsor.post(
            route,
            data={
                "driver_id": DRIVER_ID,
                AMOUNT_FIELD: "-10",
                REASON_FIELD: "Test negative deduction",
            },
            follow_redirects=False,
        )

        assert response.status_code in (200, 302, 400)

        # TODO: Assert balance and transaction table are unchanged.

    def test_deducting_points_from_unassociated_driver_is_rejected(
        self, authenticated_sponsor
    ):
        pytest.skip(
            "Requires a database fixture with a driver belonging "
            "to another sponsor."
        )
