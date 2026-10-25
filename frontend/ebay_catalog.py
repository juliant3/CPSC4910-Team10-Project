import os
import re
import base64
import json
import socket
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from flask import Blueprint, abort, redirect, render_template, request, session, url_for, flash

catalog_bp = Blueprint("ebay_catalog", __name__)
EBAY_ROOT = "https://api.ebay.com"
EBAY_SCOPE = "https://api.ebay.com/oauth/api_scope"
PAGE_SIZE = 10
# OPTIONAL quick demo: paste your Postman OAuth access token here and remove
# it before committing. It expires; client credentials below are better.
DEMO_ACCESS_TOKEN = ""
MAX_OFFSET = 9990  # eBay Browse search limits pagination to 10,000 results.

_token_lock = threading.Lock()
_cached_token = ""
_cached_until = 0.0


class CatalogError(Exception):
    """A message safe to show to someone using the catalog."""


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


def _saved_search(sponsor_id):
    """Only the search term is stored, not a snapshot of eBay listings."""
    conn = _website_helpers().get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT search_term FROM Sponsor_Catalog_Settings WHERE sponsor_id = %s",
                (sponsor_id,),
            )
            row = cursor.fetchone()
            return (row.get("search_term") or "") if row else ""
    finally:
        conn.close()


def _save_search(sponsor_id, query):
    conn = _website_helpers().get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """INSERT INTO Sponsor_Catalog_Settings (sponsor_id, search_term)
                   VALUES (%s, %s)
                   ON DUPLICATE KEY UPDATE
                       search_term = VALUES(search_term),
                       updated_at = CURRENT_TIMESTAMP""",
                (sponsor_id, query),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _get_csrf_token():
    """Protect the sponsor's save action from cross-site requests."""
    from secrets import token_urlsafe
    if "catalog_csrf" not in session:
        session["catalog_csrf"] = token_urlsafe(32)
    return session["catalog_csrf"]


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


def _search_catalog(role):
    redirect_response = _authorize(role)
    if redirect_response is not None:
        return redirect_response

    try:
        sponsor_id, notice = _catalog_context(role)
        query = _saved_search(sponsor_id) if sponsor_id is not None else ""
    except Exception:
        # Failure to apply the SQL migration or a temporary RDS outage should
        # display a useful message, rather than silently showing other data.
        sponsor_id, query = None, ""
        notice = "Catalog settings couldn't be loaded. Check the database migration and connection."

    page = _page_number()
    offset = (page - 1) * PAGE_SIZE
    items, total, has_next, error = [], 0, False, None

    if query and not notice:
        try:
            data = _ebay_get(
                "/buy/browse/v1/item_summary/search",
                params={"q": query, "limit": PAGE_SIZE, "offset": offset},
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
                    "price": price.get("value", ""),
                    "currency": price.get("currency", ""),
                    "description": listing.get("shortDescription") or listing.get("subtitle") or "",
                    "condition": listing.get("condition", ""),
                    "category": categories[0].get("categoryName", "") if categories else "",
                    "seller": (listing.get("seller") or {}).get("username", ""),
                    "shipping": shipping_cost.get("value"),
                    "url": listing.get("itemWebUrl", ""),
                })
            has_next = bool(data.get("next")) and offset + PAGE_SIZE < min(total, MAX_OFFSET + PAGE_SIZE)
        except CatalogError as exc:
            error = str(exc)

    return render_template(
        "shared/ebay_catalog.html", role=role, items=items, query=query,
        page=page, total=total, has_next=has_next, error=error,
        notice=notice, saved=request.args.get("saved") == "1",
        csrf_token=_get_csrf_token() if role == "sponsor" else None,
        first_name=session.get("first_name", ""),
    )


@catalog_bp.route("/sponsor/catalog", methods=["GET", "POST"])
def sponsor_catalog():
    redirect_response = _authorize("sponsor")
    if redirect_response is not None:
        return redirect_response

    if request.method == "POST":
        from hmac import compare_digest
        given = request.form.get("catalog_csrf", "")
        expected = session.get("catalog_csrf", "")
        if not expected or not compare_digest(given, expected):
            abort(403)

        query = " ".join(request.form.get("search_term", "").strip().split())
        if not query or len(query) > 120:
            flash("Enter a search term between 1 and 120 characters.", "error")
            return redirect(url_for("ebay_catalog.sponsor_catalog"))
        try:
            sponsor_id, _ = _catalog_context("sponsor")
            _save_search(sponsor_id, query)
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

    if not re.fullmatch(r"v1\|[0-9]{6,20}\|[0-9]{1,20}", item_id):
        abort(404)

    try:
        sponsor_id, notice = _catalog_context(role)
        query = _saved_search(sponsor_id) if sponsor_id is not None else ""
    except Exception:
        sponsor_id, query = None, ""
        notice = "Catalog settings couldn't be loaded."

    # No product details if the user has no applicable sponsor catalog.
    if not query or notice:
        return redirect(url_for("ebay_catalog." + role + "_catalog"))

    error, item = None, None
    try:
        item = _ebay_get("/buy/browse/v1/item/" + quote(item_id, safe=""))
    except CatalogError as exc:
        error = str(exc)
    return render_template(
        "shared/ebay_item.html", role=role, item=item,
        error=error, query=query, page=str(_page_number()),
    )