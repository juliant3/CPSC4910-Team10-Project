import pymysql
from werkzeug.security import generate_password_hash
import getpass


DB_CONFIG = {
    "host": "cpsc4910-f26.cobd8enwsupz.us-east-1.rds.amazonaws.com",
    "user": "Team10",
    "password": "CPSC4910TEAM10",
    "database": "Team10_DB",
}


def add_user(email, first_name, last_name, role, password):
    conn = pymysql.connect(**DB_CONFIG)

    try:
        with conn.cursor() as cursor:

            # Normalize input
            email = email.strip().lower()
            role = role.strip().capitalize()

            if role not in ("Admin", "Driver", "Sponsor"):
                raise ValueError(
                    "Role must be Admin, Driver, or Sponsor."
                )

            # Create the base user
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

            user_id = cursor.lastrowid

            # Create password
            password_hash = generate_password_hash(
                password,
                method="pbkdf2:sha256"
            )

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

            # -----------------------------
            # Driver-specific record
            # -----------------------------
            if role == "Driver":

                cursor.execute(
                    """
                    INSERT INTO Drivers
                        (driver_id, points_balance, sponsor_id, status)
                    VALUES
                        (%s, 0, NULL, 'Applicant')
                    """,
                    (user_id,)
                )

            # -----------------------------
            # Sponsor-specific record
            # -----------------------------
            elif role == "Sponsor":

                company_name = input("Company name: ").strip()
                sponsor_email = email
                phone = input("Phone (optional): ").strip()

                cursor.execute(
                    """
                    INSERT INTO Sponsors
                        (sponsor_id, company_name, email, phone)
                    VALUES
                        (%s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        company_name,
                        sponsor_email,
                        phone
                    )
                )

            conn.commit()

            print(
                f"User '{email}' created successfully "
                f"with ID {user_id}."
            )

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


if __name__ == "__main__":

    email = input("Email: ")
    first_name = input("First name: ")
    last_name = input("Last name: ")
    role = input("Role (Admin/Driver/Sponsor): ")
    password = getpass.getpass("Password: ")

    add_user(
        email,
        first_name,
        last_name,
        role,
        password
    )
