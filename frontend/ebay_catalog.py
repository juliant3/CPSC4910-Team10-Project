"""Live, read-only eBay catalog browsing for the Good Driver Program.

Register with app.register_blueprint(catalog_bp) in frontend/app.py.
Never sends an order or purchase request to eBay.
"""

import os
import re
import threading
import time
from urllib.parse import quote

import requests
from flask import Blueprint, abort, redirect, render_template, request, session, url_for

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
            response = requests.post(
                f"{EBAY_ROOT}/identity/v1/oauth2/token",
                auth=(client_id, client_secret),
                data={"grant_type": "client_credentials", "scope": EBAY_SCOPE},
                timeout=(5, 15),
            )
            response.raise_for_status()
            payload = response.json()
            token = payload["access_token"]
            expiry = max(60, int(payload.get("expires_in", 7200)) - 120)
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
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
            response = requests.get(
                f"{EBAY_ROOT}{path}", headers=headers,
                params=params, timeout=(5, 15),
            )
            if response.status_code == 401 and attempt == 0 and not (os.getenv("EBAY_ACCESS_TOKEN") or DEMO_ACCESS_TOKEN):
                continue
            if response.status_code == 401:
                raise CatalogError("Your eBay access token was rejected or has expired.")
            if response.status_code == 403:
                raise CatalogError("Your eBay application doesn't have Browse API access in Production.")
            if response.status_code == 429:
                raise CatalogError("eBay's request limit has been reached. Please try again later.")
            response.raise_for_status()
            return response.json()
        except requests.Timeout as exc:
            raise CatalogError("eBay took too long to respond. Please try again.") from exc
        except (requests.RequestException, ValueError) as exc:
            raise CatalogError("Unable to retrieve items from eBay right now.") from exc


def _allowed_role():
    if "user_id" not in session:
        return None
    role = str(session.get("role", "")).lower()
    return role if role in ("driver", "sponsor") else None


def _search_catalog(role):
    actual_role = _allowed_role()
    if actual_role is None:
        if "user_id" not in session:
            return redirect(url_for("login"))
        return redirect(url_for("home"))
    if actual_role != role:
        return redirect(url_for("home"))

    query = request.args.get("q", "truck accessories").strip()[:120]
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    page = min(page, MAX_OFFSET // PAGE_SIZE + 1)
    offset = (page - 1) * PAGE_SIZE
    items = []
    total = 0
    error = None
    has_next = False

    if query:
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
        first_name=session.get("first_name", ""),
    )


@catalog_bp.route("/sponsor/catalog")
def sponsor_catalog():
    return _search_catalog("sponsor")


@catalog_bp.route("/driver/catalog")
def driver_catalog():
    return _search_catalog("driver")


@catalog_bp.route("/<role>/catalog/item/<path:item_id>")
def catalog_item(role, item_id):
    actual_role = _allowed_role()
    if actual_role is None:
        if "user_id" not in session:
            return redirect(url_for("login"))
        return redirect(url_for("home"))
    if role not in ("driver", "sponsor") or role != actual_role:
        abort(404)

    # Avoid turning the item-details endpoint into a general-purpose HTTP proxy.
    if not re.fullmatch(r"v1\|[0-9]{6,20}\|[0-9]{1,20}", item_id):
        abort(404)
    error = None
    item = None
    try:
        item = _ebay_get("/buy/browse/v1/item/" + quote(item_id, safe=""))
    except CatalogError as exc:
        error = str(exc)

    query = request.args.get("q", "truck accessories")[:120]
    page = request.args.get("page", "1")
    if not page.isdigit():
        page = "1"
    return render_template(
        "shared/ebay_item.html", role=role, item=item,
        error=error, query=query, page=page,
    )
