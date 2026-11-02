import os
import re
import base64
import json
import secrets
import socket
import threading
import time
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from flask import Blueprint, abort, redirect, render_template, request, session, url_for, flash

catalog_bp = Blueprint("ebay_catalog", __name__)
EBAY_ROOT = "https://api.ebay.com"
EBAY_SCOPE = "https://api.ebay.com/oauth/api_scope"
PAGE_SIZE = 10
ITEM_ID_PATTERN = r"v1\|[0-9]{6,20}\|[0-9]{1,20}"

# Item types a sponsor can offer, shown in this order: label -> eBay search.
# Sponsors can also add their own type; its label is used as the search.
ITEM_TYPES = {
    "Truck Accessories": "truck accessories",
    "Car Accessories": "car accessories",
    "Cars": "cars",
    "Hats": "hat",
    "Clothing": "clothing",
    "Shoes": "shoes",
    "Headphones": "headphones",
    "Electronics": "electronics",
    "Phone Accessories": "phone accessories",
    "Tools": "tools",
    "Camping & Outdoors": "camping gear",
    "Sports & Fitness": "fitness equipment",
    "Kitchen": "kitchen gadgets",
    "Watches": "watch",
    "Travel Gear": "travel bag",
    "Toys & Games": "toys games",
}
MAX_ITEM_TYPES = 20
MAX_TYPE_LENGTH = 60
MAX_CART_ITEMS = 10
MAX_QUANTITY = 5
MAX_SEARCH_LENGTH = 80
MAX_POINT_COST = 10_000_000
# OPTIONAL quick demo: paste your Postman OAuth access token here and remove
# it before committing. It expires; client credentials below are better.
DEMO_ACCESS_TOKEN = ""
MAX_OFFSET = 9990  # eBay Browse search limits pagination to 10,000 results.

_token_lock = threading.Lock()
_cached_token = ""
_cached_until = 0.0


class CatalogError(Exception):
    """A message safe to show to someone using the catalog."""


class ItemUnavailable(CatalogError):
    """eBay no longer has the listing (it ended or was removed)."""


class _RequestTimeout(Exception):
    pass


class _RequestFailure(Exception):
    pass


def _http_json(method, url, headers=None, data=None, auth=None):
    headers = dict(headers or {})
    body = None
    if auth:
        credentials = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        headers["Authorization"] = f"Basic {credentials}"
    if data is not None:
        body = urlencode(data).encode()
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=15) as response:
            status, content = response.status, response.read()
    except HTTPError as exc:
        status, content = exc.code, exc.read()
    except (socket.timeout, TimeoutError) as exc:
        raise _RequestTimeout() from exc
    except URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            raise _RequestTimeout() from exc
        raise _RequestFailure() from exc
    except OSError as exc:
        raise _RequestFailure() from exc

    payload = json.loads(content) if content else {}
    return status, payload


def _access_token(force_refresh=False):
    global _cached_token, _cached_until

    # Optional for quick testing with an already-created Postman token.
    # This will stop working when that token expires. Use Client ID/Secret normally.
    temporary_token = (os.getenv("EBAY_ACCESS_TOKEN", "") or DEMO_ACCESS_TOKEN).strip()
    if temporary_token:
        return temporary_token

    client_id = os.getenv("EBAY_CLIENT_ID", "").strip()
    client_secret = os.getenv("EBAY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise CatalogError(
            "eBay is not configured yet. Set EBAY_CLIENT_ID and "
            "EBAY_CLIENT_SECRET on the Flask server."
        )

    with _token_lock:
        if not force_refresh and _cached_token and time.monotonic() < _cached_until:
            return _cached_token
        try:
            status, payload = _http_json(
                "POST",
                f"{EBAY_ROOT}/identity/v1/oauth2/token",
                auth=(client_id, client_secret),
                data={"grant_type": "client_credentials", "scope": EBAY_SCOPE},
            )
            if status >= 400:
                raise _RequestFailure()
            token = payload["access_token"]
            expiry = max(60, int(payload.get("expires_in", 7200)) - 120)
        except (_RequestFailure, _RequestTimeout, ValueError, KeyError, TypeError) as exc:
            raise CatalogError("Couldn't authenticate with eBay. Check your Production API credentials.") from exc
        _cached_token = token
        _cached_until = time.monotonic() + expiry
        return token


def _ebay_get(path, params=None):
    for attempt in range(2):
        headers = {
            "Authorization": f"Bearer {_access_token(force_refresh=attempt == 1)}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            "Accept": "application/json",
        }
        try:
            url = f"{EBAY_ROOT}{path}"
            if params:
                url += "?" + urlencode(params)
            status, payload = _http_json("GET", url, headers=headers)
            if status == 401 and attempt == 0 and not (os.getenv("EBAY_ACCESS_TOKEN") or DEMO_ACCESS_TOKEN):
                continue
            if status == 401:
                raise CatalogError("Your eBay access token was rejected or has expired.")
            if status == 403:
                raise CatalogError("Your eBay application doesn't have Browse API access in Production.")
            if status == 429:
                raise CatalogError("eBay's request limit has been reached. Please try again later.")
            if status in (404, 410):
                raise ItemUnavailable("This item is no longer available on eBay.")
            if status >= 400:
                raise _RequestFailure()
            return payload
        except _RequestTimeout as exc:
            raise CatalogError("eBay took too long to respond. Please try again.") from exc
        except (_RequestFailure, ValueError) as exc:
            raise CatalogError("Unable to retrieve items from eBay right now.") from exc


def _allowed_role():
    if "user_id" not in session:
        return None
    role = str(session.get("role", "")).lower()
    return role if role in ("driver", "sponsor") else None


def _website_helpers():
    # Imported only during requests because app.py registers this blueprint
    # before declaring its database and sponsor helper functions.
    import app as website
    return website


def _catalog_context(role):
    """Find the sponsor organization, NEVER trust a sponsor_id in the URL."""
    website = _website_helpers()
    if role == "sponsor":
        return website.current_sponsor_id(), None

    conn = website.get_db()
    try:
        with conn.cursor() as cursor:
            memberships = website.get_driver_sponsorships(cursor, session["user_id"])
    finally:
        conn.close()

    selected = website.pick_driver_sponsorship(memberships)
    if selected is None:
        return None, "You don't have a sponsor yet. Your catalog will appear when you join a sponsor."
    if selected.get("status") != "Active":
        return None, "Only active sponsor memberships have access to their catalogs."
    return selected["sponsor_id"], None


def _sort_item_types(types):
    """Built-in types in their usual order, then the sponsor's own types A-Z."""
    preset = [label for label in ITEM_TYPES if label in types]
    custom = sorted((t for t in types if t not in ITEM_TYPES), key=str.lower)
    return preset + custom


def _catalog_settings(sponsor_id):
    """The sponsor's item types and how many dollars one of its points is worth.

    Only the item types are stored, not a snapshot of eBay listings.
    """
    conn = _website_helpers().get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT item_type FROM Sponsor_Catalog_Types WHERE sponsor_id = %s",
                (sponsor_id,),
            )
            types = [row["item_type"] for row in cursor.fetchall()]
            cursor.execute(
                "SELECT point_value FROM Sponsors WHERE sponsor_id = %s",
                (sponsor_id,),
            )
            row = cursor.fetchone()
            point_value = row.get("point_value") if row else None
    finally:
        conn.close()
    return _sort_item_types(types), point_value


def _save_item_types(sponsor_id, types):
    conn = _website_helpers().get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM Sponsor_Catalog_Types WHERE sponsor_id = %s",
                (sponsor_id,),
            )
            cursor.executemany(
                "INSERT INTO Sponsor_Catalog_Types (sponsor_id, item_type) VALUES (%s, %s)",
                [(sponsor_id, item_type) for item_type in types],
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def to_points(price, point_value):
    """Dollar price -> sponsor points, rounded up so items never cost less."""
    try:
        dollars = Decimal(str(price))
        rate = Decimal(str(point_value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if dollars < 0 or rate <= 0:
        return None
    return int((dollars / rate).to_integral_value(rounding=ROUND_CEILING))


def _search_for(item_type, keywords=""):
    base = ITEM_TYPES.get(item_type, item_type)
    return f"{base} {keywords}" if keywords else base


def _search_keywords():
    """What the user typed into the catalog search box, tidied and capped."""
    return " ".join(request.args.get("q", "").split())[:MAX_SEARCH_LENGTH]


def _point_overrides(sponsor_id, item_ids):
    """{item_id: points} for listings this sponsor gave its own point cost."""
    item_ids = [item_id for item_id in item_ids if item_id]
    if not sponsor_id or not item_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(item_ids))
    try:
        conn = _website_helpers().get_db()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT external_product_id, price_points
                    FROM Sponsor_Item_Points
                    WHERE sponsor_id = %s
                      AND external_product_id IN ({placeholders})
                    """,
                    (sponsor_id, *item_ids),
                )
                rows = cursor.fetchall()
        finally:
            conn.close()
    except Exception as exc:
        # Never fall back to the default price: a sponsor's price must be honored.
        raise CatalogError("Point costs couldn't be loaded. Check the database connection and migration.") from exc
    return {row["external_product_id"]: int(row["price_points"]) for row in rows}


def _availability(listing):
    """(in_stock, quantity eBay says is left or None) for one eBay item."""
    for availability in listing.get("estimatedAvailabilities") or []:
        status = availability.get("estimatedAvailabilityStatus")
        quantity = availability.get("estimatedAvailableQuantity")
        if status == "OUT_OF_STOCK" or quantity == 0:
            return False, 0
        return True, quantity if isinstance(quantity, int) else None
    return True, None


def _get_csrf_token():
    """Protect the catalog's save and cart actions from cross-site requests."""
    if "catalog_csrf" not in session:
        session["catalog_csrf"] = secrets.token_urlsafe(32)
    return session["catalog_csrf"]


def _check_csrf():
    from hmac import compare_digest
    given = request.form.get("catalog_csrf", "")
    expected = session.get("catalog_csrf", "")
    if not expected or not compare_digest(given, expected):
        abort(403)


def _authorize(role):
    actual_role = _allowed_role()
    if actual_role is None:
        return redirect(url_for("login" if "user_id" not in session else "home"))
    if actual_role != role:
        return redirect(url_for("home"))
    return None


def _page_number():
    try:
        page = max(1, int(request.args.get("page", "1")))
    except (TypeError, ValueError):
        page = 1
    return min(page, MAX_OFFSET // PAGE_SIZE + 1)


def _load_catalog(role):
    """(sponsor_id, item_types, point_value, notice) for the logged-in user."""
    try:
        sponsor_id, notice = _catalog_context(role)
        if sponsor_id is None:
            return None, [], None, notice
        types, point_value = _catalog_settings(sponsor_id)
        return sponsor_id, types, point_value, notice
    except Exception:
        # Failure to apply the SQL migration or a temporary RDS outage should
        # display a useful message, rather than silently showing other data.
        return None, [], None, "Catalog settings couldn't be loaded. Check the database migration and connection."


def _selected_type(types):
    """The item type being filtered to; anything not in the catalog falls back to the first."""
    wanted = request.args.get("type", "")
    return wanted if wanted in types else (types[0] if types else "")


def _search_catalog(role):
    redirect_response = _authorize(role)
    if redirect_response is not None:
        return redirect_response

    sponsor_id, types, point_value, notice = _load_catalog(role)
    selected = _selected_type(types)
    keywords = _search_keywords()

    page = _page_number()
    offset = (page - 1) * PAGE_SIZE
    items, total, has_next, error = [], 0, False, None

    if selected and not notice:
        try:
            data = _ebay_get(
                "/buy/browse/v1/item_summary/search",
                params={"q": _search_for(selected, keywords), "limit": PAGE_SIZE, "offset": offset},
            )
            total = int(data.get("total", 0))
            for listing in data.get("itemSummaries", []):
                categories = listing.get("categories") or []
                image = listing.get("image") or {}
                price = listing.get("price") or {}
                shipping = listing.get("shippingOptions") or []
                shipping_cost = (shipping[0].get("shippingCost") or {}) if shipping else {}
                items.append({
                    "id": listing.get("itemId", ""),
                    "title": listing.get("title", "Untitled item"),
                    "image": image.get("imageUrl", ""),
                    "points": to_points(price.get("value"), point_value),
                    "description": listing.get("shortDescription") or listing.get("subtitle") or "",
                    "condition": listing.get("condition", ""),
                    "category": categories[0].get("categoryName", "") if categories else "",
                    "seller": (listing.get("seller") or {}).get("username", ""),
                    "shipping": shipping_cost.get("value"),
                    "url": listing.get("itemWebUrl", ""),
                })
            has_next = bool(data.get("next")) and offset + PAGE_SIZE < min(total, MAX_OFFSET + PAGE_SIZE)
            overrides = _point_overrides(sponsor_id, [item["id"] for item in items])
            for item in items:
                if item["id"] in overrides:
                    item["points"] = overrides[item["id"]]
                    item["custom_points"] = True
        except CatalogError as exc:
            error = str(exc)

    return render_template(
        "shared/ebay_catalog.html", role=role, items=items,
        item_types=types, selected_type=selected, keywords=keywords,
        all_item_types=_sort_item_types(set(ITEM_TYPES) | set(types)),
        page=page, total=total, has_next=has_next, error=error,
        notice=notice, saved=request.args.get("saved") == "1",
        csrf_token=_get_csrf_token(),
        cart_count=sum(_cart_for(sponsor_id).values()) if role == "driver" and sponsor_id else 0,
        first_name=session.get("first_name", ""),
    )


def _types_from_form():
    """Checked item types plus any typed-in ones (comma separated), de-duplicated."""
    chosen = request.form.getlist("item_types")
    chosen += request.form.get("custom_types", "").split(",")
    types, seen = [], set()
    for raw in chosen:
        item_type = " ".join(raw.strip().split())
        if not item_type:
            continue
        # A typed "hats" matches the built-in "Hats" instead of duplicating it.
        item_type = next((label for label in ITEM_TYPES if label.lower() == item_type.lower()), item_type)
        if item_type.lower() not in seen:
            seen.add(item_type.lower())
            types.append(item_type)
    return types


@catalog_bp.route("/sponsor/catalog", methods=["GET", "POST"])
def sponsor_catalog():
    redirect_response = _authorize("sponsor")
    if redirect_response is not None:
        return redirect_response

    if request.method == "POST":
        _check_csrf()
        types = _types_from_form()
        if not types:
            flash("Choose at least one item type for your catalog.", "error")
            return redirect(url_for("ebay_catalog.sponsor_catalog"))
        if len(types) > MAX_ITEM_TYPES or any(len(t) > MAX_TYPE_LENGTH for t in types):
            flash(f"Choose up to {MAX_ITEM_TYPES} item types, each {MAX_TYPE_LENGTH} characters or fewer.", "error")
            return redirect(url_for("ebay_catalog.sponsor_catalog"))
        try:
            sponsor_id, _ = _catalog_context("sponsor")
            _save_item_types(sponsor_id, types)
        except Exception:
            flash("Could not save the catalog. Check the database connection and migration.", "error")
            return redirect(url_for("ebay_catalog.sponsor_catalog"))
        return redirect(url_for("ebay_catalog.sponsor_catalog", saved=1))

    return _search_catalog("sponsor")


@catalog_bp.route("/driver/catalog")
def driver_catalog():
    return _search_catalog("driver")


@catalog_bp.route("/<role>/catalog/item/<path:item_id>")
def catalog_item(role, item_id):
    redirect_response = _authorize(role)
    if redirect_response is not None:
        return redirect_response
    if role not in ("driver", "sponsor"):
        abort(404)

    if not re.fullmatch(ITEM_ID_PATTERN, item_id):
        abort(404)

    sponsor_id, types, point_value, notice = _load_catalog(role)

    # No product details if the user has no applicable sponsor catalog.
    if not types or notice:
        return redirect(url_for("ebay_catalog." + role + "_catalog"))

    error, item, custom_points = None, None, None
    try:
        item = _ebay_get("/buy/browse/v1/item/" + quote(item_id, safe=""))
        custom_points = _point_overrides(sponsor_id, [item_id]).get(item_id)
    except CatalogError as exc:
        error = str(exc)
    default_points = to_points(((item or {}).get("price") or {}).get("value"), point_value)
    in_stock, _ = _availability(item or {})
    return render_template(
        "shared/ebay_item.html", role=role, item=item, item_id=item_id,
        points=custom_points if custom_points is not None else default_points,
        default_points=default_points, custom_points=custom_points, in_stock=in_stock,
        error=error, selected_type=_selected_type(types),
        page=str(_page_number()), csrf_token=_get_csrf_token(),
    )


@catalog_bp.route("/sponsor/catalog/item/<path:item_id>/points", methods=["POST"])
def set_item_points(item_id):
    """Let a sponsor charge a different number of points for one listing."""
    redirect_response = _authorize("sponsor")
    if redirect_response is not None:
        return redirect_response
    _check_csrf()
    if not re.fullmatch(ITEM_ID_PATTERN, item_id):
        abort(404)

    back = url_for("ebay_catalog.catalog_item", role="sponsor", item_id=item_id,
                   type=request.form.get("type", ""), page=request.form.get("page", "1"))
    reset = request.form.get("action") == "reset"
    if not reset:
        try:
            points = int(request.form.get("points", ""))
        except ValueError:
            points = 0
        if not 1 <= points <= MAX_POINT_COST:
            flash(f"Enter a whole number of points from 1 to {MAX_POINT_COST:,}.", "error")
            return redirect(back)

    website = _website_helpers()
    sponsor_id = website.current_sponsor_id()
    conn = website.get_db()
    try:
        with conn.cursor() as cursor:
            if reset:
                cursor.execute(
                    "DELETE FROM Sponsor_Item_Points WHERE sponsor_id = %s AND external_product_id = %s",
                    (sponsor_id, item_id),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO Sponsor_Item_Points
                        (sponsor_id, external_product_id, price_points, updated_by_user_id)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        price_points = VALUES(price_points),
                        updated_by_user_id = VALUES(updated_by_user_id)
                    """,
                    (sponsor_id, item_id, points, session["user_id"]),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        flash("Could not save the point cost. Check the database connection and migration.", "error")
        return redirect(back)
    finally:
        conn.close()

    if reset:
        flash("Point cost reset to the default price.", "success")
    else:
        flash(f"Drivers now pay {points:,} points for this item.", "success")
    return redirect(back)


# ==========================================
# DRIVER CART AND PURCHASES
# Purchases are only recorded here and paid for with points; nothing is
# ordered from eBay.
# ==========================================

def _cart_for(sponsor_id):
    """{item_id: quantity} for one sponsor; points never mix between sponsors."""
    return dict((session.get("cart") or {}).get(str(sponsor_id), {}))


def _store_cart(sponsor_id, cart):
    carts = dict(session.get("cart") or {})
    if cart:
        carts[str(sponsor_id)] = cart
    else:
        carts.pop(str(sponsor_id), None)
    session["cart"] = carts


def _back_to(fallback):
    """Return to the page the driver was on, but never off-site."""
    back = request.referrer or ""
    return back if back.startswith(request.host_url) else fallback


def _cart_items(cart, point_value, sponsor_id):
    """Look up each cart item on eBay so prices and stock are always current.

    Returns (items, cart, notes). Sold-out or ended listings are left out of
    the returned cart, quantities are lowered to what eBay has left, and each
    change is explained in notes so the driver is told.
    """
    overrides = _point_overrides(sponsor_id, list(cart))
    items, kept, notes = [], {}, []
    for item_id, quantity in cart.items():
        try:
            listing = _ebay_get("/buy/browse/v1/item/" + quote(item_id, safe=""))
        except ItemUnavailable:
            notes.append("An item in your cart is no longer available on eBay and was removed.")
            continue
        title = listing.get("title", "An item")
        price = (listing.get("price") or {}).get("value")
        points = overrides.get(item_id, to_points(price, point_value))
        in_stock, left = _availability(listing)
        if not in_stock or points is None or price is None:
            notes.append(f"\"{title}\" is out of stock and was removed from your cart.")
            continue
        if left is not None and left < quantity:
            notes.append(f"Only {left} of \"{title}\" left, so your quantity was changed to {left}.")
            quantity = left
        kept[item_id] = quantity
        items.append({
            "id": item_id,
            "title": listing.get("title", "eBay listing"),
            "image": (listing.get("image") or {}).get("imageUrl", ""),
            "url": listing.get("itemWebUrl", ""),
            "dollars": Decimal(str(price)),
            "points": points,
            "quantity": quantity,
            "line_points": points * quantity,
        })
    return items, kept, notes


def _redemption_status(cursor, driver_id, sponsor_id):
    """(balance, suspended, reason) for one driver in one sponsor's program."""
    cursor.execute(
        """
        SELECT points_balance, redeem_suspended, suspend_reason
        FROM Driver_Sponsors
        WHERE driver_id = %s AND sponsor_id = %s
        """,
        (driver_id, sponsor_id),
    )
    row = cursor.fetchone() or {}
    return row.get("points_balance") or 0, bool(row.get("redeem_suspended")), row.get("suspend_reason")


def _shopping_sponsor_user():
    """The sponsor user shopping for this driver, or None when the driver is shopping."""
    return (session.get("_assume_driver_original") or {}).get("user_id")


def _driver_cart_context():
    """(sponsor_id, point_value, notice) for the sponsor the driver is viewing."""
    sponsor_id, types, point_value, notice = _load_catalog("driver")
    if not notice and not types:
        notice = "Your sponsor hasn't created a catalog yet."
    return sponsor_id, point_value, notice


@catalog_bp.route("/driver/cart/add", methods=["POST"])
def add_to_cart():
    redirect_response = _authorize("driver")
    if redirect_response is not None:
        return redirect_response
    _check_csrf()

    item_id = request.form.get("item_id", "")
    if not re.fullmatch(ITEM_ID_PATTERN, item_id):
        abort(404)
    sponsor_id, _, notice = _driver_cart_context()
    if notice:
        flash(notice, "error")
        return redirect(url_for("ebay_catalog.driver_catalog"))

    cart = _cart_for(sponsor_id)
    if item_id not in cart and len(cart) >= MAX_CART_ITEMS:
        flash(f"Your cart can hold up to {MAX_CART_ITEMS} different items.", "error")
    elif cart.get(item_id, 0) >= MAX_QUANTITY:
        flash(f"You can buy up to {MAX_QUANTITY} of the same item at once.", "error")
    else:
        cart[item_id] = cart.get(item_id, 0) + 1
        _store_cart(sponsor_id, cart)
        flash("Added to your cart.", "success")
    return redirect(_back_to(url_for("ebay_catalog.driver_catalog")))


@catalog_bp.route("/driver/cart/remove", methods=["POST"])
def remove_from_cart():
    redirect_response = _authorize("driver")
    if redirect_response is not None:
        return redirect_response
    _check_csrf()

    sponsor_id, _, notice = _driver_cart_context()
    if sponsor_id is not None:
        cart = _cart_for(sponsor_id)
        cart.pop(request.form.get("item_id", ""), None)
        _store_cart(sponsor_id, cart)
    return redirect(url_for("ebay_catalog.driver_cart"))


def _recent_purchases(cursor, driver_id, sponsor_id):
    """The driver's latest orders with this sponsor, each with its items."""
    cursor.execute(
        """
        SELECT
            Orders.order_id,
            Orders.order_number,
            Orders.order_date,
            Orders.status,
            Orders.total_points,
            Order_Items.product_name,
            Order_Items.quantity,
            Order_Items.unit_price_points,
            Products.image_url
        FROM Orders
        JOIN Order_Items
            ON Order_Items.order_id = Orders.order_id
        LEFT JOIN Products
            ON Order_Items.product_id = Products.product_id
        WHERE Orders.driver_id = %s
          AND Orders.sponsor_id = %s
        ORDER BY Orders.order_date DESC, Orders.order_id DESC, Order_Items.order_item_id
        LIMIT 50
        """,
        (driver_id, sponsor_id),
    )
    orders = {}
    for row in cursor.fetchall():
        order = orders.setdefault(row["order_id"], {
            "order_number": row["order_number"],
            "order_date": row["order_date"],
            "status": row["status"],
            "total_points": row["total_points"],
            "items": [],
        })
        order["items"].append(row)
    return list(orders.values())


@catalog_bp.route("/driver/cart")
def driver_cart():
    redirect_response = _authorize("driver")
    if redirect_response is not None:
        return redirect_response

    sponsor_id, point_value, notice = _driver_cart_context()
    items, error, balance, purchases, stock_notes = [], None, 0, [], []
    suspended, suspend_reason = False, None

    if sponsor_id is not None:
        cart = _cart_for(sponsor_id)
        if cart and not notice:
            try:
                items, kept, stock_notes = _cart_items(cart, point_value, sponsor_id)
                if kept != cart:
                    _store_cart(sponsor_id, kept)
            except CatalogError as exc:
                error = str(exc)
        try:
            conn = _website_helpers().get_db()
            try:
                with conn.cursor() as cursor:
                    balance, suspended, suspend_reason = _redemption_status(cursor, session["user_id"], sponsor_id)
                    purchases = _recent_purchases(cursor, session["user_id"], sponsor_id)
            finally:
                conn.close()
        except Exception:
            error = error or "Your purchases couldn't be loaded. Check the database connection."

    total_points = sum(item["line_points"] for item in items)
    return render_template(
        "driver/cart.html", items=items, total_points=total_points,
        balance=balance, purchases=purchases, notice=notice, error=error,
        stock_notes=stock_notes, suspended=suspended, suspend_reason=suspend_reason,
        csrf_token=_get_csrf_token(),
    )


@catalog_bp.route("/driver/cart/checkout", methods=["POST"])
def checkout():
    redirect_response = _authorize("driver")
    if redirect_response is not None:
        return redirect_response
    _check_csrf()

    cart_page = url_for("ebay_catalog.driver_cart")
    sponsor_id, point_value, notice = _driver_cart_context()
    if notice:
        flash(notice, "error")
        return redirect(cart_page)
    cart = _cart_for(sponsor_id)
    if not cart:
        flash("Your cart is empty.", "error")
        return redirect(cart_page)

    try:
        items, kept, stock_notes = _cart_items(cart, point_value, sponsor_id)
    except CatalogError as exc:
        flash(str(exc), "error")
        return redirect(cart_page)

    # Stock or quantities changed since the cart was shown: stop and let the
    # driver see the new total before any points are taken.
    if stock_notes:
        _store_cart(sponsor_id, kept)
        for note in stock_notes:
            flash(note, "error")
        flash("Your cart changed. Review it before purchasing.", "error")
        return redirect(cart_page)

    driver_id = session["user_id"]
    placed_by = _shopping_sponsor_user() or driver_id
    total_points = sum(item["line_points"] for item in items)
    total_dollars = sum(item["dollars"] * item["quantity"] for item in items)
    order_number = f"GD-{int(time.time())}-{secrets.token_hex(3).upper()}"

    conn = _website_helpers().get_db()
    try:
        with conn.cursor() as cursor:
            # Only takes the points if the balance still covers the order.
            cursor.execute(
                """
                UPDATE Driver_Sponsors
                SET points_balance = points_balance - %s
                WHERE driver_id = %s
                  AND sponsor_id = %s
                  AND status = 'Active'
                  AND redeem_suspended = 0
                  AND points_balance >= %s
                """,
                (total_points, driver_id, sponsor_id, total_points),
            )
            if cursor.rowcount == 0:
                _, suspended, _ = _redemption_status(cursor, driver_id, sponsor_id)
                conn.rollback()
                if suspended:
                    flash("Point redemption is paused for this account while your sponsor reviews an issue.", "error")
                else:
                    flash(f"You need {total_points:,} points for this order but don't have enough.", "error")
                return redirect(cart_page)

            cursor.execute(
                """
                INSERT INTO Point_Transactions
                    (driver_id, sponsor_id, changed_by_user_id, point_change, reason)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (driver_id, sponsor_id, placed_by, -total_points, f"Catalog purchase (order {order_number})"),
            )

            cursor.execute(
                """
                INSERT INTO Orders
                    (order_number, driver_id, sponsor_id, placed_by_user_id,
                     status, total_points, total_dollars)
                VALUES (%s, %s, %s, %s, 'Placed', %s, %s)
                """,
                (order_number, driver_id, sponsor_id, placed_by, total_points, total_dollars),
            )
            order_id = cursor.lastrowid

            for item in items:
                # Order_Items needs a Products row; keep one per sponsor + listing.
                cursor.execute(
                    """
                    INSERT INTO Products
                        (sponsor_id, external_product_id, product_name, image_url,
                         product_url, price_dollars, price_points, content_rating,
                         api_source, last_api_update)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'G', 'eBay', CURRENT_TIMESTAMP)
                    ON DUPLICATE KEY UPDATE
                        product_name = VALUES(product_name),
                        image_url = VALUES(image_url),
                        product_url = VALUES(product_url),
                        price_dollars = VALUES(price_dollars),
                        price_points = VALUES(price_points),
                        last_api_update = CURRENT_TIMESTAMP
                    """,
                    (sponsor_id, item["id"], item["title"][:500], item["image"][:1000],
                     item["url"][:1000], item["dollars"], item["points"]),
                )
                cursor.execute(
                    "SELECT product_id FROM Products WHERE sponsor_id = %s AND external_product_id = %s",
                    (sponsor_id, item["id"]),
                )
                product_id = cursor.fetchone()["product_id"]
                cursor.execute(
                    """
                    INSERT INTO Order_Items
                        (order_id, product_id, quantity, product_name,
                         unit_price_points, unit_price_dollars)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (order_id, product_id, item["quantity"], item["title"][:500],
                     item["points"], item["dollars"]),
                )

            cursor.execute(
                """
                INSERT INTO Notifications
                    (user_id, notification_type, subject, message)
                VALUES
                    (%s, 'Order Placed', %s, %s)
                """,
                (driver_id, f"Order {order_number} placed",
                 f"Your sponsor placed an order for you: {len(items)} item(s) for {total_points:,} points."
                 if placed_by != driver_id else
                 f"Your order of {len(items)} item(s) for {total_points:,} points was placed."),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        flash("Your purchase couldn't be completed. No points were taken.", "error")
        return redirect(cart_page)
    finally:
        conn.close()

    _store_cart(sponsor_id, {})
    if placed_by != driver_id:
        flash(f"Order {order_number} was placed for {session.get('assumed_driver_name', 'this driver')} "
              f"using {total_points:,} of their points.", "success")
    else:
        flash(f"Purchase successful! Order {order_number} was placed for {total_points:,} points.", "success")
    return redirect(cart_page)
