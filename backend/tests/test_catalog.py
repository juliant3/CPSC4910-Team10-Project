
"""
Catalog checkout tests.

Route and database details must be mapped to the actual catalog
blueprint before these tests can exercise the checkout flow.
"""

import pytest


# TODO: Set these from frontend/ebay_catelog.py.
PURCHASE_ROUTE = None
ITEM_ID = 501
DRIVER_ID = 101


@pytest.fixture
def authenticated_driver(client):
    with client.session_transaction() as session:
        session["user_id"] = DRIVER_ID
        session["first_name"] = "Test"
        session["role"] = "Driver"
        session["driver_id"] = DRIVER_ID

    return client


def require_purchase_route():
    if not PURCHASE_ROUTE:
        pytest.skip(
            "Configure PURCHASE_ROUTE from the actual catalog "
            "purchase endpoint before enabling checkout tests."
        )

    return PURCHASE_ROUTE


class TestCatalogPurchases:

    def test_purchase_succeeds_when_driver_has_enough_points(
        self, authenticated_driver
    ):
        pytest.skip(
            "Requires the real product/order schema and mocked DB fixture; "
            "assert successful order creation and exact point deduction."
        )

    def test_purchase_fails_when_driver_has_insufficient_points(
        self, authenticated_driver
    ):
        route = require_purchase_route()

        response = authenticated_driver.post(
            route,
            data={"item_id": ITEM_ID},
            follow_redirects=False
        )

        assert response.status_code in (200, 302, 400)

        # TODO: Assert no order was inserted and no points were deducted.

    def test_purchase_fails_when_driver_has_zero_points(
        self, authenticated_driver
    ):
        pytest.skip("Requires the real checkout and database fixtures.")

    def test_failed_purchase_does_not_deduct_points(
        self, authenticated_driver
    ):
        pytest.skip(
            "Requires a database fixture to compare the balance before/after."
        )

    def test_failed_purchase_does_not_create_order(
        self, authenticated_driver
    ):
        pytest.skip(
            "Requires a database fixture to assert no Orders row was inserted."
        )

    def test_purchase_succeeds_when_balance_exactly_matches_price(
        self, authenticated_driver
    ):
        pytest.skip(
            "Requires real item pricing and checkout database fixtures."
        )

    def test_driver_cannot_purchase_with_negative_point_balance(
        self, authenticated_driver
    ):
        pytest.skip("Requires the real checkout and database fixtures.")
