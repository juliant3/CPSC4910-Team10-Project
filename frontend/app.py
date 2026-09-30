from flask import Flask, render_template, request, redirect, url_for, session
import pymysql
import os
import smtplib
import secrets
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

def send_acceptance_email(email, first_name, temp_password):
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_from = os.environ.get("SMTP_FROM", smtp_username)

    login_link = "https://cpsc4910team10truckingproject.org/login"

    if not smtp_host or not smtp_username or not smtp_password:
        print(f"[ACCEPTANCE] {email} temp password: {temp_password}")
        return True

    message = EmailMessage()
    message["Subject"] = "Your Drivers Advantage application was accepted!"
    message["From"] = smtp_from
    message["To"] = email
    message.set_content(
        f"Hi {first_name},\n\n"
        f"Congratulations! Your application has been accepted.\n\n"
        f"Log in here: {login_link}\n"
        f"Email: {email}\n"
        f"Temporary password: {temp_password}\n\n"
        "Please log in and change your password as soon as possible."
    )

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(message)
        return True
    except Exception as e:
        print(f"Failed to send acceptance email: {e}")
        return False


def send_rejection_email(email, first_name):
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_from = os.environ.get("SMTP_FROM", smtp_username)

    if not smtp_host or not smtp_username or not smtp_password:
        print(f"[REJECTION] Notified {email}")
        return True

    message = EmailMessage()
    message["Subject"] = "Your Drivers Advantage application"
    message["From"] = smtp_from
    message["To"] = email
    message.set_content(
        f"Hi {first_name or ''},\n\n"
        "Thank you for your interest in Drivers Advantage. "
        "After review, we're unable to move forward with your application at this time."
    )

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(message)
        return True
    except Exception as e:
        print(f"Failed to send rejection email: {e}")
        return False
    


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
            # Load sponsor's applications.
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

                ORDER BY Driver_Applications.application_date DESC
                """,
                (session["user_id"],)
            )

            applications = cursor.fetchall()

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
                (session["user_id"],)
            )

            questions = cursor.fetchall()

    finally:
        conn.close()

    return render_template(
        "sponsor/applications.html",
        first_name=session["first_name"],
        role=session["role"],
        applications=applications,
        questions=questions
    )
@app.route("/sponsor/questions", methods=["POST"])
def sponsor_questions():

    if "user_id" not in session:
        return redirect(url_for("login"))

    if session["role"].lower() != "sponsor":
        return redirect(url_for("home"))

    sponsor_id = session["user_id"]
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
                (application_id, session["user_id"])
            )

            application = cursor.fetchone()

            if not application or application["status"] != "Submitted":
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

            # Create the Drivers record.
            cursor.execute(
                """
                INSERT INTO Drivers
                    (driver_id, points_balance, sponsor_id, status)
                VALUES
                    (%s, 0, %s, 'Active')
                """,
                (user_id, session["user_id"])
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
                (application_id, session["user_id"])
            )

            application = cursor.fetchone()

            if not application or application["status"] != "Submitted":
                return redirect(url_for("sponsor_applications"))

            cursor.execute(
                """
                UPDATE Driver_Applications
                SET
                    status = 'Rejected',
                    decision_date = NOW(),
                    decision_by_user_id = %s
                WHERE application_id = %s
                """,
                (session["user_id"], application_id)
            )

            conn.commit()

            send_rejection_email(
                application["applicant_email"],
                application["applicant_first_name"]
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
                (question_id, session["user_id"])
            )
        conn.commit()
    finally:
        conn.close()

    return redirect(url_for("sponsor_applications"))

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
        invite_code = request.form.get("invite_code", "").strip()


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
                #-----------------------------
                # Validate driver invite code
                #-----------------------------
                sponsor = None
                if role == "Driver": 
                    if not invite_code: 
                        return render_template(
                            "shared/register.html",
                             error="A Sponsor invite code is required for Driver accounts."
                        )
                    cursor.execute(
                        """
                        SELECT sponsor_id
                        FROM Sponsors
                        WHERE invite_code = %s
                        AND is_active = True
                        """, 
                        (invite_code,)
                    )
                    sponsor = cursor.fetchone()
                    if not sponsor: 
                        return render_template(
                            "shared/register.html", 
                            error = "Invalid sponsor invite code."
                        )

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

                password_hash = generate_password_hash(password, method="pbkdf2:sha256")

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
                            (driver_id, sponsor_id)
                        VALUES
                            (%s, %s)
                        """,
                        (user_id,
                        sponsor["sponsor_id"])
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

