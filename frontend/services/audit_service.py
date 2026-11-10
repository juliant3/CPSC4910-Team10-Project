"""Centralized, best-effort audit trail for the Flask website.

Audit writes are deliberately separate from business transactions. A logging
failure must not break a checkout, login, or application decision. The event
metadata is allowlisted; form bodies, passwords, invite/reset tokens and chat
messages must never be persisted here.
"""

import json
from flask import current_app, g, request, session

# Each POST is at least recorded as an *attempt*; queue_event() records a
# confirmed outcome only when a handler explicitly knows it succeeded.
ACTION_NAMES = {
    "bulk_upload_page": ("User Management", "Bulk upload"),
    "sponsor_applications": ("Driver Application", "Manage driver applications"),
    "sponsor_questions": ("Driver Application", "Update application questions"),
    "accept_application": ("Driver Application", "Accept driver application"),
    "reject_application": ("Driver Application", "Reject driver application"),
    "delete_sponsor_question": ("Driver Application", "Delete application question"),
    "sponsor_delete_application": ("Driver Application", "Delete driver application"),
    "admin_delete_application": ("Driver Application", "Delete driver application as admin"),
    "change_driver_points": ("Point Change", "Adjust driver points"),
    "set_driver_redemption": ("Point Change", "Change driver redemption access"),
    "driver_edit_profile": ("Profile", "Update driver profile"),
    "sponsor_edit_driver": ("Profile", "Update driver details"),
    "sponsor_recurring": ("Point Change", "Manage recurring points"),
    "switch_driver_sponsor": ("Sponsor Management", "Switch active sponsor"),
    "settings": ("Account", "Change account settings"),
    "sponsor_request": ("Sponsor Management", "Request sponsor account"),
    "sponsor_apply": ("Sponsor Management", "Submit sponsor application"),
    "sponsor_team": ("User Management", "Manage sponsor team"),
    "approve_sponsor_request": ("Sponsor Management", "Approve sponsor request"),
    "reject_sponsor_request": ("Sponsor Management", "Reject sponsor request"),
    "sponsor_assume_driver": ("Security", "Enter driver support view"),
    "sponsor_exit_assumed_driver": ("Security", "Exit driver support view"),
    "admin_assume_driver": ("Security", "Enter admin driver support view"),
    "admin_assume_sponsor": ("Security", "Enter admin sponsor support view"),
    "admin_exit_assumed_sponsor": ("Security", "Exit admin sponsor support view"),
    "admin_create_invoice": ("Order", "Create invoice"),
    "admin_update_invoice_status": ("Order", "Update invoice status"),
    "ebay_catalog.sponsor_catalog": ("Catalog", "Change sponsor catalog"),
    "ebay_catalog.add_to_cart": ("Catalog", "Add item to cart"),
    "ebay_catalog.remove_from_cart": ("Catalog", "Remove item from cart"),
    "ebay_catalog.checkout": ("Order", "Place reward order"),
    "chat.conversation": ("Communication", "Send message"),
    "apply": ("Driver Application", "Submit driver application"),
}

# Explicit audit decisions also cover authentication and other public actions.
# No raw request body, query string, session object, or token is logged.
SAFE_DETAIL_KEYS = frozenset({
    "endpoint", "method", "http_status", "actor_role", "actor_user_id", "target_type",
    "target_id", "application_id", "driver_id", "sponsor_id", "invoice_id",
    "request_id", "question_id", "recurring_id", "point_delta", "points",
    "item_count", "order_number", "assumed_driver_id", "assumed_sponsor_id",
    "operation", "purpose", "login_method", "source",
})

# Client-triggered periodic requests are not user audit events.
EXCLUDED_ENDPOINTS = {
    "session_keep_alive", "mark_notifications_read", "ebay_notifications",
    "chat.new_messages", "login", "forgot_password", "reset_password",
    "logout", "register",
}


def actor_snapshot():
    """Track the *real* user while support/impersonation mode is active."""
    original = (session.get("_assume_driver_original")
                or session.get("_assume_sponsor_original"))
    return {
        "user_id": original.get("user_id") if isinstance(original, dict)
        else session.get("user_id"),
        "role": original.get("role") if isinstance(original, dict)
        else session.get("role"),
        "sponsor_id": session.get("sponsor_id"),
        "assumed_driver_id": session.get("assumed_driver_id"),
        "assumed_sponsor_id": session.get("assumed_sponsor_id"),
    }


def queue_event(category, action, *, success=None, status=None, username=None,
                reason=None, user_id=None, sponsor_id=None, driver_id=None,
                details=None):
    """Queue a confirmed event to be written after the response is created.

    Do not call this until the corresponding business transaction commits.
    Pass success=False for a confirmed rejection, and None for an attempt.
    """
    actor = actor_snapshot()
    safe_details = {
        "endpoint": request.endpoint or "unknown",
        "method": request.method,
        "actor_role": actor["role"],
        "actor_user_id": actor["user_id"],
    }
    if actor["assumed_driver_id"] is not None:
        safe_details["assumed_driver_id"] = actor["assumed_driver_id"]
    if actor["assumed_sponsor_id"] is not None:
        safe_details["assumed_sponsor_id"] = actor["assumed_sponsor_id"]
    for key, value in (details or {}).items():
        if key in SAFE_DETAIL_KEYS and isinstance(value, (str, int, bool, float)):
            safe_details[key] = str(value)[:120] if isinstance(value, str) else value

    g.audit_events = getattr(g, "audit_events", []) + [{
        "user_id": user_id if user_id is not None else actor["user_id"],
        "sponsor_id": sponsor_id if sponsor_id is not None else (
            actor["sponsor_id"] if str(actor["role"]).lower() == "sponsor" else None
        ),
        "driver_id": driver_id if driver_id is not None else (
            actor["user_id"] if str(actor["role"]).lower() == "driver" else None
        ),
        "category": str(category)[:64],
        "action": str(action)[:255],
        "status": str(status)[:100] if status is not None else None,
        "reason": str(reason)[:1000] if reason is not None else None,
        "username": str(username)[:255] if username is not None else None,
        "success": success,
        "ip_address": (request.remote_addr or "")[:45],
        "details": json.dumps(safe_details, separators=(",", ":"), sort_keys=True),
    }]


def log_audit_event(category, action, **kwargs):
    """Public helper for adding an audit event inside a Flask request."""
    queue_event(category, action, **kwargs)


def log_login_attempt(username, succeeded, *, user_id=None):
    """Record both accepted and rejected credential checks without a password."""
    queue_event("Login Attempt", "Login successful" if succeeded else "Login failed",
                status="Accepted" if succeeded else "Invalid credentials",
                success=bool(succeeded), username=username, user_id=user_id,
                details={"login_method": "password"})


def register_audit_hooks(app, connection_factory):
    """Register request-level audit hooks using the site's get_db() factory."""

    @app.after_request
    def persist_audit_events(response):
        if not current_app.config.get("AUDIT_LOGGING_ENABLED", not current_app.testing):
            return response

        events = list(getattr(g, "audit_events", []))
        endpoint = request.endpoint or ""
        if response.status_code in {401, 403} and not events:
            # Access-control failures are security-relevant even on GET.
            if actor_snapshot()["user_id"] is not None:
                queue_event("Security", "Access denied", success=False,
                            status="HTTP " + str(response.status_code),
                            details={"http_status": response.status_code})
                events = list(g.audit_events)
        if (request.method in {"POST", "PUT", "PATCH", "DELETE"}
                and endpoint not in EXCLUDED_ENDPOINTS and not events):
            actor = actor_snapshot()
            # Do not fill the log with anonymous automated/bot requests.
            if actor["user_id"] is not None or endpoint in {
                "apply", "sponsor_request", "sponsor_apply"
            }:
                category, action = ACTION_NAMES.get(
                    endpoint, ("Site Activity", endpoint.replace("_", " "))
                )
                target = next((
                    (key, value) for key, value in (request.view_args or {}).items()
                    if key in {"application_id", "driver_id", "sponsor_id",
                               "invoice_id", "request_id", "question_id"}
                    and isinstance(value, int)
                ), None)
                detail = {"http_status": response.status_code}
                if target:
                    detail.update({"target_type": target[0], "target_id": target[1]})
                queue_event(category, action + " attempted",
                            status="HTTP " + str(response.status_code),
                            success=False if response.status_code >= 400 else None,
                            details=detail)
                events = list(g.audit_events)

        if not events:
            return response

        connection = None
        try:
            connection = connection_factory()
            with connection.cursor() as cursor:
                for event in events:
                    cursor.execute("""
                        INSERT INTO Audit_Logs
                            (user_id, sponsor_id, driver_id, category, action,
                             status, reason, username, success, ip_address, details)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        event["user_id"], event["sponsor_id"], event["driver_id"],
                        event["category"], event["action"], event["status"],
                        event["reason"], event["username"], event["success"],
                        event["ip_address"], event["details"],
                    ))
            connection.commit()
        except Exception:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
            current_app.logger.exception("Audit log write failed; request continued")
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
        return response
