"""Sponsor-defined eBay catalog tests. API and database are always mocked."""

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


@pytest.fixture
def selected_sponsor(monkeypatch):
    """Mock the sponsor association for rendering; other tests exercise it directly."""
    monkeypatch.setattr(ebay_catalog, "_catalog_context", lambda role: (3, None))


@pytest.fixture
def saved_query(monkeypatch):
    terms = {3: "golf club", 8: "headphones"}
    monkeypatch.setattr(ebay_catalog, "_saved_search", lambda sponsor_id: terms.get(sponsor_id, ""))
    return terms


def test_sponsor_get_uses_saved_term_not_free_text_query(client, set_session, selected_sponsor, saved_query, fake_ebay):
    calls = fake_ebay(SEARCH_RESULT)
    set_session(role="Sponsor", user_id=50, sponsor_id=3)
    response = client.get("/sponsor/catalog?q=ignored&page=2")
    assert response.status_code == 200
    assert b"Heavy Duty Golf Club Bag" in response.data
    assert b"49.99" in response.data
    assert b"golf club" in response.data
    assert calls[0][1] == {"q": "golf club", "limit": 10, "offset": 10}
    assert b'Save catalog' in response.data


def test_driver_cannot_modify_search_and_only_sees_saved_term(client, set_session, selected_sponsor, saved_query, fake_ebay):
    calls = fake_ebay(SEARCH_RESULT)
    set_session(role="Driver", user_id=20)
    response = client.get("/driver/catalog?q=gaming+pc&page=1")
    assert response.status_code == 200
    assert b"golf club" in response.data
    assert b'Save catalog' not in response.data
    assert b'name="search_term"' not in response.data
    assert calls[0][1]["q"] == "golf club"
    assert client.post("/driver/catalog", data={"search_term": "gaming pc"}).status_code == 405


def test_no_catalog_configured_yet(client, set_session, selected_sponsor, fake_ebay, monkeypatch):
    calls = fake_ebay(SEARCH_RESULT)
    monkeypatch.setattr(ebay_catalog, "_saved_search", lambda sponsor_id: "")
    set_session(role="Driver", user_id=20)
    resp = client.get("/driver/catalog")
    assert b"hasn&#39;t created a catalog yet" in resp.data or b"hasn\'t created a catalog yet" in resp.data
    assert calls == []


def test_sponsor_can_save_single_search_term(client, set_session, fake_db, fake_ebay):
    conn = fake_db()
    calls = fake_ebay(SEARCH_RESULT)
    set_session(role="Sponsor", user_id=50, sponsor_id=3, catalog_csrf="abc123")
    resp = client.post("/sponsor/catalog", data={"search_term": "  golf   club  ", "catalog_csrf": "abc123"})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/sponsor/catalog?saved=1")
    assert conn.committed
    assert any(
        "INSERT INTO Sponsor_Catalog_Settings" in q and p == (3, "golf club")
        for q, p in conn.cursor().executed
    )
    assert calls == []


def test_save_is_scoped_to_sponsor_organization_not_login_user_id(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Sponsor", user_id=50, sponsor_id=8, catalog_csrf="key")
    client.post("/sponsor/catalog", data={"search_term": "wireless earbuds", "catalog_csrf": "key"})
    assert any(p == (8, "wireless earbuds") for q, p in conn.cursor().executed)


def test_post_requires_csrf_and_valid_term(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Sponsor", user_id=50, sponsor_id=3, catalog_csrf="abc")
    assert client.post("/sponsor/catalog", data={"search_term": "x", "catalog_csrf": "bad"}).status_code == 403
    assert not conn.cursor().executed
    resp = client.post("/sponsor/catalog", data={"search_term": "  ", "catalog_csrf": "abc"})
    assert resp.status_code == 302
    assert not conn.cursor().executed
    resp = client.post("/sponsor/catalog", data={"search_term": "z" * 121, "catalog_csrf": "abc"})
    assert resp.status_code == 302
    assert not conn.cursor().executed


def test_sponsor_search_term_is_read_from_db(client, set_session, fake_db, fake_ebay):
    conn = fake_db(fetchone_values=[{"search_term": "hiking boots"}])
    calls = fake_ebay(SEARCH_RESULT)
    set_session(role="Sponsor", user_id=50, sponsor_id=3)
    resp = client.get("/sponsor/catalog")
    assert resp.status_code == 200
    assert calls[0][1]["q"] == "hiking boots"
    assert any("SELECT search_term FROM Sponsor_Catalog_Settings" in q and p == (3,) for q, p in conn.cursor().executed)


def test_driver_uses_current_sponsor_and_rejects_unknown_sponsor(client, set_session, app_module, fake_db, saved_query, fake_ebay, monkeypatch):
    fake_db()
    calls = fake_ebay(SEARCH_RESULT)
    memberships = [
        {"sponsor_id": 3, "status": "Active", "points_balance": 10, "company_name": "Acme", "point_value": 0.01},
        {"sponsor_id": 8, "status": "Active", "points_balance": 50, "company_name": "Blue", "point_value": 0.01},
    ]
    monkeypatch.setattr(app_module, "get_driver_sponsorships", lambda cursor, driver_id: memberships)
    set_session(role="Driver", user_id=20, driver_sponsor_id=8)
    client.get("/driver/catalog")
    assert calls[-1][1]["q"] == "headphones"
    with client.session_transaction() as session:
        session["driver_sponsor_id"] = 999
    client.get("/driver/catalog")
    assert calls[-1][1]["q"] == "golf club"  # rejected 999; falls back to sponsor 3


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
def test_catalog_page_number_is_sanitized(client, set_session, selected_sponsor, saved_query, fake_ebay, page, offset):
    calls = fake_ebay(SEARCH_RESULT)
    set_session(role="Sponsor", user_id=50, sponsor_id=3)
    client.get("/sponsor/catalog?page=" + page)
    assert calls[-1][1]["offset"] == offset


def test_item_details_remain_available(client, set_session, selected_sponsor, saved_query, fake_ebay):
    calls = fake_ebay({"title": "Golf Club Set", "price": {"value": "49.99", "currency": "USD"}})
    set_session(role="Driver", user_id=20)
    resp = client.get("/driver/catalog/item/v1|123456789|0")
    assert resp.status_code == 200
    assert b"Golf Club Set" in resp.data
    assert calls[0][0].endswith("v1%7C123456789%7C0")


@pytest.mark.parametrize("bad_id", ["123", "v1|abc|0", "v1|123456789|0/../../x", "../admin"])
def test_item_details_reject_bad_id(client, set_session, selected_sponsor, saved_query, fake_ebay, bad_id):
    calls = fake_ebay({})
    set_session(role="Driver", user_id=20)
    assert client.get("/driver/catalog/item/" + bad_id).status_code == 404
    assert not calls


def test_role_access_to_catalog(client, set_session, selected_sponsor):
    set_session(role="Admin", user_id=1)
    resp = client.get("/driver/catalog")
    assert resp.status_code == 302
    set_session(role="Driver", user_id=20)
    resp = client.post("/sponsor/catalog", data={"search_term": "x"})
    assert resp.status_code == 302
