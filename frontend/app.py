from flask import Flask, render_template, request, redirect, url_for, session
import pymysql
import os
import smtplib
from datetime import timedelta
from email.message import EmailMessage
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "change_this_to_a_secure_random_value"
)
app.permanent_session_lifetime = timedelta(days=14) 

DB_CONFIG = {
    "host": "cpsc4910-f26.cobd8enwsupz.us-east-1.rds.amazonaws.com",
    "user": "Team10",
    "password": "CPSC4910TEAM10",
    "database": "Team10_DB",
}


# Testing code for password reset.
# Change this to a randomly generated value later.
RESET_CODE = "123456"


def get_db():
    return pymysql.connect(
        **DB_CONFIG,
        cursorclass=pymysql.cursors.DictCursor
    )


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


@app.route("/")
def home():

    if "user_id" not in session:
        return redirect(url_for("login"))

    role = session["role"].lower()

    # -----------------------------------------
    # Sponsor home page
    # -----------------------------------------
    if role == "sponsor":

        return render_template(
            "sponsor/dashboard.html",
            first_name=session["first_name"],
            role=session["role"]
        )

    # -----------------------------------------
    # Driver home page
    # -----------------------------------------
    if role == "driver":

        conn = get_db()

        try:
            with conn.cursor() as cursor:

                cursor.execute(
                    """
                    SELECT
                        Sponsors.company_name
                    FROM Drivers

                    JOIN Driver_Applications
                        ON Drivers.driver_id =
                           Driver_Applications.driver_id

                    JOIN Sponsors
                        ON Driver_Applications.sponsor_id =
                           Sponsors.sponsor_id

                    WHERE Drivers.driver_id = %s
                      AND Driver_Applications.status = 'Pending'

                    ORDER BY Driver_Applications.application_date DESC

                    LIMIT 1
                    """,
                    (session["user_id"],)
                )

                sponsor = cursor.fetchone()

        finally:
            conn.close()

        return render_template(
            "shared/home.html",
            first_name=session["first_name"],
            role=session["role"],
            sponsor=sponsor
        )

    # -----------------------------------------
    # Other roles
    # -----------------------------------------

    return render_template(
        "shared/home.html",
        first_name=session["first_name"],
        role=session["role"]
    )


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
            session["user_id"] = user["user_id"]
            session["first_name"] = user["first_name"]
            session["role"] = user["role"]

            return redirect(url_for("home"))

        return render_template(
            "shared/login.html",
            error="Invalid email or password.",
            step="login"
        )

    return render_template(
        "shared/login.html",
        step="login"
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

                # Find the driver.
                cursor.execute(
                    """
                    SELECT
                        Drivers.driver_id,
                        Users.first_name,
                        Users.last_name,
                        Users.email
                    FROM Drivers
                    JOIN Users
                        ON Drivers.driver_id = Users.user_id
                    WHERE LOWER(Users.email) = %s
                    """,
                    (driver_email,)
                )

                driver = cursor.fetchone()

                if not driver:
                    return render_template(
                        "sponsor/applications.html",
                        first_name=session["first_name"],
                        role=session["role"],
                        applications=[],
                        error="No driver was found with that email address."
                    )

                sponsor_id = session["user_id"]

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

                # Prevent duplicate pending applications.
                cursor.execute(
                    """
                    SELECT application_id
                    FROM Driver_Applications
                    WHERE driver_id = %s
                      AND sponsor_id = %s
                      AND status = 'Pending'
                    """,
                    (
                        driver["driver_id"],
                        sponsor_id
                    )
                )

                existing = cursor.fetchone()

                if existing:
                    return render_template(
                        "sponsor/applications.html",
                        first_name=session["first_name"],
                        role=session["role"],
                        applications=[],
                        error="There is already a pending application for this driver."
                    )

                # Create application.
                cursor.execute(
                    """
                    INSERT INTO Driver_Applications
                        (driver_id, sponsor_id, status)
                    VALUES
                        (%s, %s, 'Pending')
                    """,
                    (
                        driver["driver_id"],
                        sponsor_id
                    )
                )

                conn.commit()

                return redirect(
                    url_for("sponsor_applications")
                )

            # Load sponsor's applications.
            cursor.execute(
                """
                SELECT
                    Driver_Applications.application_id,
                    Driver_Applications.application_date,
                    Driver_Applications.status,
                    Driver_Applications.decision_date,
                    Driver_Applications.reason,

                    Users.first_name AS driver_first_name,
                    Users.last_name AS driver_last_name,
                    Users.email AS driver_email

                FROM Driver_Applications

                JOIN Drivers
                    ON Driver_Applications.driver_id =
                       Drivers.driver_id

                JOIN Users
                    ON Drivers.driver_id = Users.user_id

                WHERE Driver_Applications.sponsor_id = %s

                ORDER BY Driver_Applications.application_date DESC
                """,
                (session["user_id"],)
            )

            applications = cursor.fetchall()

    finally:
        conn.close()

    return render_template(
        "sponsor/applications.html",
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

    conn = get_db()

    try:
        with conn.cursor() as cursor:

            cursor.execute(
                """
                SELECT
                    Drivers.driver_id,
                    Drivers.points_balance,
                    Drivers.status,

                    Users.first_name,
                    Users.last_name,
                    Users.email

                FROM Drivers

                JOIN Users
                    ON Drivers.driver_id = Users.user_id

                WHERE Drivers.sponsor_id = %s

                ORDER BY Users.last_name, Users.first_name
                """,
                (session["user_id"],)
            )

            drivers = cursor.fetchall()

    finally:
        conn.close()

    return render_template(
        "sponsor/drivers.html",
        first_name=session["first_name"],
        role=session["role"],
        drivers=drivers
    )


@app.route("/sponsor/dashboard")
def sponsor_dashboard():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "sponsor":
        return redirect(url_for("home"))

    return render_template(
        "sponsor/dashboard.html",
        first_name=session["first_name"],
        role=session["role"]
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
                """
                SELECT
                    Drivers.driver_id,
                    Drivers.points_balance,
                    Drivers.status,
                    Sponsors.company_name AS sponsor_name,
                    Sponsors.email AS sponsor_email

                FROM Drivers

                LEFT JOIN Sponsors
                    ON Drivers.sponsor_id = Sponsors.sponsor_id

                WHERE Drivers.driver_id = %s
                """,
                (session["user_id"],)
            )

            driver = cursor.fetchone()

    finally:
        conn.close()

    if not driver:
        return redirect(url_for("logout"))

    return render_template(
        "driver/dashboard.html",
        first_name=session["first_name"],
        role=session["role"],
        driver=driver
    )
@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        role = request.form["role"]
        first_name = request.form["first_name"].strip()
        last_name = request.form["last_name"].strip()
        email = request.form["email"].strip().lower()

        password = request.form["password"]
        confirm_password = request.form["confirm_password"]

        company_name = request.form.get("company_name", "").strip()
        phone = request.form.get("phone", "").strip()


        # ----------------------------
        # Validate account type
        # ----------------------------

        if role not in ["Driver", "Sponsor"]:
            return render_template(
                "shared/register.html",
                error="Please select a valid account type."
            )


        # ----------------------------
        # Validate passwords
        # ----------------------------

        if password != confirm_password:
            return render_template(
                "shared/register.html",
                error="Passwords do not match."
            )


        # ----------------------------
        # Password requirements
        # ----------------------------

        if len(password) < 8:
            return render_template(
                "shared/register.html",
                error="Password must be at least 8 characters."
            )


        # ----------------------------
        # Sponsor needs company name
        # ----------------------------

        if role == "Sponsor" and not company_name:
            return render_template(
                "shared/register.html",
                error="Company name is required for Sponsor accounts."
            )


        conn = get_db()

        try:

            with conn.cursor() as cursor:

                # ----------------------------
                # Check if email already exists
                # ----------------------------

                cursor.execute(
                    """
                    SELECT user_id
                    FROM Users
                    WHERE email = %s
                    """,
                    (email,)
                )

                existing_user = cursor.fetchone()

                if existing_user:

                    return render_template(
                        "shared/register.html",
                        error="An account with this email already exists."
                    )


                # ----------------------------
                # Create Users record
                # ----------------------------

                cursor.execute(
                    """
                    INSERT INTO Users
                        (email, first_name, last_name, role)
                    VALUES
                        (%s, %s, %s, %s)
                    """,
                    (
                        email,
                        first_name,
                        last_name,
                        role
                    )
                )


                # Get the new user ID
                user_id = cursor.lastrowid


                # ----------------------------
                # Hash and store password
                # ----------------------------

                password_hash = generate_password_hash(password)

                cursor.execute(
                    """
                    INSERT INTO Password
                        (user_id, password_hash)
                    VALUES
                        (%s, %s)
                    """,
                    (
                        user_id,
                        password_hash
                    )
                )


                # ============================
                # DRIVER ACCOUNT
                # ============================

                if role == "Driver":

                    cursor.execute(
                        """
                        INSERT INTO Drivers
                            (driver_id)
                        VALUES
                            (%s)
                        """,
                        (user_id,)
                    )


                # ============================
                # SPONSOR ACCOUNT
                # ============================

                elif role == "Sponsor":

                    cursor.execute(
                        """
                        INSERT INTO Sponsors
                            (
                                sponsor_id,
                                company_name,
                                email,
                                phone
                            )
                        VALUES
                            (%s, %s, %s, %s)
                        """,
                        (
                            user_id,
                            company_name,
                            email,
                            phone if phone else None
                        )
                    )


                    # Link the user to the sponsor
                    cursor.execute(
                        """
                        INSERT INTO Sponsor_Users
                            (user_id, sponsor_id)
                        VALUES
                            (%s, %s)
                        """,
                        (
                            user_id,
                            user_id
                        )
                    )


            # Everything worked
            conn.commit()


        except Exception as e:

            conn.rollback()

            print("Registration error:", e)

            return render_template(
                "shared/register.html",
                error="There was a problem creating your account."
            )


        finally:

            conn.close()


        # Account successfully created
        return redirect(url_for("login"))


    # GET request
    return render_template("shared/register.html")

if __name__ == "__main__":
    app.run(debug=True)

