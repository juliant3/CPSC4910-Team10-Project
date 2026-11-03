
"""Bulk upload for Drivers Advantage."""

import io
import re
import secrets


def process_bulk_upload(
    stream, conn, role, actor_id, sponsor_id,
    create_password_token, unusable_password_hash,
    send_setup_email
):
    role = role.lower()

    if role not in ("admin", "sponsor"):
        raise PermissionError("Only admins and sponsors can upload.")

    if role == "sponsor" and not sponsor_id:
        raise PermissionError("No sponsor organization found.")

    results = []
    summary = {
        "processed": 0,
        "successful": 0,
        "skipped": 0,
        "warnings": 0
    }

    def report(line, status, message):
        results.append({
            "line": line,
            "status": status,
            "message": message
        })
        summary["processed"] += 1
        summary[status if status != "warning" else "warnings"] += 1

    def find_organization(cursor, name):
        cursor.execute(
            "SELECT sponsor_id FROM Sponsors WHERE company_name = %s",
            (name,)
        )
        organizations = cursor.fetchall()

        if not organizations:
            raise ValueError("Organization does not exist.")

        if len(organizations) > 1:
            raise ValueError("Organization name is ambiguous.")

        return organizations[0]["sponsor_id"]

    reader = io.TextIOWrapper(
        stream,
        encoding="utf-8-sig",
        errors="strict",
        newline=""
    )

    try:
        for line_number, raw_line in enumerate(reader, 1):
            if not raw_line.strip():
                continue

            setup_email = None
            warning = None

            try:
                fields = [
                    field.strip()
                    for field in raw_line.rstrip("\r\n").split("|")
                ]

                record_type = fields[0].upper()

                if record_type not in ("O", "D", "S"):
                    raise ValueError("Unknown record type.")

                # ORGANIZATION
                if record_type == "O":
                    if role != "admin":
                        raise ValueError(
                            "Only admins can create organizations."
                        )

                    if len(fields) != 2 or not fields[1]:
                        raise ValueError(
                            "Format: O|Organization Name"
                        )

                    organization_name = fields[1]

                    if len(organization_name) > 255:
                        raise ValueError("Organization name is too long.")

                    with conn.cursor() as cursor:
                        cursor.execute(
                            """
                            SELECT sponsor_id
                            FROM Sponsors
                            WHERE company_name = %s
                            """,
                            (organization_name,)
                        )

                        if cursor.fetchone():
                            raise ValueError(
                                "Organization already exists."
                            )

                        placeholder_email = (
                            "org-"
                            + secrets.token_hex(16)
                            + "@invalid.example"
                        )

                        cursor.execute(
                            """
                            INSERT INTO Users
                                (email, first_name, last_name,
                                 role, is_active)
                            VALUES
                                (%s, %s, %s, 'Sponsor', FALSE)
                            """,
                            (
                                placeholder_email,
                                "Organization",
                                "Placeholder"
                            )
                        )

                        organization_id = cursor.lastrowid

                        cursor.execute(
                            """
                            INSERT INTO Password
                                (user_id, password_hash)
                            VALUES (%s, %s)
                            """,
                            (
                                organization_id,
                                unusable_password_hash()
                            )
                        )

                        cursor.execute(
                            """
                            INSERT INTO Sponsors
                                (sponsor_id, company_name)
                            VALUES (%s, %s)
                            """,
                            (
                                organization_id,
                                organization_name
                            )
                        )

                    conn.commit()

                    report(
                        line_number,
                        "successful",
                        f"Organization created: {organization_name}"
                    )
                    continue

                # DRIVER / SPONSOR
                if not 5 <= len(fields) <= 7:
                    raise ValueError(
                        "Driver/Sponsor records need 5–7 fields."
                    )

                fields += [""] * (7 - len(fields))

                (
                    _, organization, first_name, last_name,
                    email, points_text, reason
                ) = fields

                if not first_name or not last_name or not email:
                    raise ValueError(
                        "First name, last name, and email are required."
                    )

                if (
                    len(first_name) > 100
                    or len(last_name) > 100
                    or len(email) > 255
                ):
                    raise ValueError("Name or email is too long.")

                if not re.fullmatch(
                    r"[^\s@]+@[^\s@]+\.[^\s@]+",
                    email
                ):
                    raise ValueError("Invalid email address.")

                if role == "sponsor":
                    if organization:
                        raise ValueError(
                            "Sponsors must leave organization blank."
                        )
                    target_sponsor_id = sponsor_id
                else:
                    if not organization:
                        raise ValueError(
                            "Admins must specify an organization."
                        )

                points = 0

                if record_type == "D":
                    if points_text:
                        try:
                            points = int(points_text)
                        except ValueError:
                            raise ValueError(
                                "Points must be a whole number."
                            )

                        if not reason:
                            raise ValueError(
                                "A reason is required for point changes."
                            )

                    if len(reason) > 255:
                        raise ValueError("Reason is too long.")

                else:
                    if points_text or reason:
                        warning = (
                            "Points/reason ignored for sponsor record."
                        )

                with conn.cursor() as cursor:
                    if role == "admin":
                        target_sponsor_id = find_organization(
                            cursor, organization
                        )

                    cursor.execute(
                        """
                        SELECT user_id, role
                        FROM Users
                        WHERE email = %s
                        FOR UPDATE
                        """,
                        (email,)
                    )

                    existing_user = cursor.fetchone()

                    expected_role = (
                        "driver" if record_type == "D"
                        else "sponsor"
                    )

                    if existing_user:
                        if existing_user["role"].lower() != expected_role:
                            raise ValueError(
                                "Email belongs to another account role."
                            )

                        user_id = existing_user["user_id"]

                    else:
                        cursor.execute(
                            """
                            INSERT INTO Users
                                (email, first_name, last_name, role)
                            VALUES (%s, %s, %s, %s)
                            """,
                            (
                                email,
                                first_name,
                                last_name,
                                "Driver" if record_type == "D"
                                else "Sponsor"
                            )
                        )

                        user_id = cursor.lastrowid

                        cursor.execute(
                            """
                            INSERT INTO Password
                                (user_id, password_hash)
                            VALUES (%s, %s)
                            """,
                            (
                                user_id,
                                unusable_password_hash()
                            )
                        )

                        token = create_password_token(
                            cursor, user_id, "Setup"
                        )

                        setup_email = (
                            email, first_name, token
                        )

                    if record_type == "S":
                        cursor.execute(
                            """
                            SELECT sponsor_id
                            FROM Sponsor_Users
                            WHERE user_id = %s
                            """,
                            (user_id,)
                        )

                        membership = cursor.fetchone()

                        if membership:
                            if membership["sponsor_id"] != target_sponsor_id:
                                raise ValueError(
                                    "Sponsor user belongs to another organization."
                                )
                        else:
                            cursor.execute(
                                """
                                INSERT INTO Sponsor_Users
                                    (user_id, sponsor_id)
                                VALUES (%s, %s)
                                """,
                                (user_id, target_sponsor_id)
                            )

                    else:
                        cursor.execute(
                            """
                            SELECT driver_id
                            FROM Drivers
                            WHERE driver_id = %s
                            """,
                            (user_id,)
                        )

                        if not cursor.fetchone():
                            cursor.execute(
                                """
                                INSERT INTO Drivers
                                    (driver_id, sponsor_id, status)
                                VALUES (%s, %s, 'Active')
                                """,
                                (user_id, target_sponsor_id)
                            )

                        cursor.execute(
                            """
                            SELECT points_balance
                            FROM Driver_Sponsors
                            WHERE driver_id = %s
                              AND sponsor_id = %s
                            FOR UPDATE
                            """,
                            (user_id, target_sponsor_id)
                        )

                        membership = cursor.fetchone()

                        current_balance = (
                            membership["points_balance"]
                            if membership else 0
                        )

                        if current_balance + points < 0:
                            raise ValueError(
                                "Point change would make balance negative."
                            )

                        if membership:
                            cursor.execute(
                                """
                                UPDATE Driver_Sponsors
                                SET status = 'Active',
                                    points_balance = points_balance + %s
                                WHERE driver_id = %s
                                  AND sponsor_id = %s
                                """,
                                (
                                    points,
                                    user_id,
                                    target_sponsor_id
                                )
                            )
                        else:
                            cursor.execute(
                                """
                                INSERT INTO Driver_Sponsors
                                    (driver_id, sponsor_id,
                                     status, points_balance)
                                VALUES (%s, %s, 'Active', %s)
                                """,
                                (
                                    user_id,
                                    target_sponsor_id,
                                    points
                                )
                            )

                        cursor.execute(
                            """
                            UPDATE Drivers
                            SET status = 'Active'
                            WHERE driver_id = %s
                            """,
                            (user_id,)
                        )

                        if points != 0:
                            cursor.execute(
                                """
                                INSERT INTO Point_Transactions
                                    (driver_id, sponsor_id,
                                     changed_by_user_id,
                                     point_change, reason)
                                VALUES (%s, %s, %s, %s, %s)
                                """,
                                (
                                    user_id,
                                    target_sponsor_id,
                                    actor_id,
                                    points,
                                    reason
                                )
                            )

                conn.commit()

                if setup_email:
                    try:
                        if send_setup_email(*setup_email) is False:
                            raise RuntimeError("Email delivery failed.")
                    except Exception:
                        warning = (
                            (warning + "; " if warning else "")
                            + "Account saved, but setup email failed."
                        )

                report(
                    line_number,
                    "warning" if warning else "successful",
                    warning or f"Imported {email}"
                )

            except ValueError as error:
                conn.rollback()
                report(line_number, "skipped", str(error))

            except Exception:
                conn.rollback()
                report(
                    line_number,
                    "skipped",
                    "Database error; record rolled back."
                )

    finally:
        reader.detach()

    return {
        "summary": summary,
        "results": results
    }
