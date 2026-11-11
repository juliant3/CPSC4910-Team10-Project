from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, abort, Response
import csv
import io
import pymysql
import os
import hashlib
import smtplib
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, datetime, timezone
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from markupsafe import Markup
from zoneinfo import ZoneInfo
from werkzeug.security import check_password_hash, generate_password_hash
from services.bulk_upload import process_bulk_upload
from services.audit_service import queue_event, log_login_attempt, register_audit_hooks


app = Flask(__name__)

from ebay_catalog import catalog_bp
app.register_blueprint(catalog_bp)

from chat import chat_bp, count_unread_messages
app.register_blueprint(chat_bp)
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "change_this_to_a_secure_random_value"
)
app.permanent_session_lifetime = timedelta(minutes = 5) 

# The server and database run on UTC; dates are shown in the program's local time.
try:
    LOCAL_TZ = ZoneInfo(os.environ.get("APP_TIMEZONE", "America/New_York"))
except Exception:
    LOCAL_TZ = timezone(timedelta(hours=-5))


def local_now():
    return datetime.now(LOCAL_TZ)


@app.template_filter("local")
def to_local_time(value):
    """Convert a UTC timestamp from the database to local time for display."""
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(LOCAL_TZ)

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "cpsc4910-f26.cobd8enwsupz.us-east-1.rds.amazonaws.com"),
    "user": os.environ.get("DB_USER", "Team10"),
    "password": os.environ.get("DB_PASSWORD"),
    "database": os.environ.get("DB_NAME", "Team10_DB"),
}


SITE_URL = os.environ.get("SITE_URL", "https://cpsc4910team10truckingproject.org")

# ==========================================
# SINGLE-USE PASSWORD LINKS
# Reset and first-time setup links carry a random token. The database only
# stores its SHA-256 hash, and a link stops working once used or expired.
# ==========================================

RESET_LINK_MINUTES = 30
SETUP_LINK_MINUTES = 72 * 60
MIN_PASSWORD_LENGTH = 8


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_password_token(cursor, user_id, purpose):
    """A new link token for 'Reset' or 'Setup'; older unused links of that kind stop working."""
    cursor.execute(
        """
        UPDATE Password_Tokens
        SET used_at = NOW()
        WHERE user_id = %s
          AND purpose = %s
          AND used_at IS NULL
        """,
        (user_id, purpose)
    )

    token = secrets.token_urlsafe(32)
    minutes = RESET_LINK_MINUTES if purpose == "Reset" else SETUP_LINK_MINUTES

    cursor.execute(
        """
        INSERT INTO Password_Tokens
            (token_hash, user_id, purpose, expires_at)
        VALUES
            (%s, %s, %s, NOW() + INTERVAL %s MINUTE)
        """,
        (hash_token(token), user_id, purpose, minutes)
    )

    return token


def find_password_token(cursor, token):
    """The user a link belongs to, or None if it is unknown, used, or expired."""
    if not token or len(token) > 100:
        return None

    cursor.execute(
        """
        SELECT
            Password_Tokens.user_id,
            Password_Tokens.purpose,
            Users.email,
            Users.first_name
        FROM Password_Tokens
        JOIN Users
            ON Users.user_id = Password_Tokens.user_id
        WHERE Password_Tokens.token_hash = %s
          AND Password_Tokens.used_at IS NULL
          AND Password_Tokens.expires_at > NOW()
        """,
        (hash_token(token),)
    )
    return cursor.fetchone()


def password_link(token, purpose):
    path = "reset-password" if purpose == "Reset" else "set-password"
    return f"{SITE_URL}/{path}/{token}"


def unusable_password_hash():
    """For new accounts: nobody knows this password, so the setup link is the only way in."""
    return generate_password_hash(secrets.token_urlsafe(32), method="pbkdf2:sha256")

# ==========================================
# SESSION TIMEOUT - 5 MINUTES
# ==========================================

SESSION_TIMEOUT = timedelta(minutes=5)


@app.before_request
def check_session_timeout():

    if request.endpoint == "static":
        return

    if "user_id" not in session:
        return

    last_activity = session.get("last_activity")

    if not last_activity:
        queue_event("Security", "Session expired", status="Missing activity timestamp", success=True)
        session.clear()
        return redirect(url_for("login"))

    try:
        last_activity_time = datetime.fromisoformat(last_activity)

        if last_activity_time.tzinfo is None:
            last_activity_time = last_activity_time.replace(
                tzinfo=timezone.utc
            )

    except (ValueError, TypeError):
        queue_event("Security", "Session expired", status="Invalid activity timestamp", success=True)
        session.clear()
        return redirect(url_for("login"))

    now = datetime.now(timezone.utc)

    if now - last_activity_time >= SESSION_TIMEOUT:
        queue_event("Security", "Session expired", status="Inactive", success=True)
        session.clear()
        return redirect(url_for("login"))

    # Any page the user opens counts as activity, not just the keep-alive ping.
    session["last_activity"] = now.isoformat()


# ==========================================
# SESSION KEEP ALIVE
# ==========================================

@app.route("/session/keep-alive", methods=["POST"])
def session_keep_alive():

    if "user_id" not in session:
        return {"success": False}, 401

    session["last_activity"] = datetime.now(
        timezone.utc
    ).isoformat()

    return {"success": True}


@app.route("/api/ebay/notifications", methods=["GET", "POST"])
def ebay_notifications():

    if request.method == "GET":
        challenge_code = request.args.get("challenge_code")

        if not challenge_code:
            return jsonify({"error": "Missing challenge code"}), 400

        verification_token = "W22lV0Gc7RJVlbZBO4vSqWR2wVgR0lCHa2hU5NRNcNUtjgUg"   
        endpoint = "https://cpsc4910team10truckingproject.org/api/ebay/notifications"

        combined = challenge_code + verification_token + endpoint

        response_hash = hashlib.sha256(
            combined.encode("utf-8")
        ).hexdigest()

        return jsonify({"challengeResponse": response_hash}), 200

    if request.method == "POST":
        # Notification signature verification and account deletion
        # processing will be implemented next.
        return jsonify({"error": "Not implemented yet"}), 501

# ==========================================
# DATABASE CONNECTION
# ==========================================

def get_db():
    return pymysql.connect(
        **DB_CONFIG,
        cursorclass=pymysql.cursors.DictCursor
    )

# Audit hooks use the same database configuration but a separate transaction.
# The lambda resolves get_db at request time (and can be replaced in tests).
register_audit_hooks(app, lambda: get_db())

def get_login_stats():
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS c FROM Drivers")
            driver_count = cursor.fetchone()["c"]

            cursor.execute("SELECT COUNT(*) AS c FROM Sponsors")
            sponsor_count = cursor.fetchone()["c"]
    finally:
        conn.close()
    return driver_count, sponsor_count

def current_sponsor_id():
    """
    The sponsor organization the logged-in sponsor user belongs to.

    Set at login from Sponsor_Users. Falls back to the user's own id, which
    is the organization id for the sponsor user who created it.
    """
    return session.get("sponsor_id") or session["user_id"]


def get_driver_sponsorships(cursor, driver_id):
    """Every sponsor a driver belongs to, with that sponsor's point balance."""
    cursor.execute(
        """
        SELECT
            Driver_Sponsors.sponsor_id,
            Driver_Sponsors.status,
            Driver_Sponsors.points_balance,
            Sponsors.company_name,
            Sponsors.point_value
        FROM Driver_Sponsors
        JOIN Sponsors
            ON Driver_Sponsors.sponsor_id = Sponsors.sponsor_id
        WHERE Driver_Sponsors.driver_id = %s
        ORDER BY (Driver_Sponsors.status = 'Active') DESC, Sponsors.company_name
        """,
        (driver_id,)
    )
    return cursor.fetchall()


def pick_driver_sponsorship(sponsorships):
    """The sponsor the driver is currently viewing (switchable in the header)."""
    if not sponsorships:
        return None

    chosen = session.get("driver_sponsor_id")
    for sponsorship in sponsorships:
        if sponsorship["sponsor_id"] == chosen:
            return sponsorship

    return sponsorships[0]


@app.context_processor
def inject_driver_sponsorships():
    """Sponsor switcher data for the navbar when a driver is logged in."""
    if "user_id" not in session or (session.get("role") or "").lower() != "driver":
        return {}

    try:
        conn = get_db()
        try:
            with conn.cursor() as cursor:
                sponsorships = get_driver_sponsorships(cursor, session["user_id"])
        finally:
            conn.close()
    except Exception:
        return {}

    # A sponsor viewing as this driver only sees their own program.
    if session.get("assumed_driver_sponsor_id"):
        sponsorships = [
            s for s in sponsorships
            if s["sponsor_id"] == session.get("assumed_driver_sponsor_id")
        ]

    return {
        "driver_sponsorships": sponsorships,
        "driver_sponsorship": pick_driver_sponsorship(sponsorships),
    }


@app.context_processor
def inject_current_year():
    return {"current_year": local_now().year}


# Settings toggles: column in User_Settings -> notification types it covers,
# its label, and who sees the toggle.
NOTIFICATION_SETTINGS = [
    ("notify_points", ("Point Change",), "Points and redemption", ("driver",)),
    ("notify_orders", ("Order Placed",), "Orders", ("driver",)),
    ("notify_program", ("Dropped",), "Program changes", ("driver",)),
    ("notify_applications", ("New Application",), "New applications and requests", ("sponsor", "admin")),
    ("notify_messages", ("Message",), "New messages", ("driver", "sponsor")),
]

# Hides notification types the user switched off. Built only from the
# constants above, never from user input.
MUTED_NOTIFICATIONS_SQL = " OR ".join(
    # COALESCE: users with no settings row have everything switched on.
    "(Notifications.notification_type IN ({}) AND COALESCE(User_Settings.{}, 1) = 0)".format(
        ", ".join(f"'{t}'" for t in types), column
    )
    for column, types, _, _ in NOTIFICATION_SETTINGS
)


@app.context_processor
def inject_notifications():
    """Unread count + latest notifications for the navbar dropdown on every page."""
    empty = {"unread_count": 0, "recent_notifications": [], "unread_messages": 0}

    if "user_id" not in session:
        return empty

    try:
        conn = get_db()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT COUNT(*) AS c
                    FROM Notifications
                    LEFT JOIN User_Settings
                        ON User_Settings.user_id = Notifications.user_id
                    WHERE Notifications.user_id = %s
                      AND Notifications.is_read = 0
                      AND NOT ({MUTED_NOTIFICATIONS_SQL})
                    """,
                    (session["user_id"],)
                )
                unread_count = cursor.fetchone()["c"]

                cursor.execute(
                    f"""
                    SELECT
                        Notifications.notification_id,
                        Notifications.subject,
                        Notifications.message,
                        Notifications.is_read,
                        Notifications.created_at
                    FROM Notifications
                    LEFT JOIN User_Settings
                        ON User_Settings.user_id = Notifications.user_id
                    WHERE Notifications.user_id = %s
                      AND NOT ({MUTED_NOTIFICATIONS_SQL})
                    ORDER BY Notifications.created_at DESC, Notifications.notification_id DESC
                    LIMIT 15
                    """,
                    (session["user_id"],)
                )
                recent = cursor.fetchall()

                # Kept separate so the bell still works if the chat table is missing.
                try:
                    unread_messages = count_unread_messages(cursor)
                except Exception:
                    unread_messages = 0

                return {
                    "unread_count": unread_count,
                    "recent_notifications": recent,
                    "unread_messages": unread_messages,
                }
        finally:
            conn.close()
    except Exception:
        return empty


# Emails go out on a background thread so the page doesn't wait on the mail
# server. A failure is printed to the server log instead of shown to the user.
EMAIL_SENDER = ThreadPoolExecutor(max_workers=2)
SMTP_TIMEOUT_SECONDS = 10


def send_email(to_email, subject, body):
    EMAIL_SENDER.submit(deliver_email, to_email, subject, body)
    return True


def deliver_email(to_email, subject, body):
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    smtp_from = os.environ.get("SMTP_FROM", smtp_username)

    # If SMTP isn't configured, print the email so development can continue.
    if not smtp_host or not smtp_username or not smtp_password:
        print(f"[EMAIL to {to_email}] {subject}\n{body}")
        return True

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp_from
    message["To"] = to_email
    message.set_content(body)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=SMTP_TIMEOUT_SECONDS) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(message)
        return True
    except Exception as e:
        print(f"Failed to send email to {to_email}: {e}")
        return False


def send_acceptance_email(email, first_name, setup_token=None):
    login_link = f"{SITE_URL}/login"

    if setup_token:
        details = (
            "Set your password here to finish creating your account:\n"
            f"{password_link(setup_token, 'Setup')}\n\n"
            f"The link works once and expires in {SETUP_LINK_MINUTES // 60} hours. "
            f"If it expires, use Forgot Password on {login_link} to get a new one."
        )
    else:
        details = f"Log in with your existing account here: {login_link}"

    body = (
        f"Hi {first_name or ''},\n\n"
        "Congratulations! Your application has been accepted.\n\n"
        f"{details}"
    )

    return send_email(email, "Your Drivers Advantage application was accepted!", body)


def send_rejection_email(email, first_name, reason=None):
    body = (
        f"Hi {first_name or ''},\n\n"
        "Thank you for your interest in Drivers Advantage. "
        "After review, your sponsor was unable to move forward "
        "with your application at this time."
    )

    if reason:
        body += f"\n\nReason provided: {reason}"

    return send_email(email, "Your Drivers Advantage application", body)


def send_sponsor_request_link(email, first_name, token):
    apply_link = f"https://cpsc4910team10truckingproject.org/sponsor-apply/{token}"

    body = (
        f"Hi {first_name or ''},\n\n"
        "Thanks for your interest in becoming a Drivers Advantage sponsor.\n\n"
        "Use the link below to tell us about your organization. "
        "An administrator will review your application and email you once "
        "your account is ready.\n\n"
        f"{apply_link}\n\n"
        "If you didn't request this, you can ignore this email."
    )

    return send_email(email, "Your Drivers Advantage sponsor application link", body)


def send_sponsor_approved_email(email, first_name, company_name, setup_token):
    body = (
        f"Hi {first_name or ''},\n\n"
        f"Your sponsor account for {company_name} has been approved.\n\n"
        "Set your password here to start using it:\n"
        f"{password_link(setup_token, 'Setup')}\n\n"
        f"The link works once and expires in {SETUP_LINK_MINUTES // 60} hours. "
        f"If it expires, use Forgot Password on {SITE_URL}/login to get a new one."
    )

    return send_email(email, "Your Drivers Advantage sponsor account is ready", body)


def send_sponsor_rejected_email(email, first_name, reason=None):
    body = (
        f"Hi {first_name or ''},\n\n"
        "Thank you for your interest in becoming a Drivers Advantage sponsor. "
        "After review, we are unable to approve your application at this time."
    )

    if reason:
        body += f"\n\nReason provided: {reason}"

    return send_email(email, "Your Drivers Advantage sponsor application", body)


def send_sponsor_team_invite(email, first_name, company_name, inviter_name, token):
    join_link = f"https://cpsc4910team10truckingproject.org/sponsor-apply/{token}"

    body = (
        f"Hi {first_name or ''},\n\n"
        f"{inviter_name} invited you to join {company_name} as a sponsor on Drivers Advantage.\n\n"
        "Use the link below to set your password and create your account:\n"
        f"{join_link}\n\n"
        "If you weren't expecting this, you can ignore this email."
    )

    return send_email(email, f"Join {company_name} on Drivers Advantage", body)


def send_application_invite(email, token):
    apply_link = f"https://cpsc4910team10truckingproject.org/apply/{token}"

    body = (
        f"You've been invited to apply for the Drivers Advantage rewards program.\n\n"
        f"Complete your application here:\n{apply_link}\n\n"
        "If you weren't expecting this, you can ignore this email."
    )

    return send_email(email, "You've been invited to Drivers Advantage", body)
    
def send_password_reset_link(email, first_name, token):
    body = (
        f"Hi {first_name or ''},\n\n"
        "Use the link below to reset your Drivers Advantage password. "
        f"It works once and expires in {RESET_LINK_MINUTES} minutes.\n\n"
        f"{password_link(token, 'Reset')}\n\n"
        "If you did not request a password reset, you can ignore this email."
    )

    return send_email(email, "Reset your Drivers Advantage password", body)


# ==========================================
# DASHBOARD DATA
# ==========================================

def month_start():
    """Midnight on the 1st of this month (local time), as a UTC timestamp for queries."""
    start = local_now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).replace(tzinfo=None)


@app.template_filter("waiting")
def waiting_filter(when):
    """'waiting 3 days' style label for how long something has been pending."""
    if not when:
        return ""

    delta = datetime.now(timezone.utc).replace(tzinfo=None) - when
    days = delta.days

    if days >= 1:
        css = "waiting long" if days >= 3 else "waiting"
        text = f"waiting {days} day{'s' if days != 1 else ''}"
    else:
        hours = max(delta.seconds // 3600, 0)
        css = "waiting"
        text = f"waiting {hours} hr{'s' if hours != 1 else ''}" if hours else "just now"

    return Markup(f'<span class="{css}">{text}</span>')


def get_admin_dashboard_data(cursor):

    cursor.execute(
        """
        SELECT COUNT(*) AS c
        FROM Driver_Applications
        WHERE status IN ('Pending', 'Submitted')
        """
    )
    pending_count = cursor.fetchone()["c"]

    cursor.execute(
        "SELECT COUNT(DISTINCT driver_id) AS c FROM Driver_Sponsors WHERE status = 'Active'"
    )
    active_drivers = cursor.fetchone()["c"]

    cursor.execute("SELECT COUNT(*) AS c FROM Sponsors WHERE is_active = 1")
    active_sponsors = cursor.fetchone()["c"]

    cursor.execute(
        """
        SELECT COALESCE(SUM(point_change), 0) AS c
        FROM Point_Transactions
        WHERE point_change > 0
          AND created_at >= %s
        """,
        (month_start(),)
    )
    points_this_month = cursor.fetchone()["c"]

    cursor.execute("SELECT COUNT(*) AS c FROM Orders")
    rewards_redeemed = cursor.fetchone()["c"]

    cursor.execute(
        """
        SELECT
            Driver_Applications.application_id,
            COALESCE(Driver_Applications.applicant_first_name, Users.first_name) AS applicant_first_name,
            COALESCE(Driver_Applications.applicant_last_name, Users.last_name) AS applicant_last_name,
            COALESCE(Driver_Applications.applicant_email, Users.email) AS applicant_email,
            Driver_Applications.application_date,
            Driver_Applications.status,
            Sponsors.company_name AS sponsor_name
        FROM Driver_Applications
        LEFT JOIN Sponsors
            ON Driver_Applications.sponsor_id = Sponsors.sponsor_id
        -- Invites to existing drivers store driver_id instead of applicant details
        LEFT JOIN Users
            ON Driver_Applications.driver_id = Users.user_id
        WHERE Driver_Applications.status IN ('Pending', 'Submitted')
        ORDER BY Driver_Applications.application_date ASC
        LIMIT 5
        """
    )
    waiting_applications = cursor.fetchall()

    # Guarded so the dashboard still loads if Sponsor_Requests doesn't exist.
    try:
        cursor.execute(
            """
            SELECT request_id, first_name, last_name, email, company_name, submitted_at
            FROM Sponsor_Requests
            WHERE status = 'Submitted'
            ORDER BY submitted_at ASC
            """
        )
        waiting_sponsor_requests = cursor.fetchall()
    except Exception:
        waiting_sponsor_requests = []

    return {
        "pending_count": pending_count,
        "sponsor_request_count": len(waiting_sponsor_requests),
        "active_drivers": active_drivers,
        "active_sponsors": active_sponsors,
        "points_this_month": points_this_month,
        "rewards_redeemed": rewards_redeemed,
        "waiting_applications": waiting_applications,
        "waiting_sponsor_requests": waiting_sponsor_requests[:5],
    }


def get_sponsor_dashboard_data(cursor, sponsor_id):

    cursor.execute(
        """
        SELECT
            COALESCE(SUM(status = 'Active'), 0) AS active_drivers,
            COALESCE(SUM(CASE WHEN status = 'Active' THEN points_balance END), 0) AS points_held
        FROM Driver_Sponsors
        WHERE sponsor_id = %s
        """,
        (sponsor_id,)
    )
    driver_totals = cursor.fetchone() or {}

    cursor.execute(
        """
        SELECT COALESCE(SUM(point_change), 0) AS c
        FROM Point_Transactions
        WHERE sponsor_id = %s
          AND point_change > 0
          AND created_at >= %s
        """,
        (sponsor_id, month_start())
    )
    points_this_month = cursor.fetchone()["c"]

    cursor.execute(
        """
        SELECT
            Driver_Applications.application_id,
            COALESCE(Driver_Applications.applicant_first_name, Users.first_name) AS applicant_first_name,
            COALESCE(Driver_Applications.applicant_last_name, Users.last_name) AS applicant_last_name,
            COALESCE(Driver_Applications.applicant_email, Users.email) AS applicant_email,
            Driver_Applications.application_date
        FROM Driver_Applications
        -- Invites to existing drivers store driver_id instead of applicant details
        LEFT JOIN Users
            ON Driver_Applications.driver_id = Users.user_id
        WHERE Driver_Applications.sponsor_id = %s
          AND Driver_Applications.status = 'Submitted'
        ORDER BY Driver_Applications.application_date ASC
        """,
        (sponsor_id,)
    )
    new_applications = cursor.fetchall()

    cursor.execute(
        """
        SELECT
            Point_Transactions.point_change,
            Point_Transactions.reason,
            Point_Transactions.created_at,
            Users.first_name,
            Users.last_name,
            Point_Transactions.driver_id
        FROM Point_Transactions
        JOIN Users
            ON Point_Transactions.driver_id = Users.user_id
        WHERE Point_Transactions.sponsor_id = %s
        ORDER BY Point_Transactions.created_at DESC,
                 Point_Transactions.transaction_id DESC
        LIMIT 6
        """,
        (sponsor_id,)
    )
    recent_activity = cursor.fetchall()

    return {
        "active_drivers": int(driver_totals.get("active_drivers") or 0),
        "points_held": int(driver_totals.get("points_held") or 0),
        "points_this_month": points_this_month,
        "new_application_count": len(new_applications),
        "new_applications": new_applications[:5],
        "recent_activity": recent_activity,
    }


def render_sponsor_dashboard():

    conn = get_db()

    try:
        with conn.cursor() as cursor:
            data = get_sponsor_dashboard_data(cursor, current_sponsor_id())

            cursor.execute(
                "SELECT company_name FROM Sponsors WHERE sponsor_id = %s",
                (current_sponsor_id(),)
            )
            sponsor = cursor.fetchone()
    finally:
        conn.close()

    return render_template(
        "sponsor/dashboard.html",
        first_name=session["first_name"],
        role=session["role"],
        company_name=sponsor["company_name"] if sponsor else None,
        today=local_now(),
        **data
    )


@app.route("/")
def home():

    if "user_id" not in session:
        return redirect(url_for("login"))

    role = session["role"].lower()

    # -----------------------------------------
    # Sponsor home page
    # -----------------------------------------
    if role == "sponsor":
        return render_sponsor_dashboard()

        # -----------------------------------------
    # Driver home page
    # -----------------------------------------
    if role == "driver":
        return redirect(url_for("driver_dashboard"))

    # -----------------------------------------
    # Admin home page
    # -----------------------------------------
    if role == "admin":

        conn = get_db()

        try:
            with conn.cursor() as cursor:
                data = get_admin_dashboard_data(cursor)
        finally:
            conn.close()

        return render_template(
            "admin/dashboard.html",
            first_name=session["first_name"],
            role=session["role"],
            today=local_now(),
            **data
        )


    # -----------------------------------------
    # Other roles
    # -----------------------------------------

    return render_template(
        "shared/home.html",
        first_name=session["first_name"],
        role=session["role"]
    )


def get_sponsor_id_for_user(user_id):
    """Look up which sponsor organization a sponsor user belongs to."""
    try:
        conn = get_db()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT sponsor_id FROM Sponsor_Users WHERE user_id = %s",
                    (user_id,)
                )
                row = cursor.fetchone()
                return row["sponsor_id"] if row else user_id
        finally:
            conn.close()
    except Exception:
        return user_id


@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        remember = request.form.get("remember")

        conn = get_db()

        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        Users.user_id,
                        Users.first_name,
                        Users.last_name,
                        Users.role,
                        Users.is_active,
                        Password.password_hash
                    FROM Users
                    JOIN Password
                        ON Users.user_id = Password.user_id
                    WHERE Users.email = %s
                    """,
                    (email,)
                )

                user = cursor.fetchone()

        finally:
            conn.close()

        if user and check_password_hash(
            user["password_hash"],
            password
        ) and not user.get("is_active", True):
            log_login_attempt(email, succeeded=False, user_id=user["user_id"])
            driver_count, sponsor_count = get_login_stats()
            return render_template(
                "shared/login.html",
                error="This account has been deactivated. Contact an administrator.",
                step="login",
                driver_count=driver_count,
                sponsor_count=sponsor_count
            )

        if user and check_password_hash(
            user["password_hash"],
            password
        ):
            session.clear()

            session["user_id"] = user["user_id"]
            session["first_name"] = user["first_name"]
            session["last_name"] = user.get("last_name") or ""
            session["role"] = user["role"]

            if user["role"] == "Sponsor":
                session["sponsor_id"] = get_sponsor_id_for_user(user["user_id"])

            session.permanent = True

            session["last_activity"] = datetime.now(timezone.utc).isoformat()
            log_login_attempt(email, succeeded=True, user_id=user["user_id"])

            return redirect(url_for("home"))
        
        log_login_attempt(email, succeeded=False)
        driver_count, sponsor_count = get_login_stats()
        return render_template(
            "shared/login.html",
            error="Invalid email or password.",
            step="login",
            driver_count=driver_count,
            sponsor_count=sponsor_count
        )

    driver_count, sponsor_count = get_login_stats()
    return render_template(
        "shared/login.html",
        step="login",
        driver_count=driver_count,
        sponsor_count=sponsor_count
    )


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():

    if request.method == "GET":
        return render_template("shared/login.html", step="email")

    email = request.form.get("email", "").strip().lower()[:255]

    if not email:
        return render_template(
            "shared/login.html",
            step="email",
            error="Please enter your email address."
        )

    conn = get_db()

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id, email, first_name
                FROM Users
                WHERE LOWER(email) = %s
                """,
                (email,)
            )
            user = cursor.fetchone()

            if user:
                token = create_password_token(cursor, user["user_id"], "Reset")
                conn.commit()

    finally:
        conn.close()

    queue_event("Password Change", "Password reset requested", status="Request received",
                username=email, success=None)
    if user:
        send_password_reset_link(user["email"], user["first_name"], token)

    # Same answer whether or not the account exists, so this page can't be
    # used to find out who has an account.
    return render_template("shared/login.html", step="sent", email=email)


@app.route("/apply/<token>", methods=["GET", "POST"])
def apply(token):

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    application_id,
                    applicant_email,
                    driver_id,
                    sponsor_id,
                    status
                FROM Driver_Applications
                WHERE token = %s
                """,
                (token,)
            )

            application = cursor.fetchone()

            if not application:
                return render_template(
                    "shared/apply.html",
                    error="This invitation link is invalid or has expired."
                )

            if application["status"] != "Pending":
                return render_template(
                    "shared/apply.html",
                    error="This application has already been submitted or decided."
                )

            # Load the sponsor's custom questions for this application.
            cursor.execute(
                """
                SELECT question_id, question_text, is_required
                FROM Sponsor_Questions
                WHERE sponsor_id = %s
                ORDER BY display_order
                """,
                (application["sponsor_id"],)
            )

            questions = cursor.fetchall()

            if request.method == "POST":

                first_name = request.form.get("first_name", "").strip()
                last_name = request.form.get("last_name", "").strip()
                date_of_birth = request.form.get("date_of_birth", "").strip()
                address = request.form.get("address", "").strip()

                if not first_name or not last_name or not date_of_birth or not address:
                    return render_template(
                        "shared/apply.html",
                        email=application["applicant_email"],
                        questions=questions,
                        error="Please fill out all required fields."
                    )

                # Validate any required custom questions were answered.
                for q in questions:
                    if q["is_required"]:
                        answer = request.form.get(f"question_{q['question_id']}", "").strip()
                        if not answer:
                            return render_template(
                                "shared/apply.html",
                                email=application["applicant_email"],
                                questions=questions,
                                error="Please answer all required questions."
                            )

                cursor.execute(
                    """
                    UPDATE Driver_Applications
                    SET
                        status = 'Submitted',
                        applicant_first_name = %s,
                        applicant_last_name = %s,
                        applicant_dob = %s,
                        applicant_address = %s
                    WHERE application_id = %s
                    """,
                    (
                        first_name,
                        last_name,
                        date_of_birth,
                        address,
                        application["application_id"]
                    )
                )

                # Save answers to any custom questions.
                for q in questions:
                    answer = request.form.get(f"question_{q['question_id']}", "").strip()
                    if answer:
                        cursor.execute(
                            """
                            INSERT INTO Application_Answers
                                (application_id, question_id, answer_text)
                            VALUES
                                (%s, %s, %s)
                            """,
                            (
                                application["application_id"],
                                q["question_id"],
                                answer
                            )
                        )

                cursor.execute(

                    """
                    INSERT INTO Notifications
                        (user_id, notification_type, subject, message)
                    SELECT members.user_id, 'New Application', %s, %s
                    FROM (
                        -- Everyone on the sponsor's team, plus the org's founding user
                        SELECT user_id FROM Sponsor_Users WHERE sponsor_id = %s
                        UNION
                        SELECT %s
                    ) AS members
                    """,
                    (
                        "New application received",
                        f"{first_name} {last_name} ({application['applicant_email']}) submitted an application.",
                        application["sponsor_id"],
                        application["sponsor_id"]
                    )
                )

                conn.commit()
                queue_event("Driver Application", "Driver application submitted", success=True,
                            status="Submitted", sponsor_id=application["sponsor_id"],
                            details={"application_id": application["application_id"]})

                return render_template(
                    "shared/apply.html",
                    submitted=True
                )

            return render_template(
                "shared/apply.html",
                email=application["applicant_email"],
                questions=questions
            )

    finally:
        conn.close()

@app.route("/reset-password/<token>", methods=["GET", "POST"])
@app.route("/set-password/<token>", methods=["GET", "POST"])
def reset_password(token):

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            link = find_password_token(cursor, token)

            if not link:
                return render_template("shared/login.html", step="invalid")

            def show_form(error=None):
                return render_template(
                    "shared/login.html",
                    step="reset",
                    token=token,
                    purpose=link["purpose"],
                    error=error
                )

            if request.method == "GET":
                return show_form()

            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")

            if len(new_password) < MIN_PASSWORD_LENGTH:
                return show_form(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")

            if new_password != confirm_password:
                return show_form("The passwords do not match.")

            if link["purpose"] == "Reset":
                cursor.execute(
                    "SELECT password_hash FROM Password WHERE user_id = %s",
                    (link["user_id"],)
                )
                current = cursor.fetchone()

                if current and check_password_hash(current["password_hash"], new_password):
                    return show_form("Your new password must be different from your current password.")

            # Claim the link. Only one request can do this, so a link that is
            # submitted twice (or raced) changes the password at most once.
            cursor.execute(
                """
                UPDATE Password_Tokens
                SET used_at = NOW()
                WHERE token_hash = %s
                  AND used_at IS NULL
                  AND expires_at > NOW()
                """,
                (hash_token(token),)
            )

            if cursor.rowcount != 1:
                conn.rollback()
                return render_template("shared/login.html", step="invalid")

            cursor.execute(
                """
                INSERT INTO Password
                    (user_id, password_hash)
                VALUES
                    (%s, %s)
                ON DUPLICATE KEY UPDATE
                    password_hash = VALUES(password_hash)
                """,
                (link["user_id"], generate_password_hash(new_password, method="pbkdf2:sha256"))
            )

            # Any other link still sitting in the user's inbox stops working too.
            cursor.execute(
                """
                UPDATE Password_Tokens
                SET used_at = NOW()
                WHERE user_id = %s
                  AND used_at IS NULL
                """,
                (link["user_id"],)
            )

            cursor.execute(
                """
                INSERT INTO Audit_Logs
                    (user_id, category, action, username, success)
                VALUES
                    (%s, 'Password Change', %s, %s, 1)
                """,
                (
                    link["user_id"],
                    "Password reset by email link" if link["purpose"] == "Reset" else "Password set up by email link",
                    link["email"]
                )
            )

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    driver_count, sponsor_count = get_login_stats()
    return render_template(
        "shared/login.html",
        step="login",
        message="Your password has been saved. Please log in.",
        driver_count=driver_count,
        sponsor_count=sponsor_count
    )


@app.route("/logout")
def logout():
    if session.get("user_id") is not None:
        queue_event("Login Attempt", "Logout", status="Signed out", success=True)
    session.clear()
    return redirect(url_for("login"))

# ==========================================
# BULK USER UPLOAD
# ==========================================

@app.route("/bulk-upload", methods=["GET", "POST"])
def bulk_upload_page():

    if "user_id" not in session:
        return redirect(url_for("login"))

    role = (session.get("role") or "").lower()

    if role not in ("admin", "sponsor"):
        abort(403)

    # Disable bulk uploading during support/assumed-user mode.
    if (
        session.get("assumed_driver_id") is not None
        or session.get("assumed_sponsor_id") is not None
    ):
        abort(403)

    csrf_token = session.get("bulk_upload_csrf")

    if not csrf_token:
        csrf_token = secrets.token_urlsafe(32)
        session["bulk_upload_csrf"] = csrf_token

    results = None

    if request.method == "POST":

        submitted_token = request.form.get("csrf_token", "")

        if not secrets.compare_digest(
            submitted_token, csrf_token
        ):
            abort(400, "Invalid upload form token.")

        uploaded = request.files.get("upload_file")

        if (
            not uploaded
            or not uploaded.filename
            or not uploaded.filename.lower().endswith(".txt")
        ):
            flash("Please select a .txt file.", "error")

            return render_template(
                "shared/bulk_upload.html",
                csrf_token=csrf_token,
                results=None
            )

        def send_setup(email, first_name, token):
            body = (
                f"Hi {first_name},\n\n"
                "An account has been created for you "
                "in Drivers Advantage.\n\n"
                "Set your password using this link:\n"
                f"{password_link(token, 'Setup')}\n\n"
                "If you were not expecting this, "
                "contact your program administrator."
            )

            return send_email(
                email,
                "Set up your Drivers Advantage account",
                body
            )

        conn = get_db()

        try:
            target_sponsor_id = None

            if role == "sponsor":
                # Your app already stores the sponsor organization
                # ID in the session when the sponsor logs in.
                target_sponsor_id = current_sponsor_id()

                # Verify that the organization exists.
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT sponsor_id
                        FROM Sponsors
                        WHERE sponsor_id = %s
                        """,
                        (target_sponsor_id,)
                    )

                    if not cursor.fetchone():
                        abort(403)

            results = process_bulk_upload(
                uploaded.stream,
                conn,
                role,
                session["user_id"],
                target_sponsor_id,
                create_password_token,
                unusable_password_hash,
                send_setup
            )

        finally:
            conn.close()

    return render_template(
        "shared/bulk_upload.html",
        csrf_token=csrf_token,
        results=results
    )

@app.route("/about")
def about():
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            #get project metadata
            cursor.execute(
                "SELECT * FROM Project_Metadata ORDER BY metadata_id DESC LIMIT 1"
            )
            metadata = cursor.fetchone()
            #count drivers
            cursor.execute("SELECT COUNT(*) AS count FROM Drivers")
            driver_count = cursor.fetchone()["count"]
            #count sponsors
            cursor.execute("SELECT COUNT(*) AS count FROM Sponsors")
            sponsor_count = cursor.fetchone()["count"]
            #count rewards
            cursor.execute("SELECT COUNT(*) AS count FROM Orders")
            rewards_count = cursor.fetchone()["count"]
    finally:
        conn.close()
    return render_template("shared/about.html", metadata=metadata, driver_count = driver_count, sponsor_count = sponsor_count, rewards_count = rewards_count)


@app.route("/sponsor/applications", methods=["GET", "POST"])
def sponsor_applications():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "sponsor":
        return redirect(url_for("home"))

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            if request.method == "POST":

                driver_email = request.form.get(
                    "driver_email",
                    ""
                ).strip().lower()

                if not driver_email:
                    return render_template(
                        "sponsor/applications.html",
                        first_name=session["first_name"],
                        role=session["role"],
                        applications=[],
                        error="Please enter a driver's email address."
                    )

                sponsor_id = current_sponsor_id()

                # Make sure the sponsor actually exists.
                cursor.execute(
                    """
                    SELECT sponsor_id
                    FROM Sponsors
                    WHERE sponsor_id = %s
                    """,
                    (sponsor_id,)
                )

                sponsor = cursor.fetchone()

                if not sponsor:
                    return render_template(
                        "sponsor/applications.html",
                        first_name=session["first_name"],
                        role=session["role"],
                        applications=[],
                        error="Your account is not associated with a sponsor."
                    )

                # Check if this person already has a driver account.
                cursor.execute(
                    """
                    SELECT Drivers.driver_id
                    FROM Drivers
                    JOIN Users
                        ON Drivers.driver_id = Users.user_id
                    WHERE LOWER(Users.email) = %s
                    """,
                    (driver_email,)
                )

                existing_driver = cursor.fetchone()

                if existing_driver:
                    cursor.execute(
                        """
                        SELECT 1
                        FROM Driver_Sponsors
                        WHERE driver_id = %s
                          AND sponsor_id = %s
                          AND status = 'Active'
                        """,
                        (existing_driver["driver_id"], sponsor_id)
                    )

                    if cursor.fetchone():
                        return render_template(
                            "sponsor/applications.html",
                            first_name=session["first_name"],
                            role=session["role"],
                            applications=[],
                            error="That driver is already in your program."
                        )

                # Prevent duplicate pending applications.
                cursor.execute(
                    """
                    SELECT application_id
                    FROM Driver_Applications
                    WHERE sponsor_id = %s
                      AND status = 'Pending'
                      AND (
                          applicant_email = %s
                          OR driver_id = %s
                      )
                    """,
                    (
                        sponsor_id,
                        driver_email,
                        existing_driver["driver_id"] if existing_driver else None
                    )
                )

                existing_application = cursor.fetchone()

                if existing_application:
                    return render_template(
                        "sponsor/applications.html",
                        first_name=session["first_name"],
                        role=session["role"],
                        applications=[],
                        error="There is already a pending application for this email."
                    )

                token = secrets.token_urlsafe(32)

                if existing_driver:
                    cursor.execute(
                        """
                        INSERT INTO Driver_Applications
                            (driver_id, applicant_email, sponsor_id, status, token)
                        VALUES
                            (%s, %s, %s, 'Pending', %s)
                        """,
                        (
                            existing_driver["driver_id"],
                            driver_email,
                            sponsor_id,
                            token
                        )
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO Driver_Applications
                            (driver_id, applicant_email, sponsor_id, status, token)
                        VALUES
                            (NULL, %s, %s, 'Pending', %s)
                        """,
                        (
                            driver_email,
                            sponsor_id,
                            token
                        )
                    )

                conn.commit()
                queue_event("Driver Application", "Driver invited to apply", success=True,
                            status="Pending", sponsor_id=sponsor_id)

                send_application_invite(driver_email, token)

                return redirect(
                    url_for("sponsor_applications")
                )

            # Load sponsor's applications.
            status_filter = request.args.get("status", "all")
            if status_filter not in ("Pending", "Submitted", "Accepted", "Rejected"):
                status_filter = "all"
            sort_order = request.args.get("sort", "newest")
            if sort_order not in ("newest", "oldest"):
                sort_order = "newest"
            cursor.execute(
                """
                SELECT
                   
                    Driver_Applications.application_id,
                    Driver_Applications.application_date,
                    Driver_Applications.status,
                    Driver_Applications.decision_date,
                    Driver_Applications.reason,
                    Driver_Applications.applicant_email,
                    Driver_Applications.applicant_first_name,
                    Driver_Applications.applicant_last_name,
                    Driver_Applications.applicant_dob,
                    Driver_Applications.applicant_address,

                    Users.first_name AS driver_first_name,
                    Users.last_name AS driver_last_name,
                    Users.email AS driver_email

                FROM Driver_Applications

                LEFT JOIN Drivers
                    ON Driver_Applications.driver_id =
                       Drivers.driver_id

                LEFT JOIN Users
                    ON Drivers.driver_id = Users.user_id

                WHERE Driver_Applications.sponsor_id = %s
                    AND (%s = 'all' OR Driver_Applications.status = %s)
                

                ORDER BY
                    CASE WHEN %s = 'oldest' THEN 
                Driver_Applications.application_date END ASC,
                    Driver_Applications.application_date DESC
                """,
                (current_sponsor_id(), status_filter, status_filter, sort_order)
            )

            applications = cursor.fetchall()

                        # Counts by status for this sponsor.
            cursor.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM Driver_Applications
                WHERE sponsor_id = %s
                GROUP BY status
                """,
                (current_sponsor_id(),)
            )

            counts = {"Pending": 0, "Submitted": 0, "Accepted": 0, "Rejected": 0}
            for row in cursor.fetchall():
                counts[row["status"]] = row["count"]
            counts["Total"] = sum(counts.values())

            # Load custom question answers for each application.
            for application in applications:
                cursor.execute(
                    """
                    SELECT
                        Sponsor_Questions.question_text,
                        Application_Answers.answer_text
                    FROM Application_Answers
                    JOIN Sponsor_Questions
                        ON Application_Answers.question_id = Sponsor_Questions.question_id
                    WHERE Application_Answers.application_id = %s
                    """,
                    (application["application_id"],)
                )
                application["answers"] = cursor.fetchall()

            # Load this sponsor's custom application questions.
            cursor.execute(
                """
                SELECT question_id, question_text, is_required, display_order
                FROM Sponsor_Questions
                WHERE sponsor_id = %s
                ORDER BY display_order
                """,
                (current_sponsor_id(),)
            )

            questions = cursor.fetchall()

    finally:
        conn.close()

    return render_template(
        "sponsor/applications.html",
        first_name=session["first_name"],
        role=session["role"],
        applications=applications,
        questions=questions,
        counts=counts,
        status_filter=status_filter
    )
@app.route("/sponsor/questions", methods=["POST"])
def sponsor_questions():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "sponsor":
        return redirect(url_for("home"))

    sponsor_id = current_sponsor_id()
    question_text = request.form.get("question_text", "").strip()
    is_required = bool(request.form.get("is_required"))

    if question_text:
        conn = get_db()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COALESCE(MAX(display_order), 0) AS max_order
                    FROM Sponsor_Questions
                    WHERE sponsor_id = %s
                    """,
                    (sponsor_id,)
                )
                next_order = cursor.fetchone()["max_order"] + 1

                cursor.execute(
                    """
                    INSERT INTO Sponsor_Questions
                        (sponsor_id, question_text, is_required, display_order)
                    VALUES
                        (%s, %s, %s, %s)
                    """,
                    (sponsor_id, question_text, is_required, next_order)
                )
            conn.commit()
        finally:
            conn.close()

    return redirect(url_for("sponsor_applications"))


@app.route("/sponsor/applications/<int:application_id>/accept", methods=["POST"])
def accept_application(application_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "sponsor":
        return redirect(url_for("home"))

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                """
                SELECT *
                FROM Driver_Applications
                WHERE application_id = %s
                  AND sponsor_id = %s
                """,
                (application_id, current_sponsor_id())
            )

            application = cursor.fetchone()

            if not application or application["status"] != "Submitted":
                return redirect(url_for("sponsor_applications"))

            if application["driver_id"]:
                # Driver already has an account: add this sponsor to it.
                # Their other sponsors and point balances are untouched.
                existing_id = application["driver_id"]

                cursor.execute(
                    """
                    INSERT INTO Driver_Sponsors
                        (driver_id, sponsor_id, status)
                    VALUES
                        (%s, %s, 'Active')
                    ON DUPLICATE KEY UPDATE status = 'Active'
                    """,
                    (existing_id, current_sponsor_id())
                )

                cursor.execute(
                    "UPDATE Drivers SET status = 'Active' WHERE driver_id = %s",
                    (existing_id,)
                )

                cursor.execute(
                    """
                    REPLACE INTO Driver_Profiles
                        (driver_id, date_of_birth, address)
                    VALUES
                        (%s, %s, %s)
                    """,
                    (
                        existing_id,
                        application["applicant_dob"],
                        application["applicant_address"]
                    )
                )

                cursor.execute(
                    """
                    UPDATE Driver_Applications
                    SET
                        status = 'Accepted',
                        decision_date = NOW(),
                        decision_by_user_id = %s
                    WHERE application_id = %s
                    """,
                    (session["user_id"], application_id)
                )

                conn.commit()
                queue_event("Driver Application", "Driver application accepted", success=True,
                            status="Accepted", sponsor_id=application["sponsor_id"],
                            details={"application_id": application_id})

                send_acceptance_email(
                    application["applicant_email"],
                    application["applicant_first_name"]
                )

                return redirect(url_for("sponsor_applications"))

            # Create the Users record.
            cursor.execute(
                """
                INSERT INTO Users
                    (email, first_name, last_name, role)
                VALUES
                    (%s, %s, %s, 'Driver')
                """,
                (
                    application["applicant_email"],
                    application["applicant_first_name"],
                    application["applicant_last_name"]
                )
            )

            user_id = cursor.lastrowid

            # The driver chooses their own password from an emailed setup link.
            cursor.execute(
                """
                INSERT INTO Password
                    (user_id, password_hash)
                VALUES
                    (%s, %s)
                """,
                (user_id, unusable_password_hash())
            )
            setup_token = create_password_token(cursor, user_id, "Setup")

            # Create the Drivers record and their first sponsorship.
            cursor.execute(
                """
                INSERT INTO Drivers
                    (driver_id, points_balance, sponsor_id, status)
                VALUES
                    (%s, 0, %s, 'Active')
                """,
                (user_id, current_sponsor_id())
            )

            cursor.execute(
                """
                INSERT INTO Driver_Sponsors
                    (driver_id, sponsor_id, status)
                VALUES
                    (%s, %s, 'Active')
                """,
                (user_id, current_sponsor_id())
            )

            # Create the Driver_Profiles record.
            cursor.execute(
                """
                INSERT INTO Driver_Profiles
                    (driver_id, date_of_birth, address)
                VALUES
                    (%s, %s, %s)
                """,
                (user_id, application["applicant_dob"], application["applicant_address"])
            )

            # Update the application.
            cursor.execute(
                """
                UPDATE Driver_Applications
                SET
                    status = 'Accepted',
                    driver_id = %s,
                    decision_date = NOW(),
                    decision_by_user_id = %s
                WHERE application_id = %s
                """,
                (user_id, session["user_id"], application_id)
            )

            conn.commit()
            queue_event("Driver Application", "Driver application accepted", success=True,
                        status="Accepted", sponsor_id=application["sponsor_id"],
                        details={"application_id": application_id, "driver_id": user_id})

            send_acceptance_email(
                application["applicant_email"],
                application["applicant_first_name"],
                setup_token
            )

    finally:
        conn.close()

    return redirect(url_for("sponsor_applications"))


@app.route("/sponsor/applications/<int:application_id>/reject", methods=["POST"])
def reject_application(application_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "sponsor":
        return redirect(url_for("home"))

    reason = request.form.get("reason", "").strip()[:500]

    if not reason:
        return redirect(url_for("sponsor_applications"))

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                """
                SELECT *
                FROM Driver_Applications
                WHERE application_id = %s
                  AND sponsor_id = %s
                """,
                (application_id, current_sponsor_id())
            )

            application = cursor.fetchone()

            if not application or application["status"] != "Submitted":
                return redirect(url_for("sponsor_applications"))

            cursor.execute(
                """
                UPDATE Driver_Applications
                SET
                    status = 'Rejected',
                    reason = %s,
                    decision_date = NOW(),
                    decision_by_user_id = %s
                WHERE application_id = %s
                """,
                (reason, session["user_id"], application_id)
            )

            conn.commit()
            queue_event("Driver Application", "Driver application rejected", success=True,
                        status="Rejected", sponsor_id=application["sponsor_id"],
                        details={"application_id": application_id})

            send_rejection_email(
                application["applicant_email"],
                application["applicant_first_name"],
                reason
            )

    finally:
        conn.close()

    return redirect(url_for("sponsor_applications"))



@app.route("/sponsor/questions/<int:question_id>/delete", methods=["POST"])
def delete_sponsor_question(question_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "sponsor":
        return redirect(url_for("home"))

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM Sponsor_Questions
                WHERE question_id = %s AND sponsor_id = %s
                """,
                (question_id, current_sponsor_id())
            )
        conn.commit()
    finally:
        conn.close()

    return redirect(url_for("sponsor_applications"))

def delete_open_application(cursor, application_id, sponsor_id=None):
    """
    Remove an application that is still waiting (Pending or Submitted).

    Decided applications are kept as history. With sponsor_id, only that
    sponsor's application can be removed. Returns the deleted row, or None.
    """
    cursor.execute(
        """
        SELECT application_id, sponsor_id, driver_id, status, applicant_email
        FROM Driver_Applications
        WHERE application_id = %s
          AND status IN ('Pending', 'Submitted')
          AND (%s IS NULL OR sponsor_id = %s)
        FOR UPDATE
        """,
        (application_id, sponsor_id, sponsor_id)
    )
    application = cursor.fetchone()

    if not application:
        return None

    cursor.execute(
        "DELETE FROM Application_Answers WHERE application_id = %s",
        (application_id,)
    )

    # Its invite link stops working because the row (and token) is gone.
    cursor.execute(
        "DELETE FROM Driver_Applications WHERE application_id = %s",
        (application_id,)
    )

    cursor.execute(
        """
        INSERT INTO Audit_Logs
            (user_id, sponsor_id, category, action, status, username)
        VALUES
            (%s, %s, 'Driver Application', 'Application deleted', %s, %s)
        """,
        (session["user_id"], application["sponsor_id"],
         application["status"], application["applicant_email"])
    )

    return application


@app.route("/sponsor/applications/<int:application_id>/delete", methods=["POST"])
def sponsor_delete_application(application_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "sponsor":
        return redirect(url_for("home"))

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            deleted = delete_open_application(cursor, application_id, current_sponsor_id())
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if deleted:
        flash("Application deleted.", "success")
    else:
        flash("Only applications still waiting on a decision can be deleted.", "error")

    return redirect(url_for("sponsor_applications", **request.args))


def cancel_open_application(cursor, application_id, cancelled_by, reason,
                            driver_id=None, token=None):
    """
    Mark an application that is still waiting as Cancelled. Unlike deleting,
    it stays in the history. With driver_id or token, only that driver's
    application can be cancelled. Returns True if one was cancelled.
    """
    cursor.execute(
        """
        UPDATE Driver_Applications
        SET
            status = 'Cancelled',
            decision_date = NOW(),
            decision_by_user_id = %s,
            reason = %s
        WHERE application_id = %s
          AND status IN ('Pending', 'Submitted')
          AND (%s IS NULL OR driver_id = %s)
          AND (%s IS NULL OR token = %s)
        """,
        (cancelled_by, reason, application_id, driver_id, driver_id, token, token)
    )
    return cursor.rowcount == 1


@app.route("/admin/applications/<int:application_id>/cancel", methods=["POST"])
def admin_cancel_application(application_id):
    if (
        not session.get("user_id")
        or (session.get("role") or "").lower() != "admin"
    ):
        abort(403)

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cancelled = cancel_open_application(
                cursor, application_id, session["user_id"], "Cancelled by admin"
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if cancelled:
        queue_event("Driver Application", "Application cancelled by admin", success=True,
                    status="Cancelled", details={"application_id": application_id})
        flash("Application cancelled.", "success")
    else:
        flash("Only applications still waiting on a decision can be cancelled.", "error")

    return redirect(url_for("admin_pending_applications"))


@app.route("/driver/applications/<int:application_id>/cancel", methods=["POST"])
def driver_cancel_application(application_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if (session.get("role") or "").lower() != "driver":
        return redirect(url_for("home"))

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cancelled = cancel_open_application(
                cursor, application_id, session["user_id"], "Withdrawn by driver",
                driver_id=session["user_id"]
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if cancelled:
        queue_event("Driver Application", "Application withdrawn by driver", success=True,
                    status="Cancelled", details={"application_id": application_id})
        flash("Your application was withdrawn.", "success")
    else:
        flash("That application can't be withdrawn. It may already have been decided.", "error")

    return redirect(url_for("driver_sponsors"))


@app.route("/apply/<token>/withdraw", methods=["POST"])
def withdraw_application(token):
    """For applicants without an account yet: the invite link proves who they are."""
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT application_id FROM Driver_Applications WHERE token = %s",
                (token,)
            )
            application = cursor.fetchone()
            cancelled = bool(application) and cancel_open_application(
                cursor, application["application_id"], None, "Withdrawn by applicant", token=token
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if cancelled:
        queue_event("Driver Application", "Application withdrawn by applicant", success=True,
                    status="Cancelled", details={"application_id": application["application_id"]})
        return render_template("shared/apply.html", withdrawn=True)

    return render_template(
        "shared/apply.html",
        error="This application has already been submitted, decided, or withdrawn."
    )


@app.route("/admin/applications/<int:application_id>/delete", methods=["POST"])
def admin_delete_application(application_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "admin":
        return redirect(url_for("home"))

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            deleted = delete_open_application(cursor, application_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if deleted:
        flash("Application deleted.", "success")
    else:
        flash("Only applications still waiting on a decision can be deleted.", "error")

    return redirect(url_for("admin_pending_applications"))


@app.route("/admin/applications")
def admin_pending_applications():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "admin":
        return redirect(url_for("home"))

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    Driver_Applications.application_id,
                    Driver_Applications.application_date,
                    Driver_Applications.status,
                    Driver_Applications.applicant_email,
                    Driver_Applications.applicant_first_name,
                    Driver_Applications.applicant_last_name,

                    Sponsors.company_name AS sponsor_name

                FROM Driver_Applications

                JOIN Sponsors
                    ON Driver_Applications.sponsor_id = Sponsors.sponsor_id

                WHERE Driver_Applications.status IN ('Pending', 'Submitted')

                ORDER BY Driver_Applications.application_date DESC
                """
            )

            applications = cursor.fetchall()

    finally:
        conn.close()

    return render_template(
        "admin/pending_applications.html",
        first_name=session["first_name"],
        role=session["role"],
        applications=applications
    )

# One-click awards on the Drivers page. The amount and reason come from here,
# never from the form, so a quick award always means the same thing.
POINT_PRESETS = {
    "weekly": {"amount": 100, "reason": "Good weekly driving"},
    "above_beyond": {"amount": 500, "reason": "Above and beyond skills"},
}

# Built-in choices for the points reason dropdown. Sponsors add their own in
# Sponsor_Point_Reasons; "Other" still lets them type a one-off reason.
BASIC_POINT_REASONS = (
    "Safe driving week",
    "On-time deliveries",
    "Clean inspection",
    "Customer compliment",
    "Fuel efficiency",
    "Late delivery",
    "Traffic violation",
    "Policy violation",
)
OTHER_REASON = "__other__"


def sponsor_point_reasons(cursor, sponsor_id):
    """The sponsor's saved reasons, or none if the table hasn't been created yet."""
    try:
        cursor.execute(
            """
            SELECT reason_id, reason
            FROM Sponsor_Point_Reasons
            WHERE sponsor_id = %s
            ORDER BY reason
            """,
            (sponsor_id,)
        )
        return cursor.fetchall()
    except pymysql.err.ProgrammingError:
        return []


@app.route("/sponsor/drivers")
def sponsor_drivers():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "sponsor":
        return redirect(url_for("home"))

    sort_options = {
        "name": "Users.last_name, Users.first_name",
        "points_high": "Driver_Sponsors.points_balance DESC, Users.last_name",
        "points_low": "Driver_Sponsors.points_balance ASC, Users.last_name"
    }

    sort_key = request.args.get("sort", "name")
    if sort_key not in sort_options:
        sort_key = "name"

    def to_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    min_points = to_int(request.args.get("min_points"))
    max_points = to_int(request.args.get("max_points"))
    search = request.args.get("search", "").strip()[:100]
    search_pattern = f"%{' '.join(search.split())}%"

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            # The ORDER BY text comes from the fixed dictionary above.
            cursor.execute(
                f"""
                SELECT
                    Driver_Sponsors.driver_id,
                    Driver_Sponsors.points_balance,
                    Driver_Sponsors.status,
                    Driver_Sponsors.redeem_suspended,
                    Driver_Sponsors.suspend_reason,

                    Users.first_name,
                    Users.last_name,
                    Users.email

                FROM Driver_Sponsors

                JOIN Users
                    ON Driver_Sponsors.driver_id = Users.user_id

                -- Only this sponsor's membership row: other sponsors stay invisible.
                WHERE Driver_Sponsors.sponsor_id = %s
                    AND (%s IS NULL OR Driver_Sponsors.points_balance >= %s)
                    AND (%s IS NULL OR Driver_Sponsors.points_balance <= %s)
                    AND (
                        %s = ''
                        OR Users.first_name LIKE %s
                        OR Users.last_name LIKE %s
                        OR CONCAT_WS(' ', Users.first_name, Users.last_name) LIKE %s
                    )
                ORDER BY {sort_options[sort_key]}
                """,
                (
                    current_sponsor_id(),
                    min_points, min_points,
                    max_points, max_points,
                    search,
                    search_pattern, 
                    search_pattern, 
                    search_pattern
                )
            )

            drivers = cursor.fetchall()

            custom_reasons = sponsor_point_reasons(cursor, current_sponsor_id())

    finally:
        conn.close()

    return render_template(
        "sponsor/drivers.html",
        first_name=session["first_name"],
        role=session["role"],
        drivers=drivers,
        sort_key=sort_key,
        min_points=min_points,
        max_points=max_points, 
        search=search,
        point_presets=POINT_PRESETS,
        basic_reasons=BASIC_POINT_REASONS,
        custom_reasons=custom_reasons,
        other_reason=OTHER_REASON
    )


@app.route("/sponsor/point-reasons", methods=["POST"])
def add_point_reason():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "sponsor":
        return redirect(url_for("home"))

    reason = " ".join(request.form.get("reason", "").split())[:255]

    if not reason:
        flash("Type a reason to save.", "error")
        return redirect(url_for("sponsor_drivers"))

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            existing = [r["reason"].lower() for r in sponsor_point_reasons(cursor, current_sponsor_id())]

            if reason.lower() in existing or reason.lower() in (r.lower() for r in BASIC_POINT_REASONS):
                flash(f'"{reason}" is already in your list.', "error")
                return redirect(url_for("sponsor_drivers"))

            cursor.execute(
                "INSERT INTO Sponsor_Point_Reasons (sponsor_id, reason) VALUES (%s, %s)",
                (current_sponsor_id(), reason)
            )
        conn.commit()
    finally:
        conn.close()

    flash(f'Saved "{reason}" to your point reasons.', "success")
    return redirect(url_for("sponsor_drivers"))


@app.route("/sponsor/point-reasons/<int:reason_id>/delete", methods=["POST"])
def delete_point_reason(reason_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "sponsor":
        return redirect(url_for("home"))

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM Sponsor_Point_Reasons WHERE reason_id = %s AND sponsor_id = %s",
                (reason_id, current_sponsor_id())
            )
        conn.commit()
    finally:
        conn.close()

    flash("Point reason removed.", "success")
    return redirect(url_for("sponsor_drivers"))

@app.route("/sponsor/drivers/<int:driver_id>/points", methods=["POST"])
def change_driver_points(driver_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "sponsor":
        return redirect(url_for("home"))

    preset = POINT_PRESETS.get(request.form.get("preset", ""))

    if preset:
        action = "add"
        amount = preset["amount"]
        reason = preset["reason"]
    else:
        action = request.form.get("action", "")
        reason = request.form.get("reason_choice", OTHER_REASON)
        if reason == OTHER_REASON:
            reason = request.form.get("reason", "")
        reason = reason.strip()[:255]

        try:
            amount = int(request.form.get("amount", ""))
        except ValueError:
            amount = 0

    if action not in ("add", "deduct") or amount <= 0 or not reason:
        flash("Enter a whole number of points above 0 and a reason.", "error")
        return redirect(url_for("sponsor_drivers"))

    point_change = amount if action == "add" else -amount

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            # Lock the row so two changes can't overwrite each other.
            cursor.execute(
                """
                SELECT points_balance
                FROM Driver_Sponsors
                WHERE driver_id = %s
                  AND sponsor_id = %s
                FOR UPDATE
                """,
                (driver_id, current_sponsor_id())
            )

            driver = cursor.fetchone()

            if not driver:
                return redirect(url_for("sponsor_drivers"))

            if (driver["points_balance"] or 0) + point_change < 0:
                flash("You can't deduct more points than the driver has.", "error")
                return redirect(url_for("sponsor_drivers"))

            cursor.execute(
                """
                UPDATE Driver_Sponsors
                SET points_balance = points_balance + %s
                WHERE driver_id = %s
                  AND sponsor_id = %s
                """,
                (point_change, driver_id, current_sponsor_id())
            )

            cursor.execute(
                "SELECT company_name FROM Sponsors WHERE sponsor_id = %s",
                (current_sponsor_id(),)
            )
            sponsor = cursor.fetchone()
            sponsor_name = sponsor["company_name"] if sponsor else "Your sponsor"

            cursor.execute(
                """
                INSERT INTO Point_Transactions
                    (driver_id, sponsor_id, changed_by_user_id, point_change, reason)
                VALUES
                    (%s, %s, %s, %s, %s)
                """,
                (
                    driver_id,
                    current_sponsor_id(),
                    session["user_id"],
                    point_change,
                    reason
                )
            )

            if action == "add":
                note_subject = f"{amount} points added"
                note_message = f"{sponsor_name} added {amount} points to your account. Reason: {reason}"
            else:
                note_subject = f"{amount} points removed"
                note_message = f"{sponsor_name} deducted {amount} points from your account. Reason: {reason}"

            cursor.execute(
                """
                INSERT INTO Notifications
                    (user_id, notification_type, subject, message)
                VALUES
                    (%s, 'Point Change', %s, %s)
                """,
                (driver_id, note_subject, note_message)
            )

        conn.commit()
        queue_event("Point Change", "Driver points adjusted", status="Committed", success=True,
                    sponsor_id=current_sponsor_id(), driver_id=driver_id,
                    details={"driver_id": driver_id, "point_delta": point_change})

    finally:
        conn.close()

    if action == "add":
        flash(f"Added {amount} points ({reason}).", "success")
    else:
        flash(f"Deducted {amount} points.", "success")

    return redirect(url_for("sponsor_drivers"))


@app.route("/sponsor/drivers/<int:driver_id>/redemption", methods=["POST"])
def set_driver_redemption(driver_id):
    """Suspend or restore a driver's ability to spend this sponsor's points."""

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "sponsor":
        return redirect(url_for("home"))

    action = request.form.get("action", "")
    reason = request.form.get("reason", "").strip()[:255]

    if action not in ("suspend", "resume"):
        return redirect(url_for("sponsor_drivers"))

    if action == "suspend" and not reason:
        flash("Enter a reason for suspending redemption.", "error")
        return redirect(url_for("sponsor_drivers"))

    suspended = action == "suspend"

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                """
                UPDATE Driver_Sponsors
                SET
                    redeem_suspended = %s,
                    suspend_reason = %s
                WHERE driver_id = %s
                  AND sponsor_id = %s
                """,
                (1 if suspended else 0, reason if suspended else None,
                 driver_id, current_sponsor_id())
            )

            # Only this sponsor's drivers can be changed.
            if cursor.rowcount == 0:
                conn.rollback()
                return redirect(url_for("sponsor_drivers"))

            cursor.execute(
                "SELECT company_name FROM Sponsors WHERE sponsor_id = %s",
                (current_sponsor_id(),)
            )
            sponsor = cursor.fetchone()
            sponsor_name = sponsor["company_name"] if sponsor else "Your sponsor"

            if suspended:
                subject = "Point redemption paused"
                message = (
                    f"{sponsor_name} has paused redeeming your points while an issue "
                    f"is reviewed. Reason: {reason}. You can still earn points."
                )
            else:
                subject = "Point redemption restored"
                message = f"{sponsor_name} has restored your ability to redeem points."

            cursor.execute(
                """
                INSERT INTO Notifications
                    (user_id, notification_type, subject, message)
                VALUES
                    (%s, 'Point Change', %s, %s)
                """,
                (driver_id, subject, message)
            )

        conn.commit()
        queue_event("Point Change", "Driver redemption access changed", status="Committed",
                    success=True, sponsor_id=current_sponsor_id(), driver_id=driver_id,
                    details={"driver_id": driver_id, "operation": "suspend" if suspended else "restore"})

    finally:
        conn.close()

    if suspended:
        flash("Point redemption suspended for this driver.", "success")
    else:
        flash("Point redemption restored for this driver.", "success")

    return redirect(url_for("sponsor_drivers"))
                                    
               


@app.route("/sponsor/dashboard")
def sponsor_dashboard():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "sponsor":
        return redirect(url_for("home"))

    return render_sponsor_dashboard()

def get_driver_profile_data(cursor, driver_id, sponsor_id=None):
    """
    A driver's profile plus their membership with ONE sponsor.

    Sponsors pass their own id so they only ever see their own points,
    status, and application answers. Drivers omit it and get the sponsor
    they're currently viewing.
    """

    if sponsor_id is None:
        chosen = pick_driver_sponsorship(get_driver_sponsorships(cursor, driver_id))
        sponsor_id = chosen["sponsor_id"] if chosen else None

    cursor.execute(
        """
        SELECT
            Drivers.driver_id,
            Driver_Sponsors.sponsor_id,
            COALESCE(Driver_Sponsors.points_balance, 0) AS points_balance,
            COALESCE(Driver_Sponsors.status, Drivers.status) AS status,

            Users.first_name,
            Users.last_name,
            Users.email,

            Driver_Profiles.date_of_birth,
            Driver_Profiles.address,

            Sponsors.company_name AS sponsor_name,
            Sponsors.email AS sponsor_email

        FROM Drivers

        JOIN Users
            ON Drivers.driver_id = Users.user_id

        LEFT JOIN Driver_Profiles
            ON Drivers.driver_id = Driver_Profiles.driver_id

        LEFT JOIN Driver_Sponsors
            ON Driver_Sponsors.driver_id = Drivers.driver_id
           AND Driver_Sponsors.sponsor_id = %s

        LEFT JOIN Sponsors
            ON Driver_Sponsors.sponsor_id = Sponsors.sponsor_id

        WHERE Drivers.driver_id = %s
        """,
        (sponsor_id, driver_id)
    )

    driver = cursor.fetchone()

    cursor.execute(
        """
        SELECT
            Sponsor_Questions.question_text,
            Application_Answers.answer_text

        FROM Application_Answers

        JOIN Driver_Applications
            ON Application_Answers.application_id =
               Driver_Applications.application_id

        JOIN Sponsor_Questions
            ON Application_Answers.question_id =
               Sponsor_Questions.question_id

        WHERE Driver_Applications.driver_id = %s
          AND Driver_Applications.sponsor_id = %s
          AND Driver_Applications.status = 'Accepted'

        ORDER BY Sponsor_Questions.display_order
        """,
        (driver_id, sponsor_id)
    )

    answers = cursor.fetchall()

    return driver, answers


@app.route("/driver/profile")
def driver_profile():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "driver":
        return redirect(url_for("home"))

    conn = get_db()

    try:
        with conn.cursor() as cursor:
            driver, answers = get_driver_profile_data(
                cursor,
                session["user_id"]
            )
    finally:
        conn.close()

    if not driver:
        return redirect(url_for("home"))

    return render_template(
        "driver/profile.html",
        first_name=session["first_name"],
        role=session["role"],
        driver=driver,
        answers=answers,
        sponsor_view=False
    )


@app.route("/sponsor/drivers/<int:driver_id>")
def sponsor_driver_profile(driver_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "sponsor":
        return redirect(url_for("home"))

    conn = get_db()

    try:
        with conn.cursor() as cursor:
            driver, answers = get_driver_profile_data(cursor, driver_id, current_sponsor_id())
    finally:
        conn.close()

    # A sponsor can only view their own drivers.
    if not driver or driver["sponsor_id"] != current_sponsor_id():
        return redirect(url_for("sponsor_drivers"))

    return render_template(
        "driver/profile.html",
        first_name=session["first_name"],
        role=session["role"],
        driver=driver,
        answers=answers,
        sponsor_view=True
    )


def save_driver_profile(cursor, driver_id, form):
    """Validates and saves profile fields. Returns an error message or None."""
    from datetime import date

    first_name = form.get("first_name", "").strip()[:100]
    last_name = form.get("last_name", "").strip()[:100]
    date_of_birth = form.get("date_of_birth", "").strip()
    address = form.get("address", "").strip()[:500]

    if not first_name or not last_name or not date_of_birth or not address:
        return "Please fill out every field."

    try:
        date.fromisoformat(date_of_birth)
    except ValueError:
        return "Please enter a valid date of birth."

    cursor.execute(
        """
        UPDATE Users
        SET first_name = %s, last_name = %s
        WHERE user_id = %s
        """,
        (first_name, last_name, driver_id)
    )

    cursor.execute(
        """
        INSERT INTO Driver_Profiles
            (driver_id, date_of_birth, address)
        VALUES
            (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
            date_of_birth = VALUES(date_of_birth),
            address = VALUES(address)
        """,
        (driver_id, date_of_birth, address)
    )

    return None


def profile_form_values(driver):
    dob = driver.get("date_of_birth")
    return {
        "first_name": driver.get("first_name") or "",
        "last_name": driver.get("last_name") or "",
        "date_of_birth": dob.isoformat() if hasattr(dob, "isoformat") else (dob or ""),
        "address": driver.get("address") or ""
    }


@app.route("/driver/profile/edit", methods=["GET", "POST"])
def driver_edit_profile():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "driver":
        return redirect(url_for("home"))

    error = None
    posted_form = None

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            if request.method == "POST":
                error = save_driver_profile(cursor, session["user_id"], request.form)

                if not error:
                    conn.commit()
                    queue_event("Profile", "Driver profile updated", success=True,
                                status="Saved", driver_id=session["user_id"])
                    session["first_name"] = request.form.get("first_name", "").strip()[:100]
                    session["last_name"] = request.form.get("last_name", "").strip()[:100]
                    return redirect(url_for("driver_profile"))

                posted_form = request.form

            driver, answers = get_driver_profile_data(cursor, session["user_id"])

    finally:
        conn.close()

    if not driver:
        return redirect(url_for("home"))

    return render_template(
        "driver/edit_profile.html",
        first_name=session["first_name"],
        role=session["role"],
        driver=driver,
        form=posted_form or profile_form_values(driver),
        form_action=url_for("driver_edit_profile"),
        back_url=url_for("driver_profile"),
        sponsor_view=False,
        error=error
    )


@app.route("/sponsor/drivers/<int:driver_id>/edit", methods=["GET", "POST"])
def sponsor_edit_driver(driver_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "sponsor":
        return redirect(url_for("home"))

    error = None
    posted_form = None

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            driver, answers = get_driver_profile_data(cursor, driver_id, current_sponsor_id())

            # A sponsor can only edit their own drivers.
            if not driver or driver["sponsor_id"] != current_sponsor_id():
                return redirect(url_for("sponsor_drivers"))

            if request.method == "POST":
                error = save_driver_profile(cursor, driver_id, request.form)

                if not error:
                    conn.commit()
                    queue_event("Profile", "Driver profile updated by sponsor", success=True,
                                status="Saved", sponsor_id=current_sponsor_id(),
                                driver_id=driver_id)
                    return redirect(url_for("sponsor_driver_profile", driver_id=driver_id))

                posted_form = request.form

    finally:
        conn.close()

    return render_template(
        "driver/edit_profile.html",
        first_name=session["first_name"],
        role=session["role"],
        driver=driver,
        form=posted_form or profile_form_values(driver),
        form_action=url_for("sponsor_edit_driver", driver_id=driver_id),
        back_url=url_for("sponsor_driver_profile", driver_id=driver_id),
        sponsor_view=True,
        error=error
    )


def advance_run(next_run, frequency, now):
    """Moves next_run forward by one period until it is in the future."""
    import calendar
    from datetime import timedelta as td

    while next_run <= now:
        if frequency == "Daily":
            next_run = next_run + td(days=1)
        elif frequency == "Weekly":
            next_run = next_run + td(days=7)
        else:
            month = next_run.month % 12 + 1
            year = next_run.year + (1 if next_run.month == 12 else 0)
            day = min(next_run.day, calendar.monthrange(year, month)[1])
            next_run = next_run.replace(year=year, month=month, day=day)
    return next_run


def process_recurring_points():
    """Gives points for every recurring rule that is due. Safe to call often."""
    conn = get_db()

    try:
        with conn.cursor() as cursor:

            cursor.execute("SELECT NOW() AS now")
            now = cursor.fetchone()["now"]

            cursor.execute(
                """
                SELECT recurring_id, sponsor_id, point_change, reason,
                       frequency, next_run, all_drivers
                FROM Recurring_Points
                WHERE is_active = 1 AND next_run <= %s
                """,
                (now,)
            )
            rules = cursor.fetchall()

            for rule in rules:

                new_next = advance_run(rule["next_run"], rule["frequency"], now)

                # Claim the rule first so two requests cannot both award it.
                cursor.execute(
                    """
                    UPDATE Recurring_Points
                    SET next_run = %s, last_run = %s
                    WHERE recurring_id = %s AND next_run = %s
                    """,
                    (new_next, now, rule["recurring_id"], rule["next_run"])
                )

                if cursor.rowcount == 0:
                    continue

                cursor.execute(
                    """
                    SELECT driver_id FROM Driver_Sponsors
                    WHERE sponsor_id = %s AND status = 'Active'
                      AND (%s = 1 OR driver_id IN (
                          SELECT driver_id FROM Recurring_Point_Drivers
                          WHERE recurring_id = %s
                      ))
                    """,
                    (rule["sponsor_id"], rule["all_drivers"],
                     rule["recurring_id"])
                )

                for d in cursor.fetchall():

                    cursor.execute(
                        """
                        UPDATE Driver_Sponsors
                        SET points_balance = points_balance + %s
                        WHERE driver_id = %s
                          AND sponsor_id = %s
                        """,
                        (rule["point_change"], d["driver_id"], rule["sponsor_id"])
                    )

                    cursor.execute(
                        """
                        INSERT INTO Point_Transactions
                            (driver_id, sponsor_id, changed_by_user_id,
                             point_change, reason)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (d["driver_id"], rule["sponsor_id"], rule["sponsor_id"],
                         rule["point_change"], rule["reason"])
                    )

                    cursor.execute(
                        """
                        INSERT INTO Notifications
                            (user_id, notification_type, subject, message)
                        VALUES (%s, 'Point Change', %s, %s)
                        """,
                        (d["driver_id"],
                         str(rule["point_change"]) + " points added",
                         "Recurring points: " + rule["reason"])
                    )

        conn.commit()

    finally:
        conn.close()


# Rules come due daily at the soonest, so checking once a minute per worker is
# plenty and saves a database round trip on almost every page.
RECURRING_CHECK_SECONDS = 60
last_recurring_check = 0.0


@app.before_request
def run_due_recurring_points():
    global last_recurring_check

    if request.method != "GET" or request.endpoint in (None, "static"):
        return

    now = time.monotonic()
    if now - last_recurring_check < RECURRING_CHECK_SECONDS:
        return
    last_recurring_check = now

    try:
        process_recurring_points()
    except Exception as e:
        print("Recurring points error:", e)


@app.route("/sponsor/recurring", methods=["GET", "POST"])
def sponsor_recurring():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "sponsor":
        return redirect(url_for("home"))

    error = None
    conn = get_db()

    try:
        with conn.cursor() as cursor:

            if request.method == "POST":
                from datetime import date

                action = request.form.get("action", "create")
                recurring_id = request.form.get("recurring_id", type=int)

                if action == "toggle" and recurring_id:
                    cursor.execute(
                        """
                        UPDATE Recurring_Points
                        SET is_active = 1 - is_active
                        WHERE recurring_id = %s AND sponsor_id = %s
                        """,
                        (recurring_id, current_sponsor_id())
                    )
                    conn.commit()
                    queue_event("Point Change", "Recurring points rule updated", success=True,
                                status="Committed", sponsor_id=current_sponsor_id(),
                                details={"recurring_id": recurring_id,
                                         "operation": action})
                    return redirect(url_for("sponsor_recurring"))

                if action == "delete" and recurring_id:
                    cursor.execute(
                        """
                        DELETE FROM Recurring_Points
                        WHERE recurring_id = %s AND sponsor_id = %s
                        """,
                        (recurring_id, current_sponsor_id())
                    )
                    conn.commit()
                    queue_event("Point Change", "Recurring points rule updated", success=True,
                                status="Committed", sponsor_id=current_sponsor_id(),
                                details={"recurring_id": recurring_id,
                                         "operation": action})
                    return redirect(url_for("sponsor_recurring"))

                points = request.form.get("point_change", type=int)
                reason = request.form.get("reason", "").strip()[:255]
                frequency = request.form.get("frequency", "")
                start_date = request.form.get("start_date", "")
                all_drivers = request.form.get("target", "all") != "selected"
                picked_ids = set(request.form.getlist("driver_ids", type=int))

                # Only keep drivers that actually belong to this sponsor.
                cursor.execute(
                    """
                    SELECT driver_id FROM Driver_Sponsors
                    WHERE sponsor_id = %s AND status = 'Active'
                    """,
                    (current_sponsor_id(),)
                )
                own_ids = {row["driver_id"] for row in cursor.fetchall()}
                picked_ids &= own_ids

                try:
                    date.fromisoformat(start_date)
                    valid_date = True
                except ValueError:
                    valid_date = False

                if not points or points <= 0:
                    error = "Enter a point amount greater than 0."
                elif not reason:
                    error = "Enter a reason."
                elif frequency not in ("Daily", "Weekly", "Monthly"):
                    error = "Choose a frequency."
                elif not valid_date:
                    error = "Choose a start date."
                elif not all_drivers and not picked_ids:
                    error = "Select at least one driver."
                else:
                    cursor.execute(
                        """
                        INSERT INTO Recurring_Points
                            (sponsor_id, point_change, reason, frequency,
                             next_run, all_drivers)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (current_sponsor_id(), points, reason, frequency,
                         start_date, 1 if all_drivers else 0)
                    )
                    new_id = cursor.lastrowid

                    if not all_drivers:
                        cursor.executemany(
                            """
                            INSERT INTO Recurring_Point_Drivers
                                (recurring_id, driver_id)
                            VALUES (%s, %s)
                            """,
                            [(new_id, driver_id) for driver_id in picked_ids]
                        )

                    conn.commit()
                    queue_event("Point Change", "Recurring points rule created", success=True,
                                status="Committed", sponsor_id=current_sponsor_id(),
                                details={"recurring_id": new_id})
                    return redirect(url_for("sponsor_recurring"))

            cursor.execute(
                """
                SELECT recurring_id, point_change, reason, frequency,
                       next_run, last_run, is_active, all_drivers
                FROM Recurring_Points
                WHERE sponsor_id = %s
                ORDER BY recurring_id DESC
                """,
                (current_sponsor_id(),)
            )
            rules = cursor.fetchall()

            # Names of the chosen drivers for each "selected drivers" rule.
            cursor.execute(
                """
                SELECT Recurring_Point_Drivers.recurring_id,
                       Users.first_name, Users.last_name
                FROM Recurring_Point_Drivers
                JOIN Recurring_Points
                    ON Recurring_Points.recurring_id = Recurring_Point_Drivers.recurring_id
                JOIN Users
                    ON Users.user_id = Recurring_Point_Drivers.driver_id
                WHERE Recurring_Points.sponsor_id = %s
                ORDER BY Users.last_name, Users.first_name
                """,
                (current_sponsor_id(),)
            )
            names_by_rule = {}
            for row in cursor.fetchall():
                names_by_rule.setdefault(row["recurring_id"], []).append(
                    row["first_name"] + " " + row["last_name"]
                )
            for rule in rules:
                rule["driver_names"] = names_by_rule.get(rule["recurring_id"], [])

            cursor.execute(
                """
                SELECT Driver_Sponsors.driver_id, Users.first_name, Users.last_name
                FROM Driver_Sponsors
                JOIN Users ON Driver_Sponsors.driver_id = Users.user_id
                WHERE Driver_Sponsors.sponsor_id = %s AND Driver_Sponsors.status = 'Active'
                ORDER BY Users.last_name, Users.first_name
                """,
                (current_sponsor_id(),)
            )
            drivers = cursor.fetchall()

    finally:
        conn.close()

    return render_template(
        "sponsor/recurring.html",
        first_name=session["first_name"],
        role=session["role"],
        rules=rules,
        drivers=drivers,
        error=error
    )


@app.route("/driver/dashboard")
def driver_dashboard():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "driver":
        return redirect(url_for("home"))

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                "SELECT driver_id, status FROM Drivers WHERE driver_id = %s",
                (session["user_id"],)
            )
            account = cursor.fetchone()

            sponsorships = get_driver_sponsorships(cursor, session["user_id"])
            current = pick_driver_sponsorship(sponsorships)

            driver = None
            if account:
                driver = {
                    "driver_id": account["driver_id"],
                    "points_balance": current["points_balance"] if current else 0,
                    "status": current["status"] if current else account["status"],
                    "sponsor_name": current["company_name"] if current else None,
                }

            # Only the activity for the sponsor being viewed.
            cursor.execute(
                """
                SELECT point_change, reason, created_at
                FROM Point_Transactions
                WHERE driver_id = %s
                  AND sponsor_id = %s
                ORDER BY created_at DESC, transaction_id DESC
                LIMIT 5
                """,
                (session["user_id"], current["sponsor_id"] if current else None)
            )

            recent_activity = cursor.fetchall()

    finally:
        conn.close()

    if not driver:
        return redirect(url_for("logout"))

    return render_template(
        "driver/dashboard.html",
        first_name=session["first_name"],
        role=session["role"],
        driver=driver,
        sponsorships=sponsorships,
        recent_activity=recent_activity,
        today=local_now()
    )
@app.route("/driver/sponsors")
def driver_sponsors(): 
    if "user_id" not in session: 
        return redirect(url_for("login"))
    if session["role"].lower() != "driver":
        return redirect(url_for("home"))
    conn = get_db()
    try: 
        with conn.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    sponsor_id,
                    company_name,
                    email,
                    phone,
                    point_value
                FROM Sponsors
                WHERE is_active = TRUE
                ORDER BY company_name
                """
            )
            sponsors = cursor.fetchall()

            cursor.execute(
                """
                SELECT
                    Driver_Applications.application_id,
                    Driver_Applications.status,
                    Driver_Applications.application_date,
                    Sponsors.company_name
                FROM Driver_Applications
                JOIN Sponsors
                    ON Sponsors.sponsor_id = Driver_Applications.sponsor_id
                WHERE Driver_Applications.driver_id = %s
                  AND Driver_Applications.status IN ('Pending', 'Submitted')
                ORDER BY Driver_Applications.application_date DESC
                """,
                (session["user_id"],)
            )
            open_applications = cursor.fetchall()
    finally:
        conn.close()
    return render_template(
        "driver/sponsors.html",
        first_name=session["first_name"],
        role=session["role"],
        sponsors=sponsors,
        open_applications=open_applications
    )

@app.route("/driver/points")
def driver_points():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "driver":
        return redirect(url_for("home"))

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                "SELECT driver_id FROM Drivers WHERE driver_id = %s",
                (session["user_id"],)
            )
            account = cursor.fetchone()

            # Points never mix between sponsors: everything on this page is
            # for the one sponsor the driver is currently viewing.
            sponsorships = get_driver_sponsorships(cursor, session["user_id"])
            current = pick_driver_sponsorship(sponsorships)
            viewing_sponsor_id = current["sponsor_id"] if current else None

            driver = None
            if account:
                driver = {
                    "points_balance": current["points_balance"] if current else 0,
                    "sponsor_name": current["company_name"] if current else None,
                }

            cursor.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN point_change > 0 THEN point_change END), 0) AS earned,
                    COALESCE(SUM(CASE WHEN point_change < 0 THEN -point_change END), 0) AS deducted
                FROM Point_Transactions
                WHERE driver_id = %s
                  AND sponsor_id = %s
                """,
                (session["user_id"], viewing_sponsor_id)
            )
            totals = cursor.fetchone()

            cursor.execute(
                """
                SELECT
                    Point_Transactions.point_change,
                    Point_Transactions.reason,
                    Point_Transactions.created_at,
                    Sponsors.company_name AS sponsor_name,
                    CONCAT(Users.first_name, ' ', Users.last_name) AS changed_by_name
                FROM Point_Transactions
                LEFT JOIN Sponsors
                    ON Point_Transactions.sponsor_id = Sponsors.sponsor_id
                LEFT JOIN Users
                    ON Point_Transactions.changed_by_user_id = Users.user_id
                WHERE Point_Transactions.driver_id = %s
                  AND Point_Transactions.sponsor_id = %s
                ORDER BY Point_Transactions.created_at DESC,
                         Point_Transactions.transaction_id DESC
                LIMIT 50
                """,
                (session["user_id"], viewing_sponsor_id)
            )
            history = cursor.fetchall()

            cursor.execute(
                """
                SELECT
                    Point_Transactions.point_change,
                    Point_Transactions.reason,
                    Point_Transactions.created_at,
                    Sponsors.company_name AS sponsor_name,
                    CONCAT(Users.first_name, ' ', Users.last_name) AS changed_by_name
                FROM Point_Transactions
                LEFT JOIN Sponsors
                    ON Point_Transactions.sponsor_id = Sponsors.sponsor_id
                LEFT JOIN Users
                    ON Point_Transactions.changed_by_user_id = Users.user_id
                WHERE Point_Transactions.driver_id = %s
                  AND Point_Transactions.sponsor_id = %s
                  AND Point_Transactions.point_change < 0
                ORDER BY Point_Transactions.created_at DESC,
                         Point_Transactions.transaction_id DESC
                LIMIT 5
                """,
                (session["user_id"], viewing_sponsor_id)
            )
            recent_deductions = cursor.fetchall()

    finally:
        conn.close()

    if not driver:
        return redirect(url_for("logout"))

    return render_template(
        "driver/points.html",
        first_name=session["first_name"],
        role=session["role"],
        driver=driver,
        sponsorships=sponsorships,
        totals=totals,
        history=history,
        recent_deductions=recent_deductions
    )


@app.route("/driver/switch-sponsor", methods=["POST"])
def switch_driver_sponsor():
    """Choose which sponsor's points, history, and catalog the driver sees."""

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "driver":
        return redirect(url_for("home"))

    try:
        # Validated on use: pick_driver_sponsorship ignores ids the driver
        # doesn't actually belong to.
        selected = int(request.form.get("sponsor_id", ""))
        session["driver_sponsor_id"] = selected
        queue_event("Sponsor Management", "Sponsor view selected", success=True,
                    status="Preference set", details={"sponsor_id": selected})
    except ValueError:
        pass

    # Go back to the page they were on, but never off-site.
    back = request.referrer or ""
    if not back.startswith(request.host_url):
        back = url_for("driver_dashboard")

    return redirect(back)


@app.route("/settings", methods=["GET", "POST"])
def settings():
    """Notification toggles, name, and password for any logged-in user."""

    if "user_id" not in session:
        return redirect(url_for("login"))

    role_key = (session.get("role") or "").lower()
    toggles = [t for t in NOTIFICATION_SETTINGS if role_key in t[3]]
    user_id = session["user_id"]

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            if request.method == "POST":
                action = request.form.get("action")

                if action == "notifications":
                    columns = [column for column, _, _, _ in toggles]
                    values = [1 if request.form.get(column) else 0 for column in columns]

                    if columns:
                        # Column names come from NOTIFICATION_SETTINGS, not the form.
                        cursor.execute(
                            f"""
                            INSERT INTO User_Settings
                                (user_id, {", ".join(columns)})
                            VALUES
                                (%s, {", ".join(["%s"] * len(columns))})
                            ON DUPLICATE KEY UPDATE
                                {", ".join(f"{c} = VALUES({c})" for c in columns)}
                            """,
                            (user_id, *values)
                        )
                        conn.commit()

                    flash("Notification settings saved.", "success")

                elif action == "profile":
                    first_name = " ".join(request.form.get("first_name", "").split())[:100]
                    last_name = " ".join(request.form.get("last_name", "").split())[:100]

                    if not first_name or not last_name:
                        flash("Enter both a first and last name.", "error")
                    else:
                        cursor.execute(
                            "UPDATE Users SET first_name = %s, last_name = %s WHERE user_id = %s",
                            (first_name, last_name, user_id)
                        )
                        conn.commit()
                        queue_event("Account", "Account name updated", success=True,
                                    status="Saved")
                        session["first_name"] = first_name
                        session["last_name"] = last_name
                        flash("Your name was updated.", "success")

                elif action == "password":
                    current = request.form.get("current_password", "")
                    new_password = request.form.get("new_password", "")
                    confirm = request.form.get("confirm_password", "")

                    cursor.execute(
                        "SELECT password_hash FROM Password WHERE user_id = %s",
                        (user_id,)
                    )
                    record = cursor.fetchone()

                    if not record or not check_password_hash(record["password_hash"], current):
                        flash("Your current password is incorrect.", "error")
                    elif len(new_password) < MIN_PASSWORD_LENGTH:
                        flash(f"New password must be at least {MIN_PASSWORD_LENGTH} characters.", "error")
                    elif new_password != confirm:
                        flash("The new passwords do not match.", "error")
                    elif new_password == current:
                        flash("Your new password must be different from your current password.", "error")
                    else:
                        cursor.execute(
                            "UPDATE Password SET password_hash = %s WHERE user_id = %s",
                            (generate_password_hash(new_password, method="pbkdf2:sha256"), user_id)
                        )
                        # Reset links sent before the change stop working.
                        cursor.execute(
                            "UPDATE Password_Tokens SET used_at = NOW() WHERE user_id = %s AND used_at IS NULL",
                            (user_id,)
                        )
                        cursor.execute(
                            """
                            INSERT INTO Audit_Logs
                                (user_id, category, action, success)
                            VALUES
                                (%s, 'Password Change', 'Password changed in settings', 1)
                            """,
                            (user_id,)
                        )
                        conn.commit()
                        flash("Your password was changed.", "success")

                return redirect(url_for("settings") + "#" + (action or ""))

            cursor.execute(
                "SELECT * FROM User_Settings WHERE user_id = %s",
                (user_id,)
            )
            saved = cursor.fetchone() or {}

    finally:
        conn.close()

    return render_template(
        "shared/settings.html",
        toggles=[
            {"column": column, "label": label, "on": bool(saved.get(column, 1))}
            for column, _, label, _ in toggles
        ],
    )


@app.route("/notifications/read", methods=["POST"])
def mark_notifications_read():
    """Called by the navbar dropdown when it is opened."""

    if "user_id" not in session:
        return "", 401

    conn = get_db()

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE Notifications
                SET is_read = 1
                WHERE user_id = %s
                  AND is_read = 0
                """,
                (session["user_id"],)
            )
        conn.commit()
    finally:
        conn.close()

    return "", 204

# ==========================================
# SPONSOR ACCESS REQUESTS
#   1. /sponsor-request          - public form: name + email
#   2. emailed /sponsor-apply/<token> - organization details
#   3. /admin/sponsor-requests   - admin approves (creates the account) or rejects
# ==========================================

@app.route("/sponsor-request", methods=["GET", "POST"])
def sponsor_request():

    if "user_id" in session:
        return redirect(url_for("home"))

    if request.method == "GET":
        return render_template("shared/sponsor_request.html")

    first_name = request.form.get("first_name", "").strip()[:100]
    last_name = request.form.get("last_name", "").strip()[:100]
    email = request.form.get("email", "").strip().lower()[:255]

    form = {"first_name": first_name, "last_name": last_name, "email": email}

    if not first_name or not last_name or not email or "@" not in email:
        return render_template(
            "shared/sponsor_request.html",
            form=form,
            error="Please enter your first name, last name, and a valid email address."
        )

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                "SELECT user_id FROM Users WHERE LOWER(email) = %s",
                (email,)
            )

            if cursor.fetchone():
                return render_template(
                    "shared/sponsor_request.html",
                    form=form,
                    error="An account with this email already exists. Please log in instead."
                )

            # Reuse an open request instead of creating duplicates.
            cursor.execute(
                """
                SELECT request_id, token, status
                FROM Sponsor_Requests
                WHERE LOWER(email) = %s
                  AND status IN ('Started', 'Submitted')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (email,)
            )

            existing = cursor.fetchone()

            if existing and existing["status"] == "Submitted":
                return render_template(
                    "shared/sponsor_request.html",
                    under_review=True
                )

            if existing:
                token = existing["token"]
            else:
                token = secrets.token_urlsafe(32)

                cursor.execute(
                    """
                    INSERT INTO Sponsor_Requests
                        (email, first_name, last_name, token)
                    VALUES
                        (%s, %s, %s, %s)
                    """,
                    (email, first_name, last_name, token)
                )

                conn.commit()

    finally:
        conn.close()

    send_sponsor_request_link(email, first_name, token)

    return render_template(
        "shared/sponsor_request.html",
        sent_to=email
    )


@app.route("/sponsor-apply/<token>", methods=["GET", "POST"])
def sponsor_apply(token):

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    request_id, email, first_name, last_name, status,
                    request_type, target_sponsor_id, company_name
                FROM Sponsor_Requests
                WHERE token = %s
                """,
                (token,)
            )

            sponsor_req = cursor.fetchone()

            if not sponsor_req or sponsor_req["status"] != "Started":
                return render_template(
                    "shared/sponsor_apply.html",
                    sponsor_req=sponsor_req,
                    closed=True
                )

            if sponsor_req.get("request_type") == "Join":
                return accept_team_invite(conn, cursor, sponsor_req)

            if request.method == "GET":
                return render_template(
                    "shared/sponsor_apply.html",
                    sponsor_req=sponsor_req,
                    form={}
                )

            company_name = request.form.get("company_name", "").strip()[:255]
            phone = request.form.get("phone", "").strip()[:25]
            message = request.form.get("message", "").strip()[:1000]

            form = {"company_name": company_name, "phone": phone, "message": message}

            if not company_name:
                return render_template(
                    "shared/sponsor_apply.html",
                    sponsor_req=sponsor_req,
                    form=form,
                    error="Organization name is required."
                )

            cursor.execute(
                "SELECT sponsor_id FROM Sponsors WHERE LOWER(company_name) = %s",
                (company_name.lower(),)
            )

            if cursor.fetchone():
                return render_template(
                    "shared/sponsor_apply.html",
                    sponsor_req=sponsor_req,
                    form=form,
                    error=(
                        "An organization with this name already exists. "
                        "Ask a sponsor there to invite you from their Team page."
                    )
                )

            cursor.execute(
                """
                UPDATE Sponsor_Requests
                SET
                    request_type = 'New',
                    company_name = %s,
                    phone = %s,
                    message = %s,
                    status = 'Submitted',
                    submitted_at = NOW()
                WHERE request_id = %s
                  AND status = 'Started'
                """,
                (
                    company_name,
                    phone or None,
                    message or None,
                    sponsor_req["request_id"]
                )
            )

            conn.commit()
            queue_event("Sponsor Management", "Sponsor application submitted", success=True,
                        status="Submitted", details={"request_id": sponsor_req["request_id"]})

            # Let admins know. A failure here should never block the application.
            try:
                cursor.execute("SELECT user_id FROM Users WHERE role = 'Admin'")
                admins = cursor.fetchall()

                for admin in admins:
                    cursor.execute(
                        """
                        INSERT INTO Notifications
                            (user_id, notification_type, subject, message)
                        VALUES
                            (%s, 'New Application', %s, %s)
                        """,
                        (
                            admin["user_id"],
                            "New sponsor request",
                            f"{sponsor_req['first_name']} {sponsor_req['last_name']} "
                            f"requested a sponsor account for {company_name}."
                        )
                    )

                conn.commit()
            except Exception as e:
                conn.rollback()
                print(f"Failed to notify admins of sponsor request: {e}")

    finally:
        conn.close()

    return render_template(
        "shared/sponsor_apply.html",
        sponsor_req=sponsor_req,
        submitted=True
    )


def accept_team_invite(conn, cursor, sponsor_req):
    """
    An existing sponsor invited this person to their organization, so there is
    no admin review: they choose a password and the account is created.
    """

    if request.method == "GET":
        return render_template(
            "shared/sponsor_apply.html",
            sponsor_req=sponsor_req,
            form={},
            joining=True
        )

    first_name = request.form.get("first_name", "").strip()[:100]
    last_name = request.form.get("last_name", "").strip()[:100]
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    form = {"first_name": first_name, "last_name": last_name}

    error = None
    if not first_name or not last_name:
        error = "Please enter your first and last name."
    elif len(password) < 8:
        error = "Password must be at least 8 characters."
    elif password != confirm_password:
        error = "Passwords do not match."

    if not error:
        cursor.execute(
            "SELECT user_id FROM Users WHERE LOWER(email) = %s",
            (sponsor_req["email"].lower(),)
        )
        if cursor.fetchone():
            error = "An account with this email already exists. Please log in instead."

    if error:
        return render_template(
            "shared/sponsor_apply.html",
            sponsor_req=sponsor_req,
            form=form,
            joining=True,
            error=error
        )

    try:
        cursor.execute(
            """
            INSERT INTO Users
                (email, first_name, last_name, role)
            VALUES
                (%s, %s, %s, 'Sponsor')
            """,
            (sponsor_req["email"], first_name, last_name)
        )

        user_id = cursor.lastrowid

        cursor.execute(
            "INSERT INTO Password (user_id, password_hash) VALUES (%s, %s)",
            (user_id, generate_password_hash(password, method="pbkdf2:sha256"))
        )

        cursor.execute(
            "INSERT INTO Sponsor_Users (user_id, sponsor_id) VALUES (%s, %s)",
            (user_id, sponsor_req["target_sponsor_id"])
        )

        cursor.execute(
            """
            UPDATE Sponsor_Requests
            SET
                status = 'Approved',
                user_id = %s,
                decision_date = NOW()
            WHERE request_id = %s
            """,
            (user_id, sponsor_req["request_id"])
        )

        conn.commit()

    except Exception as e:
        conn.rollback()
        print(f"Failed to accept team invite {sponsor_req['request_id']}: {e}")
        return render_template(
            "shared/sponsor_apply.html",
            sponsor_req=sponsor_req,
            form=form,
            joining=True,
            error="Something went wrong creating your account. Please try again."
        )

    return render_template(
        "shared/sponsor_apply.html",
        sponsor_req=sponsor_req,
        joined=True
    )


@app.route("/sponsor/team", methods=["GET", "POST"])
def sponsor_team():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "sponsor":
        return redirect(url_for("home"))

    sponsor_id = current_sponsor_id()

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            if request.method == "POST":
                action = request.form.get("action")

                if action == "invite":
                    first_name = request.form.get("first_name", "").strip()[:100]
                    last_name = request.form.get("last_name", "").strip()[:100]
                    email = request.form.get("email", "").strip().lower()[:255]

                    if not first_name or not last_name or "@" not in email:
                        flash("Please enter a first name, last name, and valid email.", "error")
                        return redirect(url_for("sponsor_team"))

                    cursor.execute(
                        "SELECT user_id FROM Users WHERE LOWER(email) = %s",
                        (email,)
                    )
                    if cursor.fetchone():
                        flash(f"{email} already has an account.", "error")
                        return redirect(url_for("sponsor_team"))

                    cursor.execute(
                        """
                        SELECT request_id
                        FROM Sponsor_Requests
                        WHERE LOWER(email) = %s
                          AND status IN ('Started', 'Submitted')
                        """,
                        (email,)
                    )
                    if cursor.fetchone():
                        flash(f"{email} already has an open invite or application.", "error")
                        return redirect(url_for("sponsor_team"))

                    cursor.execute(
                        "SELECT company_name FROM Sponsors WHERE sponsor_id = %s",
                        (sponsor_id,)
                    )
                    sponsor = cursor.fetchone()
                    company_name = sponsor["company_name"] if sponsor else "your organization"

                    token = secrets.token_urlsafe(32)

                    # decision_by_user_id records who sent the invite.
                    cursor.execute(
                        """
                        INSERT INTO Sponsor_Requests
                            (email, first_name, last_name, token, request_type,
                             target_sponsor_id, company_name, decision_by_user_id)
                        VALUES
                            (%s, %s, %s, %s, 'Join', %s, %s, %s)
                        """,
                        (email, first_name, last_name, token,
                         sponsor_id, company_name, session["user_id"])
                    )

                    conn.commit()

                    inviter = f"{session.get('first_name', '')} {session.get('last_name', '')}".strip()
                    send_sponsor_team_invite(email, first_name, company_name, inviter, token)

                    flash(f"Invite sent to {email}.", "success")
                    return redirect(url_for("sponsor_team"))

                if action == "cancel":
                    cursor.execute(
                        """
                        UPDATE Sponsor_Requests
                        SET
                            status = 'Rejected',
                            reason = 'Invite cancelled',
                            decision_date = NOW()
                        WHERE request_id = %s
                          AND target_sponsor_id = %s
                          AND request_type = 'Join'
                          AND status = 'Started'
                        """,
                        (request.form.get("request_id"), sponsor_id)
                    )
                    conn.commit()

                    flash("Invite cancelled.", "success")
                    return redirect(url_for("sponsor_team"))

            cursor.execute(
                """
                SELECT
                    Users.user_id, Users.first_name, Users.last_name, Users.email,
                    Sponsor_Users.created_at
                FROM Users
                LEFT JOIN Sponsor_Users
                    ON Sponsor_Users.user_id = Users.user_id
                WHERE Users.role = 'Sponsor'
                  AND (Sponsor_Users.sponsor_id = %s OR Users.user_id = %s)
                ORDER BY Users.first_name, Users.last_name
                """,
                (sponsor_id, sponsor_id)
            )
            members = cursor.fetchall()

            cursor.execute(
                """
                SELECT request_id, email, first_name, last_name, created_at
                FROM Sponsor_Requests
                WHERE target_sponsor_id = %s
                  AND request_type = 'Join'
                  AND status = 'Started'
                ORDER BY created_at DESC
                """,
                (sponsor_id,)
            )
            invites = cursor.fetchall()

            cursor.execute(
                "SELECT company_name FROM Sponsors WHERE sponsor_id = %s",
                (sponsor_id,)
            )
            sponsor = cursor.fetchone()

    finally:
        conn.close()

    return render_template(
        "sponsor/team.html",
        first_name=session["first_name"],
        role=session["role"],
        members=members,
        invites=invites,
        company_name=sponsor["company_name"] if sponsor else None
    )


@app.route("/admin/sponsor-requests")
def admin_sponsor_requests():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "admin":
        return redirect(url_for("home"))

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    request_id, email, first_name, last_name,
                    request_type, company_name, phone, message, submitted_at
                FROM Sponsor_Requests
                WHERE status = 'Submitted'
                ORDER BY submitted_at ASC
                """
            )
            pending = cursor.fetchall()

            cursor.execute(
                """
                SELECT
                    request_id, email, first_name, last_name,
                    company_name, status, reason, decision_date
                FROM Sponsor_Requests
                WHERE status IN ('Approved', 'Rejected')
                  AND request_type = 'New'
                ORDER BY decision_date DESC
                LIMIT 20
                """
            )
            decided = cursor.fetchall()

    finally:
        conn.close()

    return render_template(
        "admin/sponsor_requests.html",
        first_name=session["first_name"],
        role=session["role"],
        pending=pending,
        decided=decided
    )


@app.route("/admin/sponsor-requests/<int:request_id>/approve", methods=["POST"])
def approve_sponsor_request(request_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "admin":
        return redirect(url_for("home"))

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                """
                SELECT request_id, email, first_name, last_name, company_name, phone
                FROM Sponsor_Requests
                WHERE request_id = %s
                  AND status = 'Submitted'
                FOR UPDATE
                """,
                (request_id,)
            )

            sponsor_req = cursor.fetchone()

            if not sponsor_req:
                flash("That request was not found or has already been handled.", "error")
                return redirect(url_for("admin_sponsor_requests"))

            cursor.execute(
                "SELECT user_id FROM Users WHERE LOWER(email) = %s",
                (sponsor_req["email"].lower(),)
            )

            if cursor.fetchone():
                flash(f"An account with {sponsor_req['email']} already exists.", "error")
                return redirect(url_for("admin_sponsor_requests"))

            cursor.execute(
                """
                INSERT INTO Users
                    (email, first_name, last_name, role)
                VALUES
                    (%s, %s, %s, 'Sponsor')
                """,
                (sponsor_req["email"], sponsor_req["first_name"], sponsor_req["last_name"])
            )

            user_id = cursor.lastrowid

            cursor.execute(
                "INSERT INTO Password (user_id, password_hash) VALUES (%s, %s)",
                (user_id, unusable_password_hash())
            )

            # Same shape as /register: the sponsor's org id is its first user's id.
            cursor.execute(
                """
                INSERT INTO Sponsors
                    (sponsor_id, company_name, email, phone)
                VALUES
                    (%s, %s, %s, %s)
                """,
                (user_id, sponsor_req["company_name"], sponsor_req["email"], sponsor_req["phone"])
            )

            cursor.execute(
                "INSERT INTO Sponsor_Users (user_id, sponsor_id) VALUES (%s, %s)",
                (user_id, user_id)
            )

            cursor.execute(
                """
                UPDATE Sponsor_Requests
                SET
                    status = 'Approved',
                    user_id = %s,
                    decision_date = NOW(),
                    decision_by_user_id = %s
                WHERE request_id = %s
                """,
                (user_id, session["user_id"], request_id)
            )

            setup_token = create_password_token(cursor, user_id, "Setup")

            conn.commit()

    except Exception as e:
        conn.rollback()
        print(f"Failed to approve sponsor request {request_id}: {e}")
        flash("Something went wrong creating that sponsor account.", "error")
        return redirect(url_for("admin_sponsor_requests"))

    finally:
        conn.close()

    queue_event("Sponsor Management", "Sponsor request approved", success=True,
                status="Approved", details={"request_id": request_id})
    send_sponsor_approved_email(
        sponsor_req["email"],
        sponsor_req["first_name"],
        sponsor_req["company_name"],
        setup_token
    )

    flash(
        f"Approved {sponsor_req['company_name']}. A password setup link was emailed to {sponsor_req['email']}.",
        "success"
    )
    return redirect(url_for("admin_sponsor_requests"))


@app.route("/admin/sponsor-requests/<int:request_id>/reject", methods=["POST"])
def reject_sponsor_request(request_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "admin":
        return redirect(url_for("home"))

    reason = request.form.get("reason", "").strip()[:1000]

    if not reason:
        flash("Please give a reason for rejecting the request.", "error")
        return redirect(url_for("admin_sponsor_requests"))

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                """
                SELECT email, first_name, company_name
                FROM Sponsor_Requests
                WHERE request_id = %s
                  AND status = 'Submitted'
                """,
                (request_id,)
            )

            sponsor_req = cursor.fetchone()

            if not sponsor_req:
                flash("That request was not found or has already been handled.", "error")
                return redirect(url_for("admin_sponsor_requests"))

            cursor.execute(
                """
                UPDATE Sponsor_Requests
                SET
                    status = 'Rejected',
                    reason = %s,
                    decision_date = NOW(),
                    decision_by_user_id = %s
                WHERE request_id = %s
                """,
                (reason, session["user_id"], request_id)
            )

            conn.commit()

    finally:
        conn.close()

    queue_event("Sponsor Management", "Sponsor request rejected", success=True,
                status="Rejected", details={"request_id": request_id})
    send_sponsor_rejected_email(sponsor_req["email"], sponsor_req["first_name"], reason)

    flash(f"Rejected the request for {sponsor_req['company_name']}.", "success")
    return redirect(url_for("admin_sponsor_requests"))


@app.route("/register", methods=["GET", "POST"])
def register():
    # Public sign-up is closed. Drivers join through a sponsor's emailed
    # invitation, sponsors request access and are approved by an admin,
    # and sponsor teammates are invited from the Team page.
    return redirect(url_for("sponsor_request"))

# Add these imports if they are not already present.
from flask import request, session, redirect, url_for, flash


def start_driver_view(driver, by, sponsor_id=None):
    """
    Switch the session to a driver so the usual driver pages work, keeping the
    original session to restore on exit. A sponsor is locked to its own
    program (sponsor_id); an admin can see every sponsor the driver has.
    """
    original_session = dict(session)

    session["role"] = "Driver"
    session["user_id"] = driver["user_id"]
    session["first_name"] = driver["first_name"]
    session["last_name"] = driver["last_name"] or ""
    session.pop("driver_sponsor_id", None)
    if sponsor_id is not None:
        session["driver_sponsor_id"] = sponsor_id

    # Support-mode markers for the banner and the read-only guard.
    session["_assume_driver_original"] = original_session
    session["assumed_driver_id"] = driver["user_id"]
    session["assumed_driver_name"] = (
        f"{driver['first_name']} {driver['last_name'] or ''}".strip()
    )
    session["assumed_driver_by"] = by
    if sponsor_id is not None:
        session["assumed_driver_sponsor_id"] = sponsor_id


@app.route("/sponsor/assume-driver/<int:driver_id>", methods=["POST"])
def sponsor_assume_driver(driver_id):
    """Temporarily view a driver account from a sponsor's perspective."""

    # Only authenticated sponsors may start this mode.
    if not session.get("user_id") or (session.get("role") or "").lower() != "sponsor":
        return "Forbidden", 403

    # Prevent nested assume-driver sessions.
    if session.get("assumed_driver_id"):
        return "You are already viewing a driver.", 400

    sponsor_id = current_sponsor_id()

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            # Only drivers in this sponsor's program, whatever the browser sent.
            cursor.execute(
                """
                SELECT
                    Users.user_id,
                    Users.first_name,
                    Users.last_name
                FROM Driver_Sponsors
                JOIN Users
                    ON Users.user_id = Driver_Sponsors.driver_id
                WHERE Driver_Sponsors.driver_id = %s
                  AND Driver_Sponsors.sponsor_id = %s
                """,
                (driver_id, sponsor_id),
            )
            driver = cursor.fetchone()

            if not driver:
                return "That driver is not associated with your sponsor.", 404

    finally:
        conn.close()

    start_driver_view(driver, by="sponsor", sponsor_id=sponsor_id)

    flash(
        f"Now viewing {session['assumed_driver_name']}'s account. "
        "You can browse the catalog and buy items for them; other changes are off.",
        "info",
    )
    return redirect(url_for("driver_dashboard"))


@app.route("/sponsor/exit-assumed-driver", methods=["POST"])
def sponsor_exit_assumed_driver():
    """Restore the sponsor's or admin's original session."""

    original_session = session.get("_assume_driver_original")

    if not original_session:
        return "No assumed-driver session is active.", 400

    # Restore the original identity and all of its session values.
    session.clear()
    session.update(original_session)
    session["last_activity"] = datetime.now(timezone.utc).isoformat()

    if (session.get("role") or "").lower() == "admin":
        flash("Returned to your admin account.", "success")
        return redirect(url_for("admin_drivers"))

    flash("You have returned to your sponsor account.", "success")
    return redirect(url_for("sponsor_drivers"))


@app.route("/admin/drivers")
def admin_drivers():
    """Every driver and the programs they belong to, for support."""
    if (
        not session.get("user_id")
        or (session.get("role") or "").lower() != "admin"
    ):
        abort(403)

    search = request.args.get("search", "").strip()[:100]
    search_pattern = f"%{' '.join(search.split())}%"

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    Users.user_id AS driver_id,
                    Users.first_name,
                    Users.last_name,
                    Users.email,
                    GROUP_CONCAT(
                        CONCAT(Sponsors.company_name, ' (', Driver_Sponsors.status, ')')
                        ORDER BY Sponsors.company_name SEPARATOR ', '
                    ) AS programs
                FROM Users
                LEFT JOIN Driver_Sponsors
                    ON Driver_Sponsors.driver_id = Users.user_id
                LEFT JOIN Sponsors
                    ON Sponsors.sponsor_id = Driver_Sponsors.sponsor_id
                WHERE Users.role = 'Driver'
                  AND (
                      %s = ''
                      OR Users.email LIKE %s
                      OR CONCAT_WS(' ', Users.first_name, Users.last_name) LIKE %s
                  )
                GROUP BY Users.user_id, Users.first_name, Users.last_name, Users.email
                ORDER BY Users.last_name, Users.first_name
                """,
                (search, search_pattern, search_pattern)
            )
            drivers = cursor.fetchall()

            cursor.execute(
                """
                SELECT sponsor_id, company_name
                FROM Sponsors
                WHERE is_active = TRUE
                ORDER BY company_name
                """
            )
            sponsors = cursor.fetchall()
    finally:
        conn.close()

    return render_template("admin/drivers.html", drivers=drivers, sponsors=sponsors, search=search)


@app.route("/admin/drivers/<int:driver_id>/assign-sponsor", methods=["POST"])
def admin_assign_driver_sponsor(driver_id):
    """Add a driver to a sponsor's program, or reactivate them in it."""
    if (
        not session.get("user_id")
        or (session.get("role") or "").lower() != "admin"
    ):
        abort(403)

    try:
        sponsor_id = int(request.form.get("sponsor_id", ""))
    except ValueError:
        flash("Choose a sponsor.", "error")
        return redirect(url_for("admin_drivers"))

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT first_name, last_name FROM Users WHERE user_id = %s AND role = 'Driver'",
                (driver_id,)
            )
            driver = cursor.fetchone()

            cursor.execute(
                "SELECT company_name FROM Sponsors WHERE sponsor_id = %s",
                (sponsor_id,)
            )
            sponsor = cursor.fetchone()

            if not driver or not sponsor:
                flash("That driver or sponsor was not found.", "error")
                return redirect(url_for("admin_drivers"))

            cursor.execute(
                "SELECT driver_id FROM Drivers WHERE driver_id = %s",
                (driver_id,)
            )
            if not cursor.fetchone():
                cursor.execute(
                    """
                    INSERT INTO Drivers
                        (driver_id, sponsor_id, status)
                    VALUES (%s, %s, 'Active')
                    """,
                    (driver_id, sponsor_id)
                )

            # Keeps their points if they were in this program before.
            cursor.execute(
                """
                INSERT INTO Driver_Sponsors
                    (driver_id, sponsor_id, status)
                VALUES (%s, %s, 'Active')
                ON DUPLICATE KEY UPDATE
                    status = 'Active'
                """,
                (driver_id, sponsor_id)
            )

            conn.commit()

    except Exception as e:
        conn.rollback()
        print(f"Failed to assign driver {driver_id} to sponsor {sponsor_id}: {e}")
        flash("Something went wrong assigning that driver.", "error")
        return redirect(url_for("admin_drivers"))

    finally:
        conn.close()

    queue_event("Driver Management", "Driver assigned to sponsor by admin", success=True,
                status="Active", driver_id=driver_id, sponsor_id=sponsor_id)
    flash(
        f"{driver['first_name']} {driver['last_name'] or ''} is now in {sponsor['company_name']}'s program.",
        "success"
    )
    return redirect(url_for("admin_drivers"))


@app.route("/admin/assume-driver/<int:driver_id>", methods=["POST"])
def admin_assume_driver(driver_id):
    """Look at the site as a driver, read-only, for support."""
    if (
        not session.get("user_id")
        or (session.get("role") or "").lower() != "admin"
    ):
        abort(403)

    # Do not allow nested account switching.
    if session.get("assumed_driver_id") or session.get("assumed_sponsor_id"):
        abort(400, description="Exit the current support view first.")

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT user_id, first_name, last_name
                FROM Users
                WHERE user_id = %s
                  AND role = 'Driver'
                """,
                (driver_id,)
            )
            driver = cursor.fetchone()
    finally:
        conn.close()

    if not driver:
        abort(404, description="Driver not found.")

    start_driver_view(driver, by="admin")

    flash(
        f"Now viewing {session['assumed_driver_name']}'s account in read-only support mode.",
        "info",
    )
    return redirect(url_for("driver_dashboard"))

@app.route("/admin/assume-sponsor/<int:sponsor_id>", methods=["POST"])
def admin_assume_sponsor(sponsor_id):
    if (
        not session.get("user_id")
        or (session.get("role") or "").lower() != "admin"
    ):
        abort(403)

    # Do not allow nested account switching.
    if session.get("assumed_driver_id") or session.get("assumed_sponsor_id"):
        abort(400, description="Exit the current support view first.")

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            # Verify the sponsor exists and find a user belonging to it.
            cursor.execute(
                """
                SELECT
                    s.sponsor_id,
                    s.company_name,
                    su.user_id,
                    u.first_name,
                    u.last_name
                FROM Sponsors AS s
                JOIN Sponsor_Users AS su
                    ON su.sponsor_id = s.sponsor_id
                JOIN Users AS u
                    ON u.user_id = su.user_id
                WHERE s.sponsor_id = %s
                ORDER BY su.user_id
                LIMIT 1
                """,
                (sponsor_id,),
            )
            sponsor = cursor.fetchone()
    finally:
        conn.close()

    if not sponsor:
        abort(404, description="Sponsor or associated sponsor user not found.")

    # Preserve the original admin session so it can be restored on exit.
    session["_assume_sponsor_original"] = dict(session)

    session["user_id"] = sponsor["user_id"]
    session["role"] = "sponsor"
    session["sponsor_id"] = sponsor["sponsor_id"]
    session["first_name"] = sponsor["first_name"]
    session["last_name"] = sponsor["last_name"] or ""

    # Marker used by the navbar and read-only protection.
    session["assumed_sponsor_id"] = sponsor["sponsor_id"]
    session["assumed_sponsor_name"] = sponsor["company_name"]

    flash(
        f"Now viewing {sponsor['company_name']} in read-only support mode.",
        "info",
    )
    return redirect(url_for("sponsor_dashboard"))


@app.route("/admin/exit-assumed-sponsor", methods=["POST"])
def admin_exit_assumed_sponsor():
    original = session.get("_assume_sponsor_original")

    if not original or not session.get("assumed_sponsor_id"):
        abort(400, description="No assumed-sponsor session is active.")

    session.clear()
    session.update(original)
    session["last_activity"] = datetime.now(timezone.utc).isoformat()

    flash("Returned to your admin account.", "success")
    return redirect(url_for("home"))

@app.route("/admin/invoices")
def admin_invoices():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "admin":
        abort(403)

    # Only allow admins in their own account
    if session.get("assumed_driver_id") or session.get("assumed_sponsor_id"):
        abort(403)

    conn = get_db()

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    Invoices.invoice_id,
                    Invoices.invoice_date,
                    Invoices.invoice_type,
                    Invoices.description,
                    Invoices.total_amount,
                    Invoices.status,
                    Sponsors.company_name,
                    Orders.order_number
                FROM Invoices
                JOIN Sponsors
                    ON Invoices.sponsor_id = Sponsors.sponsor_id
                LEFT JOIN Orders
                    ON Invoices.order_id = Orders.order_id
                ORDER BY Invoices.invoice_date DESC,
                         Invoices.invoice_id DESC
                """
            )

            invoices = cursor.fetchall()
            # Get sponsors for the manual invoice form
            cursor.execute(
                """
                SELECT sponsor_id, company_name
                FROM Sponsors
                ORDER BY company_name
                """
            )
            sponsors = cursor.fetchall()

            # Security token for the manual invoice form
            csrf_token = session.get("invoice_csrf")

            if not csrf_token:
                csrf_token = secrets.token_urlsafe(32)
                session["invoice_csrf"] = csrf_token


    finally:
        conn.close()

    return render_template(
        "admin/invoices.html",
        invoices=invoices,
        sponsors=sponsors,
        csrf_token=csrf_token
    )




@app.route("/admin/invoices/report")
def admin_invoice_report():

    # Check that the user is logged in
    if "user_id" not in session:
        return redirect(url_for("login"))

    # Only Admin users can access invoice reports
    if session.get("role", "").lower() != "admin":
        abort(403)

    # Prevent access while impersonating another user
    if (
        session.get("assumed_driver_id") is not None
        or session.get("assumed_sponsor_id") is not None
    ):
        abort(403)

    from datetime import datetime

    # Get selected sponsor and billing period
    sponsor_id = request.args.get("sponsor_id", "all")
    start_month = request.args.get("start_month", "")
    end_month = request.args.get("end_month", "")

    reports = []
    generated = False

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            # Get all sponsors for the dropdown
            cursor.execute("""
                SELECT sponsor_id, company_name
                FROM Sponsors
                ORDER BY company_name
            """)

            sponsors = cursor.fetchall()

            # Generate reports only when both months are selected
            if start_month and end_month:

                # Convert selected months into dates
                try:
                    start = datetime.strptime(start_month, "%Y-%m")
                    end = datetime.strptime(end_month, "%Y-%m")

                except ValueError:
                    flash("Please enter valid months.", "error")
                    return redirect(url_for("admin_invoice_report"))

                # Make sure the date range is valid
                if start > end:
                    flash("End month cannot be before start month.", "error")
                    return redirect(url_for("admin_invoice_report"))

                # Calculate the first day after the end month
                if end.month == 12:
                    end_exclusive = datetime(end.year + 1, 1, 1)
                else:
                    end_exclusive = datetime(end.year, end.month + 1, 1)

                # Start with all sponsors
                selected_sponsors = sponsors

                # Filter to one sponsor if selected
                if sponsor_id != "all":

                    selected_sponsors = [
                        sponsor for sponsor in sponsors
                        if str(sponsor["sponsor_id"]) == sponsor_id
                    ]

                    if not selected_sponsors:
                        abort(400, "Invalid sponsor.")

                # Generate a separate report for each sponsor
                for sponsor in selected_sponsors:

                    # ---------------------------------
                    # Driver Fees
                    # ---------------------------------
                    cursor.execute("""
                        SELECT
                            Users.first_name,
                            Users.last_name,
                            SUM(Invoices.total_amount) AS fee_due

                        FROM Invoices

                        JOIN Orders
                            ON Invoices.order_id = Orders.order_id

                        JOIN Users
                            ON Orders.driver_id = Users.user_id

                        WHERE Invoices.sponsor_id = %s
                            AND Invoices.status IN ('Pending', 'Paid')
                            AND Invoices.invoice_date >= %s
                            AND Invoices.invoice_date < %s

                        GROUP BY
                            Users.user_id,
                            Users.first_name,
                            Users.last_name

                        ORDER BY
                            Users.last_name,
                            Users.first_name
                    """, (
                        sponsor["sponsor_id"],
                        start,
                        end_exclusive
                    ))

                    drivers = cursor.fetchall()

                    # ---------------------------------
                    # Additional Manual Charges
                    # ---------------------------------
                    cursor.execute("""
                        SELECT
                            description,
                            total_amount

                        FROM Invoices

                        WHERE sponsor_id = %s
                            AND invoice_type = 'Manual'
                            AND order_id IS NULL
                            AND status IN ('Pending', 'Paid')
                            AND invoice_date >= %s
                            AND invoice_date < %s

                        ORDER BY invoice_date
                    """, (
                        sponsor["sponsor_id"],
                        start,
                        end_exclusive
                    ))

                    manual_charges = cursor.fetchall()

                    # ---------------------------------
                    # Sponsor Totals
                    # ---------------------------------
                    cursor.execute("""
                        SELECT
                            COALESCE(SUM(total_amount), 0)
                                AS total_charges,

                            COALESCE(SUM(
                                CASE
                                    WHEN status = 'Paid'
                                    THEN total_amount
                                    ELSE 0
                                END
                            ), 0) AS total_paid,

                            COALESCE(SUM(
                                CASE
                                    WHEN status = 'Pending'
                                    THEN total_amount
                                    ELSE 0
                                END
                            ), 0) AS total_pending

                        FROM Invoices

                        WHERE sponsor_id = %s
                            AND status IN ('Pending', 'Paid')
                            AND invoice_date >= %s
                            AND invoice_date < %s
                    """, (
                        sponsor["sponsor_id"],
                        start,
                        end_exclusive
                    ))

                    totals = cursor.fetchone()

                    # ---------------------------------
                    # Save Sponsor Report
                    # ---------------------------------
                    reports.append({
                        "sponsor": sponsor,
                        "drivers": drivers,
                        "manual_charges": manual_charges,
                        "total": totals["total_charges"],
                        "total_paid": totals["total_paid"],
                        "total_pending": totals["total_pending"]
                    })

                generated = True

    finally:
        conn.close()

    # Display invoice report page
    return render_template(
        "admin/invoice_report.html",
        sponsors=sponsors,
        reports=reports,
        generated=generated,
        selected_sponsor=sponsor_id,
        start_month=start_month,
        end_month=end_month
    )



@app.route("/reports/driver-points")
def driver_points_report():

    # Require login
    if "user_id" not in session:
        return redirect(url_for("login"))

    role = session.get("role", "").lower()

    # Only Admin and Sponsor users
    if role not in ("admin", "sponsor"):
        abort(403)

    # Prevent reports while viewing as another user
    if (
        session.get("assumed_driver_id") is not None
        or session.get("assumed_sponsor_id") is not None
    ):
        abort(403)

    # Get report filters
    driver_id = request.args.get("driver_id", "all")
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")

    transactions = []
    generated = False

    # Sponsors only see their own sponsor organization
    sponsor_id = current_sponsor_id() if role == "sponsor" else None

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            # Get drivers for the dropdown
            if role == "sponsor":
                cursor.execute("""
                    SELECT DISTINCT
                        Users.user_id,
                        Users.first_name,
                        Users.last_name
                    FROM Driver_Sponsors
                    JOIN Users
                        ON Driver_Sponsors.driver_id = Users.user_id
                    WHERE Driver_Sponsors.sponsor_id = %s
                    ORDER BY Users.last_name, Users.first_name
                """, (sponsor_id,))

            else:
                cursor.execute("""
                    SELECT
                        Users.user_id,
                        Users.first_name,
                        Users.last_name
                    FROM Drivers
                    JOIN Users
                        ON Drivers.driver_id = Users.user_id
                    ORDER BY Users.last_name, Users.first_name
                """)

            drivers = cursor.fetchall()

            # Generate report when dates are selected
            if start_date and end_date:

                from datetime import datetime, timedelta

                try:
                    start = datetime.strptime(start_date, "%Y-%m-%d")
                    end = datetime.strptime(end_date, "%Y-%m-%d")
                except ValueError:
                    abort(400, "Invalid date.")

                if start > end:
                    abort(400, "End date cannot be before start date.")

                end_exclusive = end + timedelta(days=1)

                # Validate selected driver
                if driver_id != "all":
                    if not any(
                        str(driver["user_id"]) == driver_id
                        for driver in drivers
                    ):
                        abort(400, "Invalid driver.")

                # Get point transactions and current balances
                query = """
                    SELECT
                        Users.user_id AS driver_id,
                        Users.first_name,
                        Users.last_name,
                        Sponsors.company_name,
                        Point_Transactions.point_change,
                        Point_Transactions.reason,
                        Point_Transactions.created_at,
                        Driver_Sponsors.points_balance

                    FROM Point_Transactions

                    JOIN Users
                        ON Point_Transactions.driver_id = Users.user_id

                    JOIN Sponsors
                        ON Point_Transactions.sponsor_id = Sponsors.sponsor_id

                    JOIN Driver_Sponsors
                        ON Point_Transactions.driver_id = Driver_Sponsors.driver_id
                        AND Point_Transactions.sponsor_id = Driver_Sponsors.sponsor_id

                    WHERE Point_Transactions.created_at >= %s
                        AND Point_Transactions.created_at < %s
                """

                params = [start, end_exclusive]

                # Restrict sponsor access
                if role == "sponsor":
                    query += """
                        AND Point_Transactions.sponsor_id = %s
                    """
                    params.append(sponsor_id)

                # Filter by driver
                if driver_id != "all":
                    query += """
                        AND Point_Transactions.driver_id = %s
                    """
                    params.append(int(driver_id))

                query += """
                    ORDER BY
                        Users.last_name,
                        Users.first_name,
                        Point_Transactions.created_at DESC,
                        Point_Transactions.transaction_id DESC
                """

                cursor.execute(query, params)
                transactions = cursor.fetchall()

                generated = True

    finally:
        conn.close()

    return render_template(
        "reports/driver_points.html",
        drivers=drivers,
        transactions=transactions,
        generated=generated,
        selected_driver=driver_id,
        start_date=start_date,
        end_date=end_date
    )



@app.route("/reports/driver-points/export")
def export_driver_points_report():

    # Require login
    if "user_id" not in session:
        return redirect(url_for("login"))

    role = session.get("role", "").lower()

    # Only Admin and Sponsor users
    if role not in ("admin", "sponsor"):
        abort(403)

    # Prevent access while impersonating another user
    if (
        session.get("assumed_driver_id") is not None
        or session.get("assumed_sponsor_id") is not None
    ):
        abort(403)

    import csv
    import io
    from datetime import datetime, timedelta
    from flask import Response

    # Get selected filters
    driver_id = request.args.get("driver_id", "all")
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")

    # Require both dates
    if not start_date or not end_date:
        abort(400, "Please select a start and end date.")

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        abort(400, "Invalid date.")

    if start > end:
        abort(400, "End date cannot be before start date.")

    end_exclusive = end + timedelta(days=1)

    # Restrict sponsor users to their organization
    sponsor_id = current_sponsor_id() if role == "sponsor" else None

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            query = """
                SELECT
                    Users.first_name,
                    Users.last_name,
                    Driver_Sponsors.points_balance,
                    Point_Transactions.point_change,
                    Point_Transactions.created_at,
                    Sponsors.company_name,
                    Point_Transactions.reason

                FROM Point_Transactions

                JOIN Users
                    ON Point_Transactions.driver_id = Users.user_id

                JOIN Sponsors
                    ON Point_Transactions.sponsor_id = Sponsors.sponsor_id

                JOIN Driver_Sponsors
                    ON Point_Transactions.driver_id = Driver_Sponsors.driver_id
                    AND Point_Transactions.sponsor_id = Driver_Sponsors.sponsor_id

                WHERE Point_Transactions.created_at >= %s
                    AND Point_Transactions.created_at < %s
            """

            params = [start, end_exclusive]

            # Sponsor users only see their own transactions
            if role == "sponsor":
                query += """
                    AND Point_Transactions.sponsor_id = %s
                """
                params.append(sponsor_id)

            # Optional driver filter
            if driver_id != "all":

                try:
                    selected_driver_id = int(driver_id)
                except ValueError:
                    abort(400, "Invalid driver.")

                # Confirm sponsor has access to selected driver
                if role == "sponsor":
                    cursor.execute("""
                        SELECT 1
                        FROM Driver_Sponsors
                        WHERE driver_id = %s
                            AND sponsor_id = %s
                    """, (selected_driver_id, sponsor_id))

                    if cursor.fetchone() is None:
                        abort(403)

                query += """
                    AND Point_Transactions.driver_id = %s
                """
                params.append(selected_driver_id)

            query += """
                ORDER BY
                    Users.last_name,
                    Users.first_name,
                    Point_Transactions.created_at DESC,
                    Point_Transactions.transaction_id DESC
            """

            cursor.execute(query, params)
            transactions = cursor.fetchall()

    finally:
        conn.close()

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)

    # Column headings
    writer.writerow([
        "Driver",
        "Current Balance",
        "Point Change",
        "Date",
        "Sponsor",
        "Reason"
    ])

    # Transaction rows
    for transaction in transactions:
        writer.writerow([
            transaction["first_name"] + " " + transaction["last_name"],
            transaction["points_balance"],
            transaction["point_change"],
            transaction["created_at"],
            transaction["company_name"],
            transaction["reason"]
        ])

    # Return downloadable CSV file
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
                "attachment; filename=driver_point_tracking.csv"
        }
    )



@app.route("/reports/sales-by-sponsor")
def sales_by_sponsor_report():

    # Require login
    if "user_id" not in session:
        return redirect(url_for("login"))

    # Admin only
    if session.get("role", "").lower() != "admin":
        abort(403)

    # Prevent access while impersonating another user
    if (
        session.get("assumed_driver_id") is not None
        or session.get("assumed_sponsor_id") is not None
    ):
        abort(403)

    from datetime import datetime, timedelta

    # Get report filters
    sponsor_id = request.args.get("sponsor_id", "all")
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    report_type = request.args.get("report_type", "summary")

    reports = []
    generated = False

    # Only allow summary or detail
    if report_type not in ("summary", "detail"):
        abort(400, "Invalid report type.")

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            # Get sponsors for dropdown
            cursor.execute("""
                SELECT sponsor_id, company_name
                FROM Sponsors
                ORDER BY company_name
            """)

            sponsors = cursor.fetchall()

            # Generate report when dates are provided
            if start_date and end_date:

                try:
                    start = datetime.strptime(start_date, "%Y-%m-%d")
                    end = datetime.strptime(end_date, "%Y-%m-%d")
                except ValueError:
                    abort(400, "Invalid date.")

                if start > end:
                    abort(400, "End date cannot be before start date.")

                end_exclusive = end + timedelta(days=1)

                # Validate sponsor selection
                if sponsor_id != "all":
                    if not any(
                        str(sponsor["sponsor_id"]) == sponsor_id
                        for sponsor in sponsors
                    ):
                        abort(400, "Invalid sponsor.")

                # Summary Report
                if report_type == "summary":

                    query = """
                        SELECT
                            Sponsors.company_name,
                            COUNT(Orders.order_id) AS total_orders,
                            COALESCE(SUM(Orders.total_points), 0) AS total_points,
                            COALESCE(SUM(Orders.total_dollars), 0) AS total_sales

                        FROM Orders

                        JOIN Sponsors
                            ON Orders.sponsor_id = Sponsors.sponsor_id

                        WHERE Orders.order_date >= %s
                            AND Orders.order_date < %s
                            AND Orders.status <> 'Cancelled'
                    """

                    params = [start, end_exclusive]

                    if sponsor_id != "all":
                        query += """
                            AND Orders.sponsor_id = %s
                        """
                        params.append(int(sponsor_id))

                    query += """
                        GROUP BY
                            Sponsors.sponsor_id,
                            Sponsors.company_name

                        ORDER BY Sponsors.company_name
                    """

                # Detail Report
                else:

                    query = """
                        SELECT
                            Orders.order_number,
                            Orders.order_date,
                            Sponsors.company_name,
                            Users.first_name,
                            Users.last_name,
                            Orders.total_points,
                            Orders.total_dollars,
                            Orders.status

                        FROM Orders

                        JOIN Sponsors
                            ON Orders.sponsor_id = Sponsors.sponsor_id

                        JOIN Users
                            ON Orders.driver_id = Users.user_id

                        WHERE Orders.order_date >= %s
                            AND Orders.order_date < %s
                            AND Orders.status <> 'Cancelled'
                    """

                    params = [start, end_exclusive]

                    if sponsor_id != "all":
                        query += """
                            AND Orders.sponsor_id = %s
                        """
                        params.append(int(sponsor_id))

                    query += """
                        ORDER BY
                            Sponsors.company_name,
                            Orders.order_date DESC
                    """

                cursor.execute(query, params)
                reports = cursor.fetchall()
                generated = True

    finally:
        conn.close()

    return render_template(
        "reports/sales_by_sponsor.html",
        sponsors=sponsors,
        reports=reports,
        generated=generated,
        selected_sponsor=sponsor_id,
        start_date=start_date,
        end_date=end_date,
        report_type=report_type
    )



@app.route("/reports/sales-by-sponsor/export")
def export_sales_by_sponsor_report():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "admin":
        abort(403)

    if (
        session.get("assumed_driver_id") is not None
        or session.get("assumed_sponsor_id") is not None
    ):
        abort(403)

    import csv
    import io
    from datetime import datetime, timedelta
    from flask import Response

    sponsor_id = request.args.get("sponsor_id", "all")
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    report_type = request.args.get("report_type", "summary")

    if report_type not in ("summary", "detail"):
        abort(400, "Invalid report type.")

    if not start_date or not end_date:
        abort(400, "Please select a start and end date.")

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        abort(400, "Invalid date.")

    if start > end:
        abort(400, "End date cannot be before start date.")

    end_exclusive = end + timedelta(days=1)

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            if sponsor_id != "all":
                try:
                    selected_sponsor_id = int(sponsor_id)
                except ValueError:
                    abort(400, "Invalid sponsor.")

                cursor.execute("""
                    SELECT 1
                    FROM Sponsors
                    WHERE sponsor_id = %s
                """, (selected_sponsor_id,))

                if cursor.fetchone() is None:
                    abort(400, "Invalid sponsor.")

            if report_type == "summary":

                query = """
                    SELECT
                        Sponsors.company_name,
                        COUNT(Orders.order_id) AS total_orders,
                        COALESCE(SUM(Orders.total_points), 0) AS total_points,
                        COALESCE(SUM(Orders.total_dollars), 0) AS total_sales
                    FROM Orders
                    JOIN Sponsors
                        ON Orders.sponsor_id = Sponsors.sponsor_id
                    WHERE Orders.order_date >= %s
                        AND Orders.order_date < %s
                        AND Orders.status <> 'Cancelled'
                """

            else:

                query = """
                    SELECT
                        Orders.order_number,
                        Orders.order_date,
                        Sponsors.company_name,
                        Users.first_name,
                        Users.last_name,
                        Orders.total_points,
                        Orders.total_dollars,
                        Orders.status
                    FROM Orders
                    JOIN Sponsors
                        ON Orders.sponsor_id = Sponsors.sponsor_id
                    JOIN Users
                        ON Orders.driver_id = Users.user_id
                    WHERE Orders.order_date >= %s
                        AND Orders.order_date < %s
                        AND Orders.status <> 'Cancelled'
                """

            params = [start, end_exclusive]

            if sponsor_id != "all":
                query += " AND Orders.sponsor_id = %s"
                params.append(selected_sponsor_id)

            if report_type == "summary":
                query += """
                    GROUP BY
                        Sponsors.sponsor_id,
                        Sponsors.company_name
                    ORDER BY Sponsors.company_name
                """
            else:
                query += """
                    ORDER BY
                        Sponsors.company_name,
                        Orders.order_date DESC
                """

            cursor.execute(query, params)
            reports = cursor.fetchall()

    finally:
        conn.close()

    output = io.StringIO()
    writer = csv.writer(output)

    if report_type == "summary":

        writer.writerow([
            "Sponsor",
            "Total Orders",
            "Total Points Spent",
            "Total Sales ($)"
        ])

        for report in reports:
            writer.writerow([
                report["company_name"],
                report["total_orders"],
                report["total_points"],
                report["total_sales"]
            ])

    else:

        writer.writerow([
            "Order Number",
            "Date",
            "Sponsor",
            "Driver",
            "Points Spent",
            "Sales ($)",
            "Status"
        ])

        for report in reports:
            writer.writerow([
                report["order_number"],
                report["order_date"],
                report["company_name"],
                report["first_name"] + " " + report["last_name"],
                report["total_points"],
                report["total_dollars"],
                report["status"]
            ])

    filename = f"sales_by_sponsor_{report_type}.csv"

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


@app.route("/reports/sales-by-driver")
def sales_by_driver_report():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "admin":
        abort(403)

    if (
        session.get("assumed_driver_id") is not None
        or session.get("assumed_sponsor_id") is not None
    ):
        abort(403)

    from datetime import datetime, timedelta

    sponsor_id = request.args.get("sponsor_id", "all")
    driver_id = request.args.get("driver_id", "all")
    report_type = request.args.get("report_type", "summary")
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")

    if report_type not in ("summary", "detail"):
        abort(400, "Invalid report type.")

    reports = []
    generated = False

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            # Sponsors for the dropdown
            cursor.execute("""
                SELECT sponsor_id, company_name
                FROM Sponsors
                ORDER BY company_name
            """)
            sponsors = cursor.fetchall()

            # Drivers for the dropdown
            cursor.execute("""
                SELECT DISTINCT
                    Users.user_id,
                    Users.first_name,
                    Users.last_name
                FROM Users
                JOIN Orders
                    ON Users.user_id = Orders.driver_id
                ORDER BY Users.last_name, Users.first_name
            """)
            drivers = cursor.fetchall()

            # Generate the report when dates are selected
            if start_date and end_date:

                try:
                    start = datetime.strptime(start_date, "%Y-%m-%d")
                    end = datetime.strptime(end_date, "%Y-%m-%d")
                except ValueError:
                    abort(400, "Invalid date.")

                if start > end:
                    abort(400, "End date cannot be before start date.")

                end_exclusive = end + timedelta(days=1)

                if sponsor_id != "all":
                    if not any(
                        str(s["sponsor_id"]) == sponsor_id
                        for s in sponsors
                    ):
                        abort(400, "Invalid sponsor.")

                if driver_id != "all":
                    if not any(
                        str(d["user_id"]) == driver_id
                        for d in drivers
                    ):
                        abort(400, "Invalid driver.")

                if report_type == "summary":

                    query = """
                        SELECT
                            Users.first_name,
                            Users.last_name,
                            Sponsors.company_name,
                            COUNT(Orders.order_id) AS total_orders,
                            SUM(Orders.total_points) AS total_points,
                            SUM(Orders.total_dollars) AS total_sales
                        FROM Orders
                        JOIN Users
                            ON Orders.driver_id = Users.user_id
                        JOIN Sponsors
                            ON Orders.sponsor_id = Sponsors.sponsor_id
                        WHERE Orders.order_date >= %s
                            AND Orders.order_date < %s
                            AND Orders.status <> 'Cancelled'
                    """

                else:

                    query = """
                        SELECT
                            Users.first_name,
                            Users.last_name,
                            Sponsors.company_name,
                            Orders.order_number,
                            Orders.order_date,
                            Orders.total_points,
                            Orders.total_dollars,
                            Orders.status
                        FROM Orders
                        JOIN Users
                            ON Orders.driver_id = Users.user_id
                        JOIN Sponsors
                            ON Orders.sponsor_id = Sponsors.sponsor_id
                        WHERE Orders.order_date >= %s
                            AND Orders.order_date < %s
                            AND Orders.status <> 'Cancelled'
                    """

                params = [start, end_exclusive]

                # Optional sponsor filter
                if sponsor_id != "all":
                    query += " AND Orders.sponsor_id = %s"
                    params.append(int(sponsor_id))

                # Optional driver filter
                if driver_id != "all":
                    query += " AND Orders.driver_id = %s"
                    params.append(int(driver_id))

                if report_type == "summary":
                    query += """
                        GROUP BY
                            Users.user_id,
                            Users.first_name,
                            Users.last_name,
                            Sponsors.sponsor_id,
                            Sponsors.company_name
                        ORDER BY
                            Users.last_name,
                            Users.first_name,
                            Sponsors.company_name
                    """
                else:
                    query += """
                        ORDER BY
                            Users.last_name,
                            Users.first_name,
                            Orders.order_date DESC
                    """

                cursor.execute(query, params)
                reports = cursor.fetchall()
                generated = True

    finally:
        conn.close()

    return render_template(
        "reports/sales_by_driver.html",
        sponsors=sponsors,
        drivers=drivers,
        reports=reports,
        generated=generated,
        selected_sponsor=sponsor_id,
        selected_driver=driver_id,
        report_type=report_type,
        start_date=start_date,
        end_date=end_date
    )



@app.route("/reports/sales-by-driver/export")
def export_sales_by_driver_report():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "admin":
        abort(403)

    if (
        session.get("assumed_driver_id") is not None
        or session.get("assumed_sponsor_id") is not None
    ):
        abort(403)

    import csv
    import io
    from datetime import datetime, timedelta
    from flask import Response

    sponsor_id = request.args.get("sponsor_id", "all")
    driver_id = request.args.get("driver_id", "all")
    report_type = request.args.get("report_type", "summary")
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")

    if report_type not in ("summary", "detail"):
        abort(400, "Invalid report type.")

    if not start_date or not end_date:
        abort(400, "Please select a start and end date.")

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        abort(400, "Invalid date.")

    if start > end:
        abort(400, "End date cannot be before start date.")

    end_exclusive = end + timedelta(days=1)

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            # Validate sponsor selection
            if sponsor_id != "all":
                try:
                    sponsor_id = int(sponsor_id)
                except ValueError:
                    abort(400, "Invalid sponsor.")

                cursor.execute("""
                    SELECT sponsor_id
                    FROM Sponsors
                    WHERE sponsor_id = %s
                """, (sponsor_id,))

                if cursor.fetchone() is None:
                    abort(400, "Invalid sponsor.")

            # Validate driver selection
            if driver_id != "all":
                try:
                    driver_id = int(driver_id)
                except ValueError:
                    abort(400, "Invalid driver.")

                cursor.execute("""
                    SELECT DISTINCT Users.user_id
                    FROM Users
                    JOIN Orders
                        ON Users.user_id = Orders.driver_id
                    WHERE Users.user_id = %s
                """, (driver_id,))

                if cursor.fetchone() is None:
                    abort(400, "Invalid driver.")

            if report_type == "summary":

                query = """
                    SELECT
                        Users.first_name,
                        Users.last_name,
                        Sponsors.company_name,
                        COUNT(Orders.order_id) AS total_orders,
                        SUM(Orders.total_points) AS total_points,
                        SUM(Orders.total_dollars) AS total_sales
                    FROM Orders
                    JOIN Users
                        ON Orders.driver_id = Users.user_id
                    JOIN Sponsors
                        ON Orders.sponsor_id = Sponsors.sponsor_id
                    WHERE Orders.order_date >= %s
                        AND Orders.order_date < %s
                        AND Orders.status <> 'Cancelled'
                """

            else:

                query = """
                    SELECT
                        Users.first_name,
                        Users.last_name,
                        Sponsors.company_name,
                        Orders.order_number,
                        Orders.order_date,
                        Orders.total_points,
                        Orders.total_dollars,
                        Orders.status
                    FROM Orders
                    JOIN Users
                        ON Orders.driver_id = Users.user_id
                    JOIN Sponsors
                        ON Orders.sponsor_id = Sponsors.sponsor_id
                    WHERE Orders.order_date >= %s
                        AND Orders.order_date < %s
                        AND Orders.status <> 'Cancelled'
                """

            params = [start, end_exclusive]

            # Sponsor filter
            if sponsor_id != "all":
                query += " AND Orders.sponsor_id = %s"
                params.append(sponsor_id)

            # Driver filter
            if driver_id != "all":
                query += " AND Orders.driver_id = %s"
                params.append(driver_id)

            if report_type == "summary":
                query += """
                    GROUP BY
                        Users.user_id,
                        Users.first_name,
                        Users.last_name,
                        Sponsors.sponsor_id,
                        Sponsors.company_name
                    ORDER BY
                        Users.last_name,
                        Users.first_name,
                        Sponsors.company_name
                """
            else:
                query += """
                    ORDER BY
                        Users.last_name,
                        Users.first_name,
                        Orders.order_date DESC
                """

            cursor.execute(query, params)
            reports = cursor.fetchall()

    finally:
        conn.close()

    output = io.StringIO()
    writer = csv.writer(output)

    if report_type == "summary":

        writer.writerow([
            "Driver",
            "Sponsor",
            "Total Orders",
            "Total Points Spent",
            "Total Sales ($)"
        ])

        for report in reports:
            writer.writerow([
                report["first_name"] + " " + report["last_name"],
                report["company_name"],
                report["total_orders"],
                report["total_points"],
                report["total_sales"]
            ])

    else:

        writer.writerow([
            "Driver",
            "Sponsor",
            "Order Number",
            "Date",
            "Points Spent",
            "Sales ($)",
            "Status"
        ])

        for report in reports:
            writer.writerow([
                report["first_name"] + " " + report["last_name"],
                report["company_name"],
                report["order_number"],
                report["order_date"],
                report["total_points"],
                report["total_dollars"],
                report["status"]
            ])

    filename = f"sales_by_driver_{report_type}.csv"

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )



@app.route("/reports/audit-logs")
def audit_log_report():

    if "user_id" not in session:
        return redirect(url_for("login"))

    role = session.get("role", "").lower()

    if role not in ("admin", "sponsor"):
        abort(403)

    if (
        session.get("assumed_driver_id") is not None
        or session.get("assumed_sponsor_id") is not None
    ):
        abort(403)

    from datetime import datetime, timedelta

    sponsor_id = request.args.get("sponsor_id", "all")
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")

    reports = []
    generated = False
    sponsors = []

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            # Admin can select any sponsor
            if role == "admin":

                cursor.execute("""
                    SELECT sponsor_id, company_name
                    FROM Sponsors
                    ORDER BY company_name
                """)

                sponsors = cursor.fetchall()

                if sponsor_id != "all":
                    if not any(
                        str(s["sponsor_id"]) == sponsor_id
                        for s in sponsors
                    ):
                        abort(400, "Invalid sponsor.")

            else:
                # Sponsor can only view their own organization.
                # Resolve membership from the database.
                cursor.execute("""
                    SELECT sponsor_id
                    FROM Sponsor_Users
                    WHERE user_id = %s
                """, (session["user_id"],))

                sponsor_memberships = cursor.fetchall()

                if not sponsor_memberships:
                    cursor.execute("""
                        SELECT sponsor_id
                        FROM Sponsors
                        WHERE sponsor_id = %s
                    """, (session["user_id"],))

                    sponsor_memberships = cursor.fetchall()

                allowed_ids = {
                    s["sponsor_id"] for s in sponsor_memberships
                }

                active_sponsor = current_sponsor_id()

                if active_sponsor not in allowed_ids:
                    abort(403)

                sponsor_id = active_sponsor

            # Generate report when both dates are provided
            if start_date and end_date:

                try:
                    start = datetime.strptime(start_date, "%Y-%m-%d")
                    end = datetime.strptime(end_date, "%Y-%m-%d")
                except ValueError:
                    abort(400, "Invalid date.")

                if start > end:
                    abort(400, "End date cannot be before start date.")

                end_exclusive = end + timedelta(days=1)

                query = """
                    SELECT
                        Audit_Logs.log_id,
                        Audit_Logs.timestamp,
                        Audit_Logs.username,
                        Sponsors.company_name,
                        Audit_Logs.category,
                        Audit_Logs.action,
                        Audit_Logs.status,
                        Audit_Logs.reason,
                        Audit_Logs.success,
                        Audit_Logs.details
                    FROM Audit_Logs
                    LEFT JOIN Sponsors
                        ON Audit_Logs.sponsor_id = Sponsors.sponsor_id
                    WHERE Audit_Logs.timestamp >= %s
                        AND Audit_Logs.timestamp < %s
                """

                params = [start, end_exclusive]

                if sponsor_id != "all":
                    query += " AND Audit_Logs.sponsor_id = %s"
                    params.append(sponsor_id)

                query += """
                    ORDER BY
                        Audit_Logs.timestamp DESC,
                        Audit_Logs.log_id DESC
                """

                cursor.execute(query, params)
                reports = cursor.fetchall()
                generated = True

    finally:
        conn.close()

    return render_template(
        "reports/audit_logs.html",
        reports=reports,
        sponsors=sponsors,
        generated=generated,
        role=role,
        selected_sponsor=str(sponsor_id),
        start_date=start_date,
        end_date=end_date
    )



@app.route("/reports/audit-logs/export")
def export_audit_log_report():

    if "user_id" not in session:
        return redirect(url_for("login"))

    role = session.get("role", "").lower()

    if role not in ("admin", "sponsor"):
        abort(403)

    if (
        session.get("assumed_driver_id") is not None
        or session.get("assumed_sponsor_id") is not None
    ):
        abort(403)

    import csv
    import io
    from datetime import datetime, timedelta

    sponsor_id = request.args.get("sponsor_id", "all")
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")

    if not start_date or not end_date:
        abort(400, "Please select a date range.")

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        abort(400, "Invalid date.")

    if start > end:
        abort(400, "End date cannot be before start date.")

    end_exclusive = end + timedelta(days=1)

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            if role == "admin":

                if sponsor_id != "all":
                    cursor.execute("""
                        SELECT sponsor_id
                        FROM Sponsors
                        WHERE sponsor_id = %s
                    """, (sponsor_id,))

                    if not cursor.fetchone():
                        abort(400, "Invalid sponsor.")

            else:

                cursor.execute("""
                    SELECT sponsor_id
                    FROM Sponsor_Users
                    WHERE user_id = %s
                """, (session["user_id"],))

                memberships = cursor.fetchall()

                if not memberships:
                    cursor.execute("""
                        SELECT sponsor_id
                        FROM Sponsors
                        WHERE sponsor_id = %s
                    """, (session["user_id"],))

                    memberships = cursor.fetchall()

                allowed_ids = {
                    s["sponsor_id"] for s in memberships
                }

                active_sponsor = current_sponsor_id()

                if active_sponsor not in allowed_ids:
                    abort(403)

                sponsor_id = active_sponsor

            query = """
                SELECT
                    Audit_Logs.timestamp,
                    Audit_Logs.username,
                    Sponsors.company_name,
                    Audit_Logs.category,
                    Audit_Logs.action,
                    Audit_Logs.status,
                    Audit_Logs.reason,
                    Audit_Logs.success,
                    Audit_Logs.details
                FROM Audit_Logs
                LEFT JOIN Sponsors
                    ON Audit_Logs.sponsor_id = Sponsors.sponsor_id
                WHERE Audit_Logs.timestamp >= %s
                    AND Audit_Logs.timestamp < %s
            """

            params = [start, end_exclusive]

            if sponsor_id != "all":
                query += " AND Audit_Logs.sponsor_id = %s"
                params.append(sponsor_id)

            query += """
                ORDER BY
                    Audit_Logs.timestamp DESC,
                    Audit_Logs.log_id DESC
            """

            cursor.execute(query, params)
            reports = cursor.fetchall()

    finally:
        conn.close()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Date",
        "Username",
        "Sponsor",
        "Category",
        "Action",
        "Status",
        "Reason",
        "Success",
        "Details"
    ])

    for report in reports:

        writer.writerow([
            report["timestamp"],
            report["username"],
            report["company_name"],
            report["category"],
            report["action"],
            report["status"],
            report["reason"],
            report["success"],
            report["details"]
        ])

    filename = f"audit_log_report_{start_date}_to_{end_date}.csv"

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )




@app.route("/admin/reports")
def admin_reports():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "admin":
        abort(403)

    if (
        session.get("assumed_driver_id") is not None
        or session.get("assumed_sponsor_id") is not None
    ):
        abort(403)

    return render_template(
        "reports/dashboard.html",
        role="admin"
    )


@app.route("/sponsor/reports")
def sponsor_reports():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "sponsor":
        abort(403)

    if (
        session.get("assumed_driver_id") is not None
        or session.get("assumed_sponsor_id") is not None
    ):
        abort(403)

    return render_template(
        "reports/dashboard.html",
        role="sponsor"
    )


@app.route("/admin/invoices/export")
def admin_export_invoices():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "admin":
        abort(403)

    if (
        session.get("assumed_driver_id") is not None
        or session.get("assumed_sponsor_id") is not None
    ):
        abort(403)

    conn = get_db()

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    Invoices.invoice_id,
                    Invoices.invoice_date,
                    Sponsors.company_name,
                    Invoices.invoice_type,
                    Invoices.description,
                    Orders.order_number,
                    Invoices.total_amount,
                    Invoices.status
                FROM Invoices
                JOIN Sponsors
                    ON Invoices.sponsor_id = Sponsors.sponsor_id
                LEFT JOIN Orders
                    ON Invoices.order_id = Orders.order_id
                ORDER BY Invoices.invoice_id DESC
                """
            )

            invoices = cursor.fetchall()

    finally:
        conn.close()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Invoice Number",
        "Date",
        "Sponsor",
        "Type",
        "Description",
        "Order Number",
        "Amount",
        "Status"
    ])

    # Prevent spreadsheet programs from interpreting text as formulas
    def safe_csv_text(value):
        value = str(value or "")
        if value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return value

    for invoice in invoices:
        writer.writerow([
            invoice["invoice_id"],
            invoice["invoice_date"],
            safe_csv_text(invoice["company_name"]),
            invoice["invoice_type"],
            safe_csv_text(invoice["description"]),
            safe_csv_text(invoice["order_number"]),
            invoice["total_amount"],
            invoice["status"]
        ])

    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=invoices.csv"
        }
    )


@app.route("/admin/invoices/<int:invoice_id>/export")
def admin_export_invoice(invoice_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "admin":
        abort(403)

    if (
        session.get("assumed_driver_id") is not None
        or session.get("assumed_sponsor_id") is not None
    ):
        abort(403)

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    Invoices.invoice_id,
                    Invoices.invoice_date,
                    Sponsors.company_name,
                    Invoices.invoice_type,
                    Invoices.description,
                    Orders.order_number,
                    Invoices.total_amount,
                    Invoices.status
                FROM Invoices
                JOIN Sponsors
                    ON Invoices.sponsor_id = Sponsors.sponsor_id
                LEFT JOIN Orders
                    ON Invoices.order_id = Orders.order_id
                WHERE Invoices.invoice_id = %s
                """,
                (invoice_id,)
            )

            invoice = cursor.fetchone()

            if not invoice:
                abort(404)

            cursor.execute(
                """
                SELECT description, quantity, unit_cost, line_total
                FROM Invoice_Items
                WHERE invoice_id = %s
                ORDER BY invoice_item_id
                """,
                (invoice_id,)
            )

            items = cursor.fetchall()

    finally:
        conn.close()

    output = io.StringIO()
    writer = csv.writer(output)

    def safe_csv_text(value):
        value = str(value or "")
        if value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return value

    writer.writerow(["Invoice Number", invoice["invoice_id"]])
    writer.writerow(["Date", invoice["invoice_date"]])
    writer.writerow(["Sponsor", safe_csv_text(invoice["company_name"])])
    writer.writerow(["Type", invoice["invoice_type"]])
    writer.writerow(["Description", safe_csv_text(invoice["description"])])
    writer.writerow(["Order Number", safe_csv_text(invoice["order_number"])])
    writer.writerow(["Status", invoice["status"]])
    writer.writerow([])

    writer.writerow([
        "Item Description",
        "Quantity",
        "Unit Cost",
        "Line Total"
    ])

    for item in items:
        writer.writerow([
            safe_csv_text(item["description"]),
            item["quantity"],
            item["unit_cost"],
            item["line_total"]
        ])

    writer.writerow([])
    writer.writerow(["Invoice Total", invoice["total_amount"]])

    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
                f"attachment; filename=invoice_{invoice_id}.csv"
        }
    )


@app.route("/admin/invoices/create", methods=["POST"])
def admin_create_invoice():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "admin":
        abort(403)

    # Do not allow changes while viewing another user's account
    if (
        session.get("assumed_driver_id") is not None
        or session.get("assumed_sponsor_id") is not None
    ):
        abort(403)

     # Check the form security token
    submitted_token = request.form.get("csrf_token", "")
    expected_token = session.get("invoice_csrf", "")

    if not expected_token or not secrets.compare_digest(
        submitted_token, expected_token
    ):
        abort(400, "Invalid invoice form token.")


    sponsor_id = request.form.get("sponsor_id")
    description = request.form.get("description", "").strip()
    amount = request.form.get("amount", "").strip()

    # Check that the required fields were entered
    if not sponsor_id or not description or not amount:
        flash("Please fill out all fields.", "error")
        return redirect(url_for("admin_invoices"))

    from decimal import Decimal, InvalidOperation

    try:
        amount = Decimal(amount)

        if not amount.is_finite() or amount <= 0:
            raise ValueError()

        if amount.as_tuple().exponent < -2:
            raise ValueError()

        amount = amount.quantize(Decimal("0.01"))

        if amount > Decimal("9999999999.99"):
            raise ValueError()

        sponsor_id = int(sponsor_id)

        if sponsor_id <= 0:
            raise ValueError()

    except (ValueError, InvalidOperation):
        flash("Please enter a valid sponsor and amount.", "error")
        return redirect(url_for("admin_invoices"))

    if len(description) > 255:
        flash("Description must be 255 characters or fewer.", "error")
        return redirect(url_for("admin_invoices"))

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            # Make sure the sponsor exists
            cursor.execute(
                "SELECT sponsor_id FROM Sponsors WHERE sponsor_id = %s",
                (sponsor_id,)
            )

            if not cursor.fetchone():
                flash("Sponsor not found.", "error")
                return redirect(url_for("admin_invoices"))

            # Create the manual invoice
            cursor.execute(
                """
                INSERT INTO Invoices
                    (sponsor_id, invoice_type, description,
                     total_amount, created_by_user_id)
                VALUES
                    (%s, 'Manual', %s, %s, %s)
                """,
                (
                    sponsor_id,
                    description,
                    amount,
                    session["user_id"]
                )
            )

            invoice_id = cursor.lastrowid

            # Add the expense as an invoice item
            cursor.execute(
                """
                INSERT INTO Invoice_Items
                    (invoice_id, description, quantity,
                     unit_cost, line_total)
                VALUES
                    (%s, %s, 1, %s, %s)
                """,
                (invoice_id, description, amount, amount)
            )

        conn.commit()
        queue_event("Order", "Manual invoice created", success=True,
                    status="Created", sponsor_id=sponsor_id,
                    details={"invoice_id": invoice_id})
        flash("Manual invoice created successfully!", "success")

    except Exception:
        conn.rollback()
        app.logger.exception("Could not create manual invoice")
        flash("Could not create the invoice.", "error")

    finally:
        conn.close()

    return redirect(url_for("admin_invoices"))


@app.route("/admin/invoices/<int:invoice_id>")
def admin_invoice_details(invoice_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "admin":
        abort(403)

    # Do not allow access while viewing another account
    if (
        session.get("assumed_driver_id") is not None
        or session.get("assumed_sponsor_id") is not None
    ):
        abort(403)

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            # Get the invoice information
            cursor.execute(
                """
                SELECT
                    Invoices.invoice_id,
                    Invoices.invoice_date,
                    Invoices.invoice_type,
                    Invoices.description,
                    Invoices.total_amount,
                    Invoices.status,
                    Sponsors.company_name,
                    Orders.order_number
                FROM Invoices
                JOIN Sponsors
                    ON Invoices.sponsor_id = Sponsors.sponsor_id
                LEFT JOIN Orders
                    ON Invoices.order_id = Orders.order_id
                WHERE Invoices.invoice_id = %s
                """,
                (invoice_id,)
            )

            invoice = cursor.fetchone()

            if not invoice:
                abort(404)

            # Get the individual invoice items
            cursor.execute(
                """
                SELECT
                    description,
                    quantity,
                    unit_cost,
                    line_total
                FROM Invoice_Items
                WHERE invoice_id = %s
                ORDER BY invoice_item_id
                """,
                (invoice_id,)
            )

            items = cursor.fetchall()

    finally:
        conn.close()

    csrf_token = session.get("invoice_csrf")

    if not csrf_token:
        csrf_token = secrets.token_urlsafe(32)
        session["invoice_csrf"] = csrf_token

    return render_template(
        "admin/invoice_details.html",
        invoice=invoice,
        items=items,
        csrf_token=csrf_token
    )



@app.route("/admin/invoices/<int:invoice_id>/status", methods=["POST"])
def admin_update_invoice_status(invoice_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session.get("role", "").lower() != "admin":
        abort(403)

    # Prevent changes while viewing another account
    if (
        session.get("assumed_driver_id") is not None
        or session.get("assumed_sponsor_id") is not None
    ):
        abort(403)

    # Check the form security token
    submitted_token = request.form.get("csrf_token", "")
    expected_token = session.get("invoice_csrf", "")

    if not expected_token or not secrets.compare_digest(
        submitted_token, expected_token
    ):
        abort(400, "Invalid invoice form token.")

    # Get the selected status
    new_status = request.form.get("status", "")

    if new_status not in ("Pending", "Paid", "Cancelled"):
        flash("Invalid invoice status.", "error")
        return redirect(
            url_for("admin_invoice_details", invoice_id=invoice_id)
        )

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            # Make sure the invoice exists
            cursor.execute(
                "SELECT invoice_id FROM Invoices WHERE invoice_id = %s",
                (invoice_id,)
            )

            if not cursor.fetchone():
                abort(404)

            # Update the invoice status
            cursor.execute(
                """
                UPDATE Invoices
                SET status = %s
                WHERE invoice_id = %s
                """,
                (new_status, invoice_id)
            )

        conn.commit()
        queue_event("Order", "Invoice status updated", success=True,
                    status=new_status, details={"invoice_id": invoice_id})
        flash("Invoice status updated successfully!", "success")

    except Exception:
        conn.rollback()
        app.logger.exception("Could not update invoice status")
        flash("Could not update invoice status.", "error")

    finally:
        conn.close()

    return redirect(
        url_for("admin_invoice_details", invoice_id=invoice_id)
    )


@app.route("/admin/sponsors", methods=["GET", "POST"])
def admin_sponsors():
    if (
        not session.get("user_id")
        or (session.get("role") or "").lower() != "admin"
    ):
        abort(403)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        if not name or not email:
            flash("Name and email are required.", "error")
        elif len(name) > 100 or len(email) > 255:
            flash("Name or email is too long.", "error")
        elif len(password) < MIN_PASSWORD_LENGTH:
            flash(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.", "error")
        else:
            conn = get_db()
            try:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT user_id FROM Users WHERE LOWER(email) = %s",
                        (email.lower(),)
                    )

                    if cursor.fetchone():
                        flash(f"An account with {email} already exists.", "error")
                    else:
                        cursor.execute(
                            """
                            INSERT INTO Users
                                (email, first_name, last_name, role)
                            VALUES
                                (%s, %s, '', 'Sponsor')
                            """,
                            (email, name)
                        )

                        user_id = cursor.lastrowid

                        cursor.execute(
                            "INSERT INTO Password (user_id, password_hash) VALUES (%s, %s)",
                            (user_id, generate_password_hash(password, method="pbkdf2:sha256"))
                        )

                        # Same shape as an approved sponsor request: the org id is its first user's id.
                        cursor.execute(
                            """
                            INSERT INTO Sponsors
                                (sponsor_id, company_name, email)
                            VALUES
                                (%s, %s, %s)
                            """,
                            (user_id, name, email)
                        )

                        cursor.execute(
                            "INSERT INTO Sponsor_Users (user_id, sponsor_id) VALUES (%s, %s)",
                            (user_id, user_id)
                        )

                        conn.commit()

                        queue_event("Sponsor Management", "Sponsor added by admin", success=True,
                                    status="Created", details={"sponsor_id": user_id})
                        flash(f"Added sponsor {name}. They can log in with {email}.", "success")
                        return redirect(url_for("admin_sponsors"))

            except Exception as e:
                conn.rollback()
                print(f"Failed to add sponsor {email}: {e}")
                flash("Something went wrong adding that sponsor.", "error")

            finally:
                conn.close()

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT sponsor_id, company_name, point_value, is_active
                FROM Sponsors
                ORDER BY is_active DESC, company_name
                """
            )
            sponsors = cursor.fetchall()
    finally:
        conn.close()

    return render_template("admin/admin_sponsors.html", sponsors=sponsors)


@app.route("/admin/sponsors/<int:sponsor_id>")
def admin_sponsor_info(sponsor_id):
    if (
        not session.get("user_id")
        or (session.get("role") or "").lower() != "admin"
    ):
        abort(403)

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT sponsor_id, company_name, email, phone, invite_code,
                       point_value, is_active
                FROM Sponsors
                WHERE sponsor_id = %s
                """,
                (sponsor_id,)
            )
            sponsor = cursor.fetchone()

            if not sponsor:
                abort(404)

            cursor.execute(
                """
                SELECT Users.user_id, Users.first_name, Users.last_name,
                       Users.email, Users.is_active
                FROM Sponsor_Users
                JOIN Users
                    ON Users.user_id = Sponsor_Users.user_id
                WHERE Sponsor_Users.sponsor_id = %s
                  AND Users.email NOT LIKE '%%@invalid.example'
                ORDER BY Users.last_name, Users.first_name
                """,
                (sponsor_id,)
            )
            team = cursor.fetchall()

            cursor.execute(
                """
                SELECT Users.user_id AS driver_id, Users.first_name, Users.last_name,
                       Users.email, Driver_Sponsors.status, Driver_Sponsors.points_balance
                FROM Driver_Sponsors
                JOIN Users
                    ON Users.user_id = Driver_Sponsors.driver_id
                WHERE Driver_Sponsors.sponsor_id = %s
                ORDER BY Driver_Sponsors.status = 'Active' DESC, Users.last_name, Users.first_name
                """,
                (sponsor_id,)
            )
            drivers = cursor.fetchall()

            cursor.execute(
                """
                SELECT COUNT(*) AS order_count,
                       COALESCE(SUM(total_points), 0) AS points_redeemed
                FROM Orders
                WHERE sponsor_id = %s
                  AND status <> 'Cancelled'
                """,
                (sponsor_id,)
            )
            stats = cursor.fetchone() or {"order_count": 0, "points_redeemed": 0}
    finally:
        conn.close()

    return render_template(
        "admin/sponsor_info.html",
        sponsor=sponsor,
        team=team,
        drivers=drivers,
        stats=stats
    )


@app.route("/admin/sponsors/<int:sponsor_id>/active", methods=["POST"])
def admin_set_sponsor_active(sponsor_id):
    """Remove (deactivate) or restore a sponsor. Nothing is deleted, so its
    orders, invoices and point history stay intact."""
    if (
        not session.get("user_id")
        or (session.get("role") or "").lower() != "admin"
    ):
        abort(403)

    active = request.form.get("active") == "1"

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE Sponsors SET is_active = %s WHERE sponsor_id = %s",
                (active, sponsor_id)
            )
            found = cursor.rowcount

            # Its team can't log in while removed. Bulk upload's placeholder
            # organization user always stays inactive.
            cursor.execute(
                """
                UPDATE Users
                JOIN Sponsor_Users
                    ON Sponsor_Users.user_id = Users.user_id
                SET Users.is_active = %s
                WHERE Sponsor_Users.sponsor_id = %s
                  AND Users.email NOT LIKE '%%@invalid.example'
                """,
                (active, sponsor_id)
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if found:
        queue_event("Sponsor Management", "Sponsor restored" if active else "Sponsor removed",
                    success=True, status="Active" if active else "Inactive",
                    sponsor_id=sponsor_id)
        flash("Sponsor restored. Its team can log in again." if active
              else "Sponsor removed. Its team can no longer log in, and drivers won't see it.",
              "success")

    return redirect(url_for("admin_sponsor_info", sponsor_id=sponsor_id))


@app.route("/admin/sponsors/<int:sponsor_id>/point-value", methods=["POST"])
def admin_set_point_value(sponsor_id):
    """Dollars each point is worth for this sponsor's catalog."""
    if (
        not session.get("user_id")
        or (session.get("role") or "").lower() != "admin"
    ):
        abort(403)

    back = (url_for("admin_sponsor_info", sponsor_id=sponsor_id)
            if request.form.get("back") == "info" else url_for("admin_sponsors"))

    try:
        point_value = Decimal(request.form.get("point_value", ""))
    except InvalidOperation:
        point_value = None

    if point_value is None or not Decimal("0.0001") <= point_value <= Decimal("100"):
        flash("Point value must be between $0.0001 and $100.", "error")
        return redirect(back)

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE Sponsors SET point_value = %s WHERE sponsor_id = %s",
                (point_value, sponsor_id)
            )
            updated = cursor.rowcount
        conn.commit()
    finally:
        conn.close()

    if updated:
        queue_event("Sponsor Management", "Point value changed by admin", success=True,
                    status="Updated", sponsor_id=sponsor_id)
        flash(f"Point value updated to ${point_value:.4f}.", "success")
    else:
        flash("No change was made.", "error")

    return redirect(back)


@app.route("/admin/admins", methods=["POST"])
def admin_create_admin():
    if (
        not session.get("user_id")
        or (session.get("role") or "").lower() != "admin"
    ):
        abort(403)

    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")

    if not first_name or not last_name or not email:
        flash("Name and email are required.", "error")
        return redirect(url_for("home"))

    if len(first_name) > 100 or len(last_name) > 100 or len(email) > 255:
        flash("Name or email is too long.", "error")
        return redirect(url_for("home"))

    if len(password) < MIN_PASSWORD_LENGTH:
        flash(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.", "error")
        return redirect(url_for("home"))

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT user_id FROM Users WHERE LOWER(email) = %s",
                (email.lower(),)
            )

            if cursor.fetchone():
                flash(f"An account with {email} already exists.", "error")
                return redirect(url_for("home"))

            cursor.execute(
                """
                INSERT INTO Users
                    (email, first_name, last_name, role)
                VALUES
                    (%s, %s, %s, 'Admin')
                """,
                (email, first_name, last_name)
            )

            user_id = cursor.lastrowid

            cursor.execute(
                "INSERT INTO Password (user_id, password_hash) VALUES (%s, %s)",
                (user_id, generate_password_hash(password, method="pbkdf2:sha256"))
            )

        conn.commit()

    except Exception as e:
        conn.rollback()
        print(f"Failed to add admin {email}: {e}")
        flash("Something went wrong adding that admin.", "error")
        return redirect(url_for("home"))

    finally:
        conn.close()

    queue_event("Admin Management", "Admin added", success=True,
                status="Created", details={"target_type": "admin", "target_id": user_id})
    flash(f"Added {first_name} {last_name} as an admin. They can log in with {email}.", "success")
    return redirect(url_for("home"))


SPONSOR_SHOPPING_ENDPOINTS = {
    "ebay_catalog.add_to_cart",
    "ebay_catalog.remove_from_cart",
    "ebay_catalog.checkout",
}


@app.before_request
def enforce_assumed_account_read_only():
    assumed_driver = session.get("assumed_driver_id") is not None
    assumed_sponsor = session.get("assumed_sponsor_id") is not None

    if not assumed_driver and not assumed_sponsor:
        return None

    allowed_endpoints = {
        "sponsor_exit_assumed_driver",
        "admin_exit_assumed_sponsor",
        "session_keep_alive",
    }

    # A sponsor viewing a driver may shop the catalog on that driver's behalf;
    # an admin only looks, but can switch between the driver's sponsors.
    if assumed_driver and session.get("assumed_driver_by") == "admin":
        allowed_endpoints.add("switch_driver_sponsor")
    elif assumed_driver:
        allowed_endpoints |= SPONSOR_SHOPPING_ENDPOINTS

    if request.endpoint in allowed_endpoints:
        return None

    if request.method not in ("GET", "HEAD", "OPTIONS"):
        abort(
            403,
            description="This support session is read-only. Exit the assumed account to make changes.",
        )




# ==========================================
# ORDER HISTORY AND CANCELLING
# Orders can be cancelled until they ship. Cancelling refunds the points to
# the driver's balance with that sponsor and cancels the unpaid invoice.
# ==========================================

CANCELLABLE_ORDER_STATUSES = ("Placed", "Processing")
ADMIN_ORDER_STATUSES = ("Placed", "Processing", "Shipped", "Delivered")


def fetch_orders(cursor, driver_id=None, status=None, limit=200):
    """Orders newest first, each with its items. driver_id=None means every driver."""
    cursor.execute(
        """
        SELECT
            Orders.order_id,
            Orders.order_number,
            Orders.order_date,
            Orders.status,
            Orders.total_points,
            Orders.driver_id,
            Sponsors.company_name,
            Users.first_name,
            Users.last_name,
            Order_Items.product_name,
            Order_Items.quantity,
            Order_Items.unit_price_points
        FROM (
            SELECT *
            FROM Orders
            WHERE (%s IS NULL OR driver_id = %s)
              AND (%s IS NULL OR status = %s)
            ORDER BY order_date DESC, order_id DESC
            LIMIT %s
        ) AS Orders
        JOIN Sponsors
            ON Sponsors.sponsor_id = Orders.sponsor_id
        JOIN Users
            ON Users.user_id = Orders.driver_id
        LEFT JOIN Order_Items
            ON Order_Items.order_id = Orders.order_id
        ORDER BY Orders.order_date DESC, Orders.order_id DESC, Order_Items.order_item_id
        """,
        (driver_id, driver_id, status, status, limit)
    )

    orders = {}
    for row in cursor.fetchall():
        order = orders.setdefault(row["order_id"], {
            "order_id": row["order_id"],
            "order_number": row["order_number"],
            "order_date": row["order_date"],
            "status": row["status"],
            "total_points": row["total_points"],
            "company_name": row["company_name"],
            "driver_name": f"{row['first_name']} {row['last_name'] or ''}".strip(),
            "can_cancel": row["status"] in CANCELLABLE_ORDER_STATUSES,
            "items": [],
        })
        if row["product_name"]:
            order["items"].append(row)
    return list(orders.values())


def cancel_order(cursor, order_id, cancelled_by, driver_id=None):
    """Cancel an order and refund its points. Returns the order, or None if it
    can't be cancelled (not found, not this driver's, or already shipped)."""
    cursor.execute(
        """
        SELECT order_id, order_number, driver_id, sponsor_id, total_points
        FROM Orders
        WHERE order_id = %s
          AND (%s IS NULL OR driver_id = %s)
          AND status IN %s
        FOR UPDATE
        """,
        (order_id, driver_id, driver_id, CANCELLABLE_ORDER_STATUSES)
    )
    order = cursor.fetchone()

    if not order:
        return None

    cursor.execute(
        "UPDATE Orders SET status = 'Cancelled' WHERE order_id = %s",
        (order_id,)
    )

    if order["total_points"]:
        cursor.execute(
            """
            UPDATE Driver_Sponsors
            SET points_balance = points_balance + %s
            WHERE driver_id = %s
              AND sponsor_id = %s
            """,
            (order["total_points"], order["driver_id"], order["sponsor_id"])
        )

        cursor.execute(
            """
            INSERT INTO Point_Transactions
                (driver_id, sponsor_id, changed_by_user_id, point_change, reason)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (order["driver_id"], order["sponsor_id"], cancelled_by, order["total_points"],
             f"Refund for cancelled order {order['order_number']}")
        )

    # The sponsor isn't billed for a cancelled order.
    cursor.execute(
        "UPDATE Invoices SET status = 'Cancelled' WHERE order_id = %s AND status = 'Pending'",
        (order_id,)
    )

    return order


@app.route("/driver/orders")
def driver_orders():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if (session.get("role") or "").lower() != "driver":
        return redirect(url_for("home"))

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            orders = fetch_orders(cursor, driver_id=session["user_id"])
    finally:
        conn.close()

    return render_template("shared/orders.html", orders=orders, is_admin=False)


@app.route("/driver/orders/<int:order_id>/cancel", methods=["POST"])
def driver_cancel_order(order_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if (session.get("role") or "").lower() != "driver":
        return redirect(url_for("home"))

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            order = cancel_order(cursor, order_id, session["user_id"], driver_id=session["user_id"])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if order:
        queue_event("Orders", "Order cancelled by driver", success=True, status="Cancelled",
                    sponsor_id=order["sponsor_id"],
                    details={"order_number": order["order_number"], "points": order["total_points"]})
        flash(f"Order {order['order_number']} was cancelled and "
              f"{order['total_points']:,} points were returned to you.", "success")
    else:
        flash("That order can't be cancelled. Orders can only be cancelled before they ship.", "error")

    return redirect(url_for("driver_orders"))


@app.route("/admin/orders")
def admin_orders():
    if (
        not session.get("user_id")
        or (session.get("role") or "").lower() != "admin"
    ):
        abort(403)

    status = request.args.get("status", "")
    if status not in ADMIN_ORDER_STATUSES + ("Cancelled",):
        status = ""

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            orders = fetch_orders(cursor, status=status or None)
    finally:
        conn.close()

    return render_template(
        "shared/orders.html",
        orders=orders,
        is_admin=True,
        status_filter=status,
        statuses=ADMIN_ORDER_STATUSES
    )


@app.route("/admin/orders/<int:order_id>/status", methods=["POST"])
def admin_order_status(order_id):
    if (
        not session.get("user_id")
        or (session.get("role") or "").lower() != "admin"
    ):
        abort(403)

    new_status = request.form.get("status", "")

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            if new_status == "Cancelled":
                order = cancel_order(cursor, order_id, session["user_id"])
                updated = order is not None
            elif new_status in ADMIN_ORDER_STATUSES:
                # A cancelled order stays cancelled; its points were already refunded.
                cursor.execute(
                    "UPDATE Orders SET status = %s WHERE order_id = %s AND status <> 'Cancelled'",
                    (new_status, order_id)
                )
                updated = cursor.rowcount > 0
            else:
                updated = False
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if updated and new_status == "Cancelled":
        queue_event("Orders", "Order cancelled by admin", success=True, status="Cancelled",
                    sponsor_id=order["sponsor_id"], driver_id=order["driver_id"],
                    details={"order_number": order["order_number"], "points": order["total_points"]})
        flash(f"Order {order['order_number']} was cancelled and "
              f"{order['total_points']:,} points were refunded to the driver.", "success")
    elif updated:
        queue_event("Orders", "Order status changed by admin", success=True, status=new_status,
                    details={"target_type": "order", "target_id": order_id})
        flash(f"Order status changed to {new_status}.", "success")
    else:
        flash("That order couldn't be changed. Shipped orders can't be cancelled, "
              "and cancelled orders can't be reopened.", "error")

    return redirect(url_for("admin_orders", status=request.args.get("status", "")))


@app.route("/admin/audit-logs")
def admin_audit_logs():
    """Read-only audit log for administrators, newest 100 events first."""
    if not session.get("user_id"):
        return redirect(url_for("login"))
    if (session.get("role") or "").lower() != "admin":
        abort(403)
    if session.get("assumed_driver_id") is not None or session.get("assumed_sponsor_id") is not None:
        abort(403)

    from datetime import datetime, timedelta

    selected_category = request.args.get("category", "").strip()[:64]
    selected_user = request.args.get("user_id", "").strip()
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    if selected_user and (not selected_user.isdecimal() or len(selected_user) > 12):
        abort(400)

    if bool(start_date) != bool(end_date):
        abort(400, "Please select both a start and end date.")

    conditions = []
    params = []

    if selected_category:
        conditions.append("category = %s")
        params.append(selected_category)

    if selected_user:
        conditions.append("user_id = %s")
        params.append(int(selected_user))

    if start_date and end_date:
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError:
            abort(400, "Invalid date.")

        if start > end:
            abort(400, "End date cannot be before start date.")

        conditions.append("timestamp >= %s")
        params.append(start)

        # Include the entire end date.
        conditions.append("timestamp < %s")
        params.append(end + timedelta(days=1))

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT log_id, timestamp, user_id, category, action, status, "
                "success, ip_address, details FROM Audit_Logs" + where +
                " ORDER BY timestamp DESC, log_id DESC LIMIT 100",
                tuple(params)
            )
            events = cursor.fetchall()
    finally:
        conn.close()

    return render_template(
        "admin/audit_logs.html",
        events=events,
        selected_category=selected_category,
        selected_user=selected_user,
        start_date=start_date,
        end_date=end_date
    )


@app.route("/admin/audit-logs/export")
def admin_export_audit_logs():
    """Download every audit log matching the selected filters as CSV."""
    if not session.get("user_id"):
        return redirect(url_for("login"))
    if (session.get("role") or "").lower() != "admin":
        abort(403)
    if session.get("assumed_driver_id") is not None or session.get("assumed_sponsor_id") is not None:
        abort(403)

    import csv
    import io
    from datetime import datetime, timedelta
    from flask import Response

    selected_category = request.args.get("category", "").strip()[:64]
    selected_user = request.args.get("user_id", "").strip()
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    if selected_user and (not selected_user.isdecimal() or len(selected_user) > 12):
        abort(400)

    if bool(start_date) != bool(end_date):
        abort(400, "Please select both a start and end date.")

    conditions = []
    params = []

    if selected_category:
        conditions.append("category = %s")
        params.append(selected_category)

    if selected_user:
        conditions.append("user_id = %s")
        params.append(int(selected_user))

    if start_date and end_date:
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError:
            abort(400, "Invalid date.")

        if start > end:
            abort(400, "End date cannot be before start date.")

        conditions.append("timestamp >= %s")
        params.append(start)

        conditions.append("timestamp < %s")
        params.append(end + timedelta(days=1))

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT log_id, timestamp, user_id, username, sponsor_id, "
                "driver_id, category, action, status, reason, success, "
                "ip_address, details FROM Audit_Logs" + where +
                " ORDER BY timestamp DESC, log_id DESC",
                tuple(params)
            )
            events = cursor.fetchall()
    finally:
        conn.close()

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Log ID",
        "Date and Time",
        "User ID",
        "Username",
        "Sponsor ID",
        "Driver ID",
        "Category",
        "Action",
        "Status",
        "Reason",
        "Success",
        "IP Address",
        "Details"
    ])

    def safe_csv_text(value):
        value = str(value or "")
        if value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return value

    for event in events:
        writer.writerow([
            event["log_id"],
            event["timestamp"],
            event["user_id"],
            safe_csv_text(event["username"]),
            event["sponsor_id"],
            event["driver_id"],
            safe_csv_text(event["category"]),
            safe_csv_text(event["action"]),
            safe_csv_text(event["status"]),
            safe_csv_text(event["reason"]),
            event["success"],
            safe_csv_text(event["ip_address"]),
            safe_csv_text(event["details"])
        ])

    filename = "audit_logs"
    if start_date and end_date:
        filename += f"_{start_date}_to_{end_date}"
    filename += ".csv"

    return Response(
        "\ufeff" + output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )



if __name__ == "__main__":
    app.run(debug=True)
