"""
eBay catalog (frontend/ebay_catalog.py). Never calls eBay: the API helper is
replaced with canned data. Only covers behavior that should stay stable while
the catalog is still being built.
"""

import pytest

import ebay_catalog


@pytest.fixture
def no_ebay_keys(monkeypatch):
    for name in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "EBAY_ACCESS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ebay_catalog, "DEMO_ACCESS_TOKEN", "")


@pytest.fixture
def fake_ebay(monkeypatch):
    """Replace the eBay API call; records every request made."""
    calls = []

    def _install(response):
        def fake_get(path, params=None):
            calls.append((path, params))
            if isinstance(response, Exception):
                raise response
            return response
        monkeypatch.setattr(ebay_catalog, "_ebay_get", fake_get)
        return calls

    return _install


SEARCH_RESULT = {
    "total": 2,
    "itemSummaries": [
        {
            "itemId": "v1|123456789|0",
            "title": "Heavy Duty Seat Cushion",
            "price": {"value": "49.99", "currency": "USD"},
            "condition": "New",
            "categories": [{"categoryName": "Truck Accessories"}],
            "seller": {"username": "haulgear"},
            "shippingOptions": [{"shippingCost": {"value": "0.00"}}],
        },
        {"itemId": "v1|987654321|0", "title": "Dash Phone Mount"},
    ],
}


def test_catalog_explains_when_ebay_is_not_configured(client, set_session, no_ebay_keys):
    set_session(role="Driver", user_id=20)

    response = client.get("/driver/catalog?q=headphones")

    assert response.status_code == 200
    assert b"eBay is not configured yet" in response.data


def test_catalog_shows_search_results(client, set_session, fake_ebay):
    calls = fake_ebay(SEARCH_RESULT)
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    response = client.get("/sponsor/catalog?q=seat cushion")

    assert b"Heavy Duty Seat Cushion" in response.data
    assert b"Dash Phone Mount" in response.data
    assert b"49.99" in response.data
    assert calls[0][1]["q"] == "seat cushion"


def test_catalog_shows_ebay_errors_instead_of_crashing(client, set_session, fake_ebay):
    fake_ebay(ebay_catalog.CatalogError("eBay took too long to respond. Please try again."))
    set_session(role="Driver", user_id=20)

    response = client.get("/driver/catalog?q=mug")

    assert response.status_code == 200
    assert b"eBay took too long to respond" in response.data


@pytest.mark.parametrize("page, expected_offset", [("1", 0), ("3", 20), ("0", 0), ("-4", 0), ("abc", 0)])
def test_catalog_page_number_is_sanitized(client, set_session, fake_ebay, page, expected_offset):
    calls = fake_ebay(SEARCH_RESULT)
    set_session(role="Driver", user_id=20)

    client.get(f"/driver/catalog?q=mug&page={page}")

    assert calls[0][1]["offset"] == expected_offset


def test_catalog_search_text_is_capped(client, set_session, fake_ebay):
    calls = fake_ebay(SEARCH_RESULT)
    set_session(role="Driver", user_id=20)

    client.get("/driver/catalog?q=" + "a" * 500)

    assert len(calls[0][1]["q"]) == 120


def test_item_page_shows_listing(client, set_session, fake_ebay):
    calls = fake_ebay({"title": "Heavy Duty Seat Cushion", "price": {"value": "49.99", "currency": "USD"}})
    set_session(role="Driver", user_id=20)

    response = client.get("/driver/catalog/item/v1|123456789|0")

    assert response.status_code == 200
    assert b"Heavy Duty Seat Cushion" in response.data
    assert calls[0][0].endswith("v1%7C123456789%7C0")


@pytest.mark.parametrize("bad_id", ["123", "v1|abc|0", "v1|123456789|0/../../x", "../admin"])
def test_item_page_rejects_malformed_ids(client, set_session, fake_ebay, bad_id):
    calls = fake_ebay({})
    set_session(role="Driver", user_id=20)

    response = client.get(f"/driver/catalog/item/{bad_id}")

    assert response.status_code == 404
    assert calls == [], "a bad id must never be forwarded to eBay"


def test_item_page_must_match_your_role(client, set_session, fake_ebay):
    calls = fake_ebay({})
    set_session(role="Driver", user_id=20)

    response = client.get("/sponsor/catalog/item/v1|123456789|0")

    assert response.status_code == 404
    assert calls == []


def test_admins_cannot_use_the_catalog(client, set_session):
    set_session(role="Admin", user_id=1)

    response = client.get("/driver/catalog", follow_redirects=False)

    assert response.headers["Location"].endswith("/")
