from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, abort
import pymysql
import os
import hashlib
import smtplib
import secrets
from datetime import timedelta, datetime, timezone
from email.message import EmailMessage
from markupsafe import Markup
from zoneinfo import ZoneInfo
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)

from ebay_catalog import catalog_bp
app.register_blueprint(catalog_bp)
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
    "host": "cpsc4910-f26.cobd8enwsupz.us-east-1.rds.amazonaws.com",
    "user": "Team10",
    "password": "CPSC4910TEAM10",
    "database": "Team10_DB",
}


# Testing code for password reset.
# Change this to a randomly generated value later.
RESET_CODE = "123456"

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
        session.clear()
        return redirect(url_for("login"))

    try:
        last_activity_time = datetime.fromisoformat(last_activity)

        if last_activity_time.tzinfo is None:
            last_activity_time = last_activity_time.replace(
                tzinfo=timezone.utc
            )

    except (ValueError, TypeError):
        session.clear()
        return redirect(url_for("login"))

    now = datetime.now(timezone.utc)

    if now - last_activity_time >= SESSION_TIMEOUT:
        session.clear()
        return redirect(url_for("login"))


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

    return {
        "driver_sponsorships": sponsorships,
        "driver_sponsorship": pick_driver_sponsorship(sponsorships),
    }


@app.context_processor
def inject_current_year():
    return {"current_year": local_now().year}


@app.context_processor
def inject_notifications():
    """Unread count + latest notifications for the navbar dropdown on every page."""
    empty = {"unread_count": 0, "recent_notifications": []}

    if "user_id" not in session:
        return empty

    try:
        conn = get_db()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*) AS c
                    FROM Notifications
                    WHERE user_id = %s
                      AND is_read = 0
                    """,
                    (session["user_id"],)
                )
                unread_count = cursor.fetchone()["c"]

                cursor.execute(
                    """
                    SELECT notification_id, subject, message, is_read, created_at
                    FROM Notifications
                    WHERE user_id = %s
                    ORDER BY created_at DESC, notification_id DESC
                    LIMIT 15
                    """,
                    (session["user_id"],)
                )
                recent = cursor.fetchall()

                return {"unread_count": unread_count, "recent_notifications": recent}
        finally:
            conn.close()
    except Exception:
        return empty


def send_email(to_email, subject, body):
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
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(message)
        return True
    except Exception as e:
        print(f"Failed to send email to {to_email}: {e}")
        return False


def send_acceptance_email(email, first_name, temp_password=None):
    login_link = "https://cpsc4910team10truckingproject.org/login"

    if temp_password:
        details = (
            f"Log in here: {login_link}\n"
            f"Email: {email}\n"
            f"Temporary password: {temp_password}\n\n"
            "Please log in and change your password as soon as possible."
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


def send_sponsor_approved_email(email, first_name, company_name, temp_password):
    login_link = "https://cpsc4910team10truckingproject.org/login"

    body = (
        f"Hi {first_name or ''},\n\n"
        f"Your sponsor account for {company_name} has been approved.\n\n"
        f"Log in here: {login_link}\n"
        f"Email: {email}\n"
        f"Temporary password: {temp_password}\n\n"
        "Please log in and change your password as soon as possible."
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

    smtp_host = os.environ.get("SMTP_HOST")
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_from = os.environ.get("SMTP_FROM", smtp_username)

    if not smtp_host or not smtp_username or not smtp_password:
        print(f"[APPLICATION INVITE] Link for {email}: {apply_link}")
        return True

    message = EmailMessage()
    message["Subject"] = "You've been invited to Drivers Advantage"
    message["From"] = smtp_from
    message["To"] = email
    message.set_content(
        f"You've been invited to apply for the Drivers Advantage rewards program.\n\n"
        f"Complete your application here:\n{apply_link}\n\n"
        "If you weren't expecting this, you can ignore this email."
    )

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(message)
        return True
    except Exception as e:
        print(f"Failed to send invite email: {e}")
        return False
    
def send_reset_code(email):
    """
    Sends the password reset code to the user's email.

    SMTP settings should be stored as environment variables:

        SMTP_HOST
        SMTP_PORT
        SMTP_USERNAME
        SMTP_PASSWORD
        SMTP_FROM
    """

    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    smtp_from = os.environ.get("SMTP_FROM", smtp_username)

    # During initial testing, if SMTP isn't configured,
    # print the code so development can continue.
    if not smtp_host or not smtp_username or not smtp_password:
        print(f"[PASSWORD RESET] Code for {email}: {RESET_CODE}")
        return True

    message = EmailMessage()
    message["Subject"] = "Trucking Rewards Password Reset"
    message["From"] = smtp_from
    message["To"] = email
    message.set_content(
        f"Your Trucking Rewards password reset code is: {RESET_CODE}\n\n"
        "If you did not request a password reset, you can ignore this email."
    )

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(message)

        return True

    except Exception as e:
        print(f"Failed to send reset email: {e}")
        return False


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

            return redirect(url_for("home"))
        
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

    # User has submitted their email.
    if request.method == "POST":

        email = request.form.get("email", "").strip().lower()

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
                    SELECT user_id, email
                    FROM Users
                    WHERE LOWER(email) = %s
                    """,
                    (email,)
                )

                user = cursor.fetchone()

        finally:
            conn.close()

        if not user:
            return render_template(
                "shared/login.html",
                step="email",
                error="No account was found with that email address.",
                email=email
            )

        # Store information needed for the reset process.
        session["reset_user_id"] = user["user_id"]
        session["reset_email"] = user["email"]

        # Send the testing code.
        if not send_reset_code(user["email"]):
            session.pop("reset_user_id", None)
            session.pop("reset_email", None)

            return render_template(
                "shared/login.html",
                step="email",
                error="Unable to send the verification email. Please try again.",
                email=email
            )

        return render_template(
            "shared/login.html",
            step="code",
            email=user["email"]
        )

    # User clicked "Forgot Password?"
    # If we already have an email in the login form, the browser
    # cannot automatically pass it here because this is a new GET.
    # Therefore, show the email field.
    return render_template(
        "shared/login.html",
        step="email"
    )


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

@app.route("/verify-reset-code", methods=["POST"])
def verify_reset_code():

    code = request.form.get("code", "").strip()

    if "reset_user_id" not in session:
        return redirect(url_for("forgot_password"))

    if code != RESET_CODE:
        return render_template(
            "shared/login.html",
            step="code",
            email=session.get("reset_email"),
            error="Invalid verification code."
        )

    # Code is correct.
    session["reset_verified"] = True

    return render_template(
        "shared/login.html",
        step="reset"
    )


@app.route("/reset-password", methods=["POST"])
def reset_password():

    if "reset_user_id" not in session:
        return redirect(url_for("forgot_password"))

    if not session.get("reset_verified"):
        return redirect(url_for("forgot_password"))

    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    if not new_password:
        return render_template(
            "shared/login.html",
            step="reset",
            error="Please enter a new password."
        )

    if new_password != confirm_password:
        return render_template(
            "shared/login.html",
            step="reset",
            error="The passwords do not match."
        )

    user_id = session["reset_user_id"]

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            # Get the user's current password hash.
            cursor.execute(
                """
                SELECT password_hash
                FROM Password
                WHERE user_id = %s
                """,
                (user_id,)
            )

            password_record = cursor.fetchone()

            if not password_record:
                return render_template(
                    "shared/login.html",
                    step="reset",
                    error="Unable to find your password information."
                )

            # Check whether the new password is the same
            # as the existing password.
            if check_password_hash(
                password_record["password_hash"],
                new_password
            ):
                return render_template(
                    "shared/login.html",
                    step="reset",
                    error="Your new password must be different from your current password."
                )

            # Hash the new password.
            new_password_hash = generate_password_hash(new_password, method='pbkdf2:sha256')

            cursor.execute(
                """
                UPDATE Password
                SET password_hash = %s
                WHERE user_id = %s
                """,
                (new_password_hash, user_id)
            )

        conn.commit()

    finally:
        conn.close()

    # Clear all password-reset information.
    session.pop("reset_user_id", None)
    session.pop("reset_email", None)
    session.pop("reset_verified", None)

    return render_template(
        "shared/login.html",
        step="login",
        error="Password reset successfully. Please log in."
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))
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

            # Generate a temporary password.
            temp_password = secrets.token_urlsafe(9)
            password_hash = generate_password_hash(temp_password, method="pbkdf2:sha256")

            cursor.execute(
                """
                INSERT INTO Password
                    (user_id, password_hash)
                VALUES
                    (%s, %s)
                """,
                (user_id, password_hash)
            )

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

            send_acceptance_email(
                application["applicant_email"],
                application["applicant_first_name"],
                temp_password
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
        search=search
    )

@app.route("/sponsor/drivers/<int:driver_id>/points", methods=["POST"])
def change_driver_points(driver_id):

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "sponsor":
        return redirect(url_for("home"))

    action = request.form.get("action", "")
    reason = request.form.get("reason", "").strip()[:255]

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

    finally:
        conn.close()

    if action == "add":
        flash(f"Added {amount} points.", "success")
    else:
        flash(f"Deducted {amount} points.", "success")

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


@app.before_request
def run_due_recurring_points():
    if request.method != "GET" or request.endpoint in (None, "static"):
        return

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
    finally: 
        conn.close()
    return render_template(
        "driver/sponsors.html",
        first_name=session["first_name"],
        role=session["role"],
        sponsors=sponsors
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
        session["driver_sponsor_id"] = int(request.form.get("sponsor_id", ""))
    except ValueError:
        pass

    # Go back to the page they were on, but never off-site.
    back = request.referrer or ""
    if not back.startswith(request.host_url):
        back = url_for("driver_dashboard")

    return redirect(back)


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

            temp_password = secrets.token_urlsafe(9)

            cursor.execute(
                "INSERT INTO Password (user_id, password_hash) VALUES (%s, %s)",
                (user_id, generate_password_hash(temp_password, method="pbkdf2:sha256"))
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

            conn.commit()

    except Exception as e:
        conn.rollback()
        print(f"Failed to approve sponsor request {request_id}: {e}")
        flash("Something went wrong creating that sponsor account.", "error")
        return redirect(url_for("admin_sponsor_requests"))

    finally:
        conn.close()

    send_sponsor_approved_email(
        sponsor_req["email"],
        sponsor_req["first_name"],
        sponsor_req["company_name"],
        temp_password
    )

    flash(
        f"Approved {sponsor_req['company_name']}. Login details were emailed to {sponsor_req['email']}.",
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


@app.route("/sponsor/assume-driver/<int:driver_id>", methods=["POST"])
def sponsor_assume_driver(driver_id):
    """Temporarily view a driver account from a sponsor's perspective."""

    # Only authenticated sponsors may start this mode.
    if not session.get("user_id") or (session.get("role") or "").lower() != "sponsor":
        return "Forbidden", 403

    # Prevent nested assume-driver sessions.
    if session.get("assumed_driver_id"):
        return "You are already viewing a driver.", 400

    sponsor_user_id = session["user_id"]
    preferred_sponsor_id = session.get("sponsor_id")

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            # Verify that the logged-in user belongs to this sponsor.
            cursor.execute(
                """
                SELECT sponsor_id
                FROM Sponsor_Users
                WHERE user_id = %s
                  AND (%s IS NULL OR sponsor_id = %s)
                LIMIT 1
                """,
                (sponsor_user_id, preferred_sponsor_id, preferred_sponsor_id),
            )
            sponsor_row = cursor.fetchone()

            if not sponsor_row:
                return "Your account is not associated with a sponsor.", 403

            sponsor_id = sponsor_row["sponsor_id"]

            # Verify the driver is associated with this sponsor.
            # Do not trust the driver_id supplied by the browser by itself.
            cursor.execute(
                """
                SELECT
                    d.driver_id,
                    d.user_id,
                    u.first_name,
                    u.last_name
                FROM Drivers AS d
                JOIN Users AS u ON u.user_id = d.user_id
                JOIN Driver_Sponsors AS ds ON ds.driver_id = d.driver_id
                WHERE d.driver_id = %s
                  AND ds.sponsor_id = %s
                LIMIT 1
                """,
                (driver_id, sponsor_id),
            )
            driver = cursor.fetchone()

            if not driver:
                return "That driver is not associated with your sponsor.", 404

    finally:
        conn.close()

    # Preserve the sponsor's complete original session so we can restore it.
    original_session = dict(session)

    # Switch the active session to the driver's identity.
    # Existing driver routes can now use their usual session fields.
    session["role"] = "driver"
    session["user_id"] = driver["user_id"]
    session["driver_id"] = driver["driver_id"]
    session["driver_sponsor_id"] = sponsor_id
    session["first_name"] = driver["first_name"]
    session["last_name"] = driver["last_name"] or ""

    # Keep explicit support-mode metadata for the banner and safety checks.
    session["_assume_driver_original"] = original_session
    session["assumed_driver_id"] = driver["driver_id"]
    session["assumed_driver_name"] = (
        f"{driver['first_name']} {driver['last_name'] or ''}".strip()
    )
    session["assumed_sponsor_id"] = sponsor_id

    flash(
        f"Now viewing {session['assumed_driver_name']}'s account in read-only mode.",
        "info",
    )
    return redirect(url_for("driver_dashboard"))


@app.route("/sponsor/exit-assumed-driver", methods=["POST"])
def sponsor_exit_assumed_driver():
    """Restore the sponsor's original session."""

    original_session = session.get("_assume_driver_original")

    if not original_session:
        return "No assumed-driver session is active.", 400

    # Restore the original sponsor identity and all of its session values.
    session.clear()
    session.update(original_session)

    flash("You have returned to your sponsor account.", "success")
    return redirect(url_for("sponsor_drivers"))

@app.before_request
def enforce_assumed_driver_read_only():
    if not session.get("assumed_driver_id"):
        return None

    # The one operation allowed to end support mode.
    if request.endpoint == "sponsor_exit_assumed_driver":
        return None

    # Preserve the existing session keep-alive endpoint.
    # This endpoint should only refresh session activity, not alter account data.
    if request.path == "/session/keep-alive":
        return None

    # Block POST, PUT, PATCH and DELETE requests during support mode.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        return (
            "This is a read-only support session. Exit driver view to make changes.",
            403,
        )

    return None


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

    flash("Returned to your admin account.", "success")
    return redirect(url_for("home"))


@app.route("/admin/sponsors")
def admin_sponsors():
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
                SELECT sponsor_id, company_name
                FROM Sponsors
                ORDER BY company_name
                """
            )
            sponsors = cursor.fetchall()
    finally:
        conn.close()

    return render_template("admin/admin_sponsors.html", sponsors=sponsors)


@app.before_request
def enforce_assumed_account_read_only():
    assumed_driver = session.get("assumed_driver_id") is not None
    assumed_sponsor = session.get("assumed_sponsor_id") is not None

    if not assumed_driver and not assumed_sponsor:
        return None

    allowed_exit_endpoints = {
        "sponsor_exit_assumed_driver",
        "admin_exit_assumed_sponsor",
    }

    if request.endpoint in allowed_exit_endpoints:
        return None

    if request.method not in ("GET", "HEAD", "OPTIONS"):
        abort(
            403,
            description="This support session is read-only. Exit the assumed account to make changes.",
        )



if __name__ == "__main__":
    app.run(debug=True)