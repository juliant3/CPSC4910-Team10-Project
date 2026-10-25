"""Sponsor-defined eBay catalog, cart, and purchase tests. API and database are always mocked."""

from decimal import Decimal

import pytest

import ebay_catalog


@pytest.fixture
def fake_ebay(monkeypatch):
    calls = []

    def install(response):
        def get(path, params=None):
            calls.append((path, params))
            if isinstance(response, Exception):
                raise response
            if callable(response):
                return response(path)
            return response
        monkeypatch.setattr(ebay_catalog, "_ebay_get", get)
        return calls
    return install


SEARCH_RESULT = {
    "total": 21,
    "next": "https://api.ebay.com/...offset=10",
    "itemSummaries": [
        {
            "itemId": "v1|123456789|0",
            "title": "Heavy Duty Golf Club Bag",
            "price": {"value": "49.99", "currency": "USD"},
            "condition": "New",
            "categories": [{"categoryName": "Golf"}],
            "seller": {"username": "sportsgear"},
            "shippingOptions": [{"shippingCost": {"value": "0.00"}}],
        },
        {"itemId": "v1|987654321|0", "title": "Golf Wedges"},
    ],
}

ITEM = {
    "title": "Golf Club Set",
    "price": {"value": "12.50", "currency": "USD"},
    "image": {"imageUrl": "https://i.ebayimg.com/x.jpg"},
    "itemWebUrl": "https://www.ebay.com/itm/123456789",
}


@pytest.fixture
def selected_sponsor(monkeypatch):
    """Mock the sponsor association for rendering; other tests exercise it directly."""
    monkeypatch.setattr(ebay_catalog, "_catalog_context", lambda role: (3, None))


@pytest.fixture
def saved_types(monkeypatch):
    # Sponsor 3 offers golf and hats at $0.01 a point; sponsor 8 headphones at $0.10.
    settings = {3: (["Hats", "golf club"], Decimal("0.0100")), 8: (["Headphones"], Decimal("0.1000"))}
    monkeypatch.setattr(ebay_catalog, "_catalog_settings", lambda sponsor_id: settings.get(sponsor_id, ([], None)))
    return settings


# ------------------------------------------------------------------
# Browsing
# ------------------------------------------------------------------

def test_prices_are_shown_in_sponsor_points_not_dollars(client, set_session, selected_sponsor, saved_types, fake_ebay):
    fake_ebay(SEARCH_RESULT)
    set_session(role="Driver", user_id=20)
    resp = client.get("/driver/catalog")
    assert resp.status_code == 200
    assert b"4,999 pts" in resp.data  # $49.99 / $0.01
    assert b"49.99" not in resp.data
    assert b"USD" not in resp.data


@pytest.mark.parametrize("price,rate,points", [
    ("49.99", "0.01", 4999), ("10.001", "0.01", 1001), ("5", "0.10", 50), ("0", "0.01", 0),
    (None, "0.01", None), ("abc", "0.01", None), ("5", "0", None), ("5", None, None),
])
def test_to_points_rounds_up_and_rejects_bad_values(price, rate, points):
    assert ebay_catalog.to_points(price, rate) == points


def test_driver_defaults_to_first_item_type_and_can_filter(client, set_session, selected_sponsor, saved_types, fake_ebay):
    calls = fake_ebay(SEARCH_RESULT)
    set_session(role="Driver", user_id=20)
    resp = client.get("/driver/catalog")
    assert calls[-1][1] == {"q": "hat", "limit": 10, "offset": 0}
    assert b"Hats" in resp.data and b"golf club" in resp.data
    client.get("/driver/catalog?type=golf+club&page=2")
    assert calls[-1][1] == {"q": "golf club", "limit": 10, "offset": 10}


def test_driver_cannot_filter_to_a_type_the_sponsor_did_not_choose(client, set_session, selected_sponsor, saved_types, fake_ebay):
    calls = fake_ebay(SEARCH_RESULT)
    set_session(role="Driver", user_id=20)
    client.get("/driver/catalog?type=gaming+pc&q=gaming+pc")
    assert calls[-1][1]["q"] == "hat"
    assert client.post("/driver/catalog", data={"item_types": "Cars"}).status_code == 405


def test_driver_sees_add_to_cart_but_not_catalog_settings(client, set_session, selected_sponsor, saved_types, fake_ebay):
    fake_ebay(SEARCH_RESULT)
    set_session(role="Driver", user_id=20)
    resp = client.get("/driver/catalog")
    assert b"Add to cart" in resp.data
    assert b"Save catalog" not in resp.data
    assert b'name="item_types"' not in resp.data


def test_sponsor_sees_settings_and_points_but_no_cart(client, set_session, selected_sponsor, saved_types, fake_ebay):
    fake_ebay(SEARCH_RESULT)
    set_session(role="Sponsor", user_id=50, sponsor_id=3)
    resp = client.get("/sponsor/catalog")
    assert resp.status_code == 200
    assert b"Save catalog" in resp.data
    assert b'value="Hats" checked' in resp.data
    assert b'value="golf club" checked' in resp.data  # sponsor's own type stays listed
    assert b'value="Cars" ' in resp.data and b'value="Cars" checked' not in resp.data
    assert b"4,999 pts" in resp.data
    assert b"Add to cart" not in resp.data


def test_no_catalog_configured_yet(client, set_session, selected_sponsor, fake_ebay, monkeypatch):
    calls = fake_ebay(SEARCH_RESULT)
    monkeypatch.setattr(ebay_catalog, "_catalog_settings", lambda sponsor_id: ([], Decimal("0.01")))
    set_session(role="Driver", user_id=20)
    resp = client.get("/driver/catalog")
    assert b"hasn&#39;t created a catalog yet" in resp.data or b"hasn\'t created a catalog yet" in resp.data
    assert calls == []


def test_catalog_settings_are_read_from_db(client, set_session, fake_db, fake_ebay):
    conn = fake_db(
        fetchall_values=[[{"item_type": "Watches"}, {"item_type": "Hats"}]],
        fetchone_values=[{"point_value": Decimal("0.05")}],
    )
    calls = fake_ebay(SEARCH_RESULT)
    set_session(role="Sponsor", user_id=50, sponsor_id=3)
    resp = client.get("/sponsor/catalog")
    assert resp.status_code == 200
    assert calls[0][1]["q"] == "hat"  # built-in order: Hats comes before Watches
    assert b"1,000 pts" in resp.data  # $49.99 / $0.05
    executed = conn.cursor().executed
    assert any("FROM Sponsor_Catalog_Types WHERE sponsor_id" in q and p == (3,) for q, p in executed)
    assert any("SELECT point_value FROM Sponsors" in q and p == (3,) for q, p in executed)


def test_driver_uses_current_sponsor_and_rejects_unknown_sponsor(client, set_session, app_module, fake_db, saved_types, fake_ebay, monkeypatch):
    fake_db()
    calls = fake_ebay(SEARCH_RESULT)
    memberships = [
        {"sponsor_id": 3, "status": "Active", "points_balance": 10, "company_name": "Acme", "point_value": 0.01},
        {"sponsor_id": 8, "status": "Active", "points_balance": 50, "company_name": "Blue", "point_value": 0.1},
    ]
    monkeypatch.setattr(app_module, "get_driver_sponsorships", lambda cursor, driver_id: memberships)
    set_session(role="Driver", user_id=20, driver_sponsor_id=8)
    resp = client.get("/driver/catalog")
    assert calls[-1][1]["q"] == "headphones"
    assert b"500 pts" in resp.data  # sponsor 8's own point value
    with client.session_transaction() as session:
        session["driver_sponsor_id"] = 999
    client.get("/driver/catalog")
    assert calls[-1][1]["q"] == "hat"  # rejected 999; falls back to sponsor 3


def test_driver_with_no_sponsor_sees_empty_catalog(client, set_session, app_module, fake_db, fake_ebay, monkeypatch):
    fake_db()
    calls = fake_ebay(SEARCH_RESULT)
    monkeypatch.setattr(app_module, "get_driver_sponsorships", lambda cursor, driver_id: [])
    set_session(role="Driver", user_id=20)
    resp = client.get("/driver/catalog")
    assert b"don&#39;t have a sponsor yet" in resp.data or b"don\'t have a sponsor yet" in resp.data
    assert calls == []


def test_inactive_sponsorship_cannot_view_catalog(client, set_session, app_module, fake_db, fake_ebay, monkeypatch):
    fake_db()
    calls = fake_ebay(SEARCH_RESULT)
    monkeypatch.setattr(app_module, "get_driver_sponsorships", lambda cursor, driver_id: [{"sponsor_id": 3, "status": "Inactive", "points_balance": 0, "company_name": "Acme", "point_value": 0.01}])
    set_session(role="Driver", user_id=20)
    resp = client.get("/driver/catalog")
    assert b"Only active sponsor memberships" in resp.data
    assert calls == []


@pytest.mark.parametrize("page,offset", [("1", 0), ("3", 20), ("0", 0), ("-4", 0), ("abc", 0)])
def test_catalog_page_number_is_sanitized(client, set_session, selected_sponsor, saved_types, fake_ebay, page, offset):
    calls = fake_ebay(SEARCH_RESULT)
    set_session(role="Sponsor", user_id=50, sponsor_id=3)
    client.get("/sponsor/catalog?page=" + page)
    assert calls[-1][1]["offset"] == offset


def test_item_details_show_points(client, set_session, selected_sponsor, saved_types, fake_ebay):
    calls = fake_ebay(ITEM)
    set_session(role="Driver", user_id=20)
    resp = client.get("/driver/catalog/item/v1|123456789|0")
    assert resp.status_code == 200
    assert b"Golf Club Set" in resp.data
    assert b"1,250 points" in resp.data
    assert b"12.50" not in resp.data
    assert b"Add to cart" in resp.data
    assert calls[0][0].endswith("v1%7C123456789%7C0")


@pytest.mark.parametrize("bad_id", ["123", "v1|abc|0", "v1|123456789|0/../../x", "../admin"])
def test_item_details_reject_bad_id(client, set_session, selected_sponsor, saved_types, fake_ebay, bad_id):
    calls = fake_ebay({})
    set_session(role="Driver", user_id=20)
    assert client.get("/driver/catalog/item/" + bad_id).status_code == 404
    assert not calls


def test_role_access_to_catalog(client, set_session, selected_sponsor):
    set_session(role="Admin", user_id=1)
    resp = client.get("/driver/catalog")
    assert resp.status_code == 302
    set_session(role="Driver", user_id=20)
    resp = client.post("/sponsor/catalog", data={"item_types": "Hats"})
    assert resp.status_code == 302


# ------------------------------------------------------------------
# Sponsor choosing item types
# ------------------------------------------------------------------

def saved_rows(conn):
    return [p for q, p in conn.cursor().executed if "INSERT INTO Sponsor_Catalog_Types" in q]


def test_sponsor_can_save_multiple_item_types(client, set_session, fake_db, fake_ebay):
    conn = fake_db()
    calls = fake_ebay(SEARCH_RESULT)
    set_session(role="Sponsor", user_id=50, sponsor_id=3, catalog_csrf="abc123")
    resp = client.post("/sponsor/catalog", data={
        "item_types": ["Cars", "Hats"],
        "custom_types": "  golf   clubs , hats,, sunglasses",
        "catalog_csrf": "abc123",
    })
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/sponsor/catalog?saved=1")
    assert conn.committed
    assert any("DELETE FROM Sponsor_Catalog_Types" in q and p == (3,) for q, p in conn.cursor().executed)
    # The typed "hats" matches the built-in type instead of being saved twice.
    assert saved_rows(conn) == [(3, "Cars"), (3, "Hats"), (3, "golf clubs"), (3, "sunglasses")]
    assert calls == []


def test_save_is_scoped_to_sponsor_organization_not_login_user_id(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Sponsor", user_id=50, sponsor_id=8, catalog_csrf="key")
    client.post("/sponsor/catalog", data={"item_types": "Watches", "catalog_csrf": "key"})
    assert saved_rows(conn) == [(8, "Watches")]


def test_post_requires_csrf_and_valid_types(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Sponsor", user_id=50, sponsor_id=3, catalog_csrf="abc")
    assert client.post("/sponsor/catalog", data={"item_types": "Hats", "catalog_csrf": "bad"}).status_code == 403
    assert not conn.cursor().executed
    resp = client.post("/sponsor/catalog", data={"custom_types": " , ", "catalog_csrf": "abc"})
    assert resp.status_code == 302
    assert not conn.cursor().executed
    resp = client.post("/sponsor/catalog", data={"custom_types": "z" * 61, "catalog_csrf": "abc"})
    assert resp.status_code == 302
    assert not conn.cursor().executed
    too_many = ",".join(f"type {n}" for n in range(21))
    resp = client.post("/sponsor/catalog", data={"custom_types": too_many, "catalog_csrf": "abc"})
    assert resp.status_code == 302
    assert not conn.cursor().executed


# ------------------------------------------------------------------
# Cart and purchases
# ------------------------------------------------------------------

ITEM_A = "v1|123456789|0"
ITEM_B = "v1|555555555|0"


def put_in_cart(client, cart, sponsor_id=3):
    with client.session_transaction() as sess:
        sess["catalog_csrf"] = "tok"
        sess["cart"] = {str(sponsor_id): cart}


def cart_in_session(client):
    with client.session_transaction() as sess:
        return sess.get("cart")


def test_add_to_cart_stores_item_per_sponsor(client, set_session, selected_sponsor, saved_types):
    set_session(role="Driver", user_id=20, catalog_csrf="tok")
    for _ in range(2):
        resp = client.post("/driver/cart/add", data={"item_id": ITEM_A, "catalog_csrf": "tok"})
        assert resp.status_code == 302
    assert cart_in_session(client) == {"3": {ITEM_A: 2}}


def test_add_to_cart_requires_csrf_and_valid_item(client, set_session, selected_sponsor, saved_types):
    set_session(role="Driver", user_id=20, catalog_csrf="tok")
    assert client.post("/driver/cart/add", data={"item_id": ITEM_A, "catalog_csrf": "nope"}).status_code == 403
    assert client.post("/driver/cart/add", data={"item_id": "../admin", "catalog_csrf": "tok"}).status_code == 404
    assert not cart_in_session(client)


def test_cart_limits_quantity(client, set_session, selected_sponsor, saved_types):
    set_session(role="Driver", user_id=20)
    put_in_cart(client, {ITEM_A: ebay_catalog.MAX_QUANTITY})
    client.post("/driver/cart/add", data={"item_id": ITEM_A, "catalog_csrf": "tok"})
    assert cart_in_session(client) == {"3": {ITEM_A: ebay_catalog.MAX_QUANTITY}}


def test_remove_from_cart(client, set_session, selected_sponsor, saved_types):
    set_session(role="Driver", user_id=20)
    put_in_cart(client, {ITEM_A: 1, ITEM_B: 2})
    client.post("/driver/cart/remove", data={"item_id": ITEM_A, "catalog_csrf": "tok"})
    assert cart_in_session(client) == {"3": {ITEM_B: 2}}


def test_cart_page_shows_points_total_and_recent_purchases(client, set_session, selected_sponsor, saved_types, fake_db, fake_ebay):
    fake_ebay(ITEM)
    fake_db(
        fetchone_values=[{"points_balance": 5000}],
        fetchall_values=[[{
            "order_id": 1, "order_number": "GD-1-ABC", "order_date": None, "status": "Placed",
            "total_points": 900, "product_name": "Trucker Hat", "quantity": 1,
            "unit_price_points": 900, "image_url": "",
        }]],
    )
    set_session(role="Driver", user_id=20)
    put_in_cart(client, {ITEM_A: 2})
    resp = client.get("/driver/cart")
    assert resp.status_code == 200
    assert b"Golf Club Set" in resp.data
    assert b"2,500 points" in resp.data  # 2 x 1,250
    assert b"2,500 points left after purchase" in resp.data
    assert b"Recently purchased" in resp.data
    assert b"Trucker Hat" in resp.data and b"GD-1-ABC" in resp.data


def test_checkout_takes_points_records_order_and_empties_cart(client, set_session, selected_sponsor, saved_types, fake_db, fake_ebay):
    fake_ebay(ITEM)
    conn = fake_db(fetchone_values=[{"product_id": 77}], lastrowid=501)
    set_session(role="Driver", user_id=20)
    put_in_cart(client, {ITEM_A: 2})
    resp = client.post("/driver/cart/checkout", data={"catalog_csrf": "tok"})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/driver/cart")
    assert conn.committed

    executed = conn.cursor().executed
    update = next(p for q, p in executed if "UPDATE Driver_Sponsors" in q)
    assert update == (2500, 20, 3, 2500)
    tx = next(p for q, p in executed if "INSERT INTO Point_Transactions" in q)
    assert tx[:4] == (20, 3, 20, -2500)
    order = next(p for q, p in executed if "INSERT INTO Orders" in q)
    assert order[1:] == (20, 3, 20, 2500, Decimal("25.00"))
    line = next(p for q, p in executed if "INSERT INTO Order_Items" in q)
    assert line == (501, 77, 2, "Golf Club Set", 1250, Decimal("12.50"))
    assert any("'Order Placed'" in q for q, p in executed)
    assert not cart_in_session(client)

    with client.session_transaction() as sess:
        assert any("Purchase successful" in message for _, message in sess["_flashes"])


def test_checkout_without_enough_points_changes_nothing(client, set_session, selected_sponsor, saved_types, fake_db, fake_ebay):
    fake_ebay(ITEM)
    conn = fake_db()
    conn.cursor().rowcount = 0  # balance didn't cover the order
    set_session(role="Driver", user_id=20)
    put_in_cart(client, {ITEM_A: 1})
    client.post("/driver/cart/checkout", data={"catalog_csrf": "tok"})
    assert not conn.committed
    assert conn.rolled_back
    assert not any("INSERT INTO Orders" in q for q, p in conn.cursor().executed)
    assert cart_in_session(client) == {"3": {ITEM_A: 1}}


def test_checkout_requires_csrf_and_items(client, set_session, selected_sponsor, saved_types, fake_db, fake_ebay):
    calls = fake_ebay(ITEM)
    conn = fake_db()
    set_session(role="Driver", user_id=20)
    put_in_cart(client, {ITEM_A: 1})
    assert client.post("/driver/cart/checkout", data={"catalog_csrf": "bad"}).status_code == 403
    put_in_cart(client, {})
    client.post("/driver/cart/checkout", data={"catalog_csrf": "tok"})
    assert not conn.cursor().executed
    assert calls == []


def test_cart_is_separate_for_each_sponsor(client, set_session, saved_types, fake_db, fake_ebay, monkeypatch):
    fake_ebay(ITEM)
    conn = fake_db()
    monkeypatch.setattr(ebay_catalog, "_catalog_context", lambda role: (8, None))
    set_session(role="Driver", user_id=20)
    put_in_cart(client, {ITEM_A: 1}, sponsor_id=3)
    client.post("/driver/cart/checkout", data={"catalog_csrf": "tok"})
    assert not any("UPDATE Driver_Sponsors" in q for q, p in conn.cursor().executed)
    assert cart_in_session(client) == {"3": {ITEM_A: 1}}
