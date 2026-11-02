import pytest

import ebay_catalog as catalog_module


DRIVER_ID = 101
SPONSOR_ID = 20


class CheckoutDB:
    """Fake database for catalog checkout tests."""

    def __init__(self, points_balance=1000):
        self.points_balance = points_balance
        self.orders = []
        self.order_items = []
        self.notifications = []
        self.committed = False

    def cursor(self, *args, **kwargs):
        return CheckoutCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class CheckoutCursor:
    def __init__(self, db):
        self.db = db
        self.result = None

    def execute(self, query, params=None):
        query_lower = " ".join(query.lower().split())
        params = params or ()

        if "select points_balance" in query_lower:
            self.result = {
                "points_balance": self.db.points_balance
            }

        elif "update driver_sponsors" in query_lower:
            amount = params[0]

            if self.db.points_balance >= amount:
                self.db.points_balance -= amount
                self.result = None
            else:
                self.result = None

        elif "insert into orders" in query_lower:
            self.db.orders.append(params)

        elif "insert into order_items" in query_lower:
            self.db.order_items.append(params)

        elif "insert into notifications" in query_lower:
            self.db.notifications.append(params)

        else:
            self.result = None

    def fetchone(self):
        return self.result

    def fetchall(self):
        return []

    def close(self):
        pass


def setup_driver(client):
    with client.session_transaction() as session:
        session["user_id"] = DRIVER_ID
        session["role"] = "Driver"
        session["cart"] = {
            str(SPONSOR_ID): [
                {
                    "item_id": "TEST-ITEM-1",
                    "title": "Test Product",
                    "price": 100,
                    "quantity": 1,
                }
            ]
        }


def configure_catalog(monkeypatch, db):
    monkeypatch.setattr(catalog_module, "get_db", lambda: db)

    # Keep checkout tests focused on checkout behavior rather than
    # authentication, CSRF, or external website helper behavior.
    monkeypatch.setattr(catalog_module, "_authorize", lambda *args, **kwargs: None)
    monkeypatch.setattr(catalog_module, "_check_csrf", lambda *args, **kwargs: None)

    monkeypatch.setattr(
        catalog_module,
        "_driver_cart_context",
        lambda *args, **kwargs: {
            "sponsor_id": SPONSOR_ID,
            "driver_id": DRIVER_ID,
        },
    )

    monkeypatch.setattr(
        catalog_module,
        "_cart_items",
        lambda *args, **kwargs: [
            {
                "item_id": "TEST-ITEM-1",
                "title": "Test Product",
                "price": 100,
                "quantity": 1,
            }
        ],
    )

    monkeypatch.setattr(
        catalog_module,
        "_website_helpers",
        lambda *args, **kwargs: {},
    )


def test_checkout_succeeds_with_sufficient_points(client, monkeypatch):
    db = CheckoutDB(points_balance=1000)
    configure_catalog(monkeypatch, db)
    setup_driver(client)

    response = client.post(
        "/driver/cart/checkout",
        follow_redirects=False,
    )

    assert response.status_code in (200, 302)
    assert db.points_balance == 900
    assert len(db.orders) == 1
    assert len(db.order_items) >= 1
    assert db.committed is True


def test_checkout_rejects_insufficient_points(client, monkeypatch):
    db = CheckoutDB(points_balance=50)
    configure_catalog(monkeypatch, db)
    setup_driver(client)

    response = client.post(
        "/driver/cart/checkout",
        follow_redirects=False,
    )

    assert response.status_code in (200, 302)
    assert db.points_balance == 50
    assert len(db.orders) == 0
    assert db.committed is False


def test_checkout_does_not_deduct_points_when_balance_is_insufficient(
    client, monkeypatch
):
    db = CheckoutDB(points_balance=99)
    configure_catalog(monkeypatch, db)
    setup_driver(client)

    client.post("/driver/cart/checkout")

    assert db.points_balance == 99


def test_checkout_creates_order_record(client, monkeypatch):
    db = CheckoutDB(points_balance=1000)
    configure_catalog(monkeypatch, db)
    setup_driver(client)

    client.post("/driver/cart/checkout")

    assert len(db.orders) == 1


def test_checkout_creates_order_item_record(client, monkeypatch):
    db = CheckoutDB(points_balance=1000)
    configure_catalog(monkeypatch, db)
    setup_driver(client)

    client.post("/driver/cart/checkout")

    assert len(db.order_items) >= 1


def test_checkout_reduces_balance_by_purchase_total(client, monkeypatch):
    db = CheckoutDB(points_balance=500)
    configure_catalog(monkeypatch, db)
    setup_driver(client)

    client.post("/driver/cart/checkout")

    assert db.points_balance == 400


def test_checkout_requires_driver_session(client, monkeypatch):
    db = CheckoutDB(points_balance=1000)
    configure_catalog(monkeypatch, db)

    response = client.post(
        "/driver/cart/checkout",
        follow_redirects=False,
    )

    assert response.status_code in (302, 401, 403)
    assert db.points_balance == 1000
    assert len(db.orders) == 0