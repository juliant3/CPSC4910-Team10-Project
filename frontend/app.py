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
            cursor.execute(
                "SELECT * FROM Project_Metadata ORDER BY metadata_id DESC LIMIT 1"
            )
            metadata = cursor.fetchone()
    finally:
        conn.close()
    return render_template("shared/about.html", metadata=metadata)

if __name__ == "__main__":
    app.run(debug=True)
