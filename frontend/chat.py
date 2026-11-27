"""Chat between drivers and sponsors, and between sponsors and admins.

There is one conversation per driver and sponsor organization; everyone on the
sponsor's team shares the sponsor side. Only drivers in a sponsor's program
(an Active Driver_Sponsors row) can message with that sponsor. The page asks
for new messages every few seconds instead of keeping a live connection open.
"""

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for
from services.audit_service import queue_event

chat_bp = Blueprint("chat", __name__)
MAX_MESSAGE_LENGTH = 2000
SHOWN_MESSAGES = 200


def _website():
    # Imported during requests only; app.py registers this blueprint before
    # it defines get_db and current_sponsor_id.
    import app as website
    return website


def _side():
    """('driver', driver_id) or ('sponsor', sponsor_id) for the logged-in user, else None."""
    if "user_id" not in session:
        return None
    role = (session.get("role") or "").lower()
    if role == "driver":
        return "driver", session["user_id"]
    if role == "sponsor":
        return "sponsor", _website().current_sponsor_id()
    return None


def _support_view():
    """A sponsor or admin looking through a driver's account: read, but never mark read."""
    return session.get("assumed_driver_id") is not None


def _unread_from_other_side(side):
    """SQL condition for messages the given side hasn't read yet."""
    return ("Messages.sender_user_id <> Messages.driver_id" if side == "driver"
            else "Messages.sender_user_id = Messages.driver_id")


def count_unread_messages(cursor):
    """Unread messages for the navbar badge."""
    me = _side()
    if me is None:
        return 0
    side, my_id = me
    column = "driver_id" if side == "driver" else "sponsor_id"
    cursor.execute(
        f"""
        SELECT COUNT(*) AS c
        FROM Messages
        WHERE Messages.{column} = %s
          AND Messages.read_at IS NULL
          AND {_unread_from_other_side(side)}
        """,
        (my_id,),
    )
    row = cursor.fetchone()
    return row["c"] if row else 0


def _conversation(cursor, other_id):
    """The conversation with other_id, or None if the two aren't in a program together."""
    side, my_id = _side()
    driver_id, sponsor_id = (my_id, other_id) if side == "driver" else (other_id, my_id)

    # A sponsor looking through a driver's account only sees its own conversation.
    locked = session.get("assumed_driver_sponsor_id")
    if locked is not None and sponsor_id != locked:
        return None

    cursor.execute(
        """
        SELECT
            Driver_Sponsors.status,
            Users.first_name,
            Users.last_name,
            Sponsors.company_name
        FROM Driver_Sponsors
        JOIN Users
            ON Users.user_id = Driver_Sponsors.driver_id
        JOIN Sponsors
            ON Sponsors.sponsor_id = Driver_Sponsors.sponsor_id
        WHERE Driver_Sponsors.driver_id = %s
          AND Driver_Sponsors.sponsor_id = %s
        """,
        (driver_id, sponsor_id),
    )
    row = cursor.fetchone()
    if not row:
        return None

    driver_name = f"{row['first_name']} {row['last_name'] or ''}".strip()
    return {
        "side": side,
        "other_id": other_id,
        "driver_id": driver_id,
        "sponsor_id": sponsor_id,
        "title": row["company_name"] if side == "driver" else driver_name,
        "can_send": row["status"] == "Active",
    }


def _messages(cursor, convo, after=0):
    cursor.execute(
        """
        SELECT
            Messages.message_id,
            Messages.body,
            Messages.created_at,
            Messages.sender_user_id,
            Users.first_name,
            Users.last_name
        FROM Messages
        JOIN Users
            ON Users.user_id = Messages.sender_user_id
        WHERE Messages.driver_id = %s
          AND Messages.sponsor_id = %s
          AND Messages.message_id > %s
        ORDER BY Messages.message_id DESC
        LIMIT %s
        """,
        (convo["driver_id"], convo["sponsor_id"], after, SHOWN_MESSAGES),
    )
    rows = list(reversed(cursor.fetchall()))
    local = _website().to_local_time
    for row in rows:
        from_driver = row["sender_user_id"] == convo["driver_id"]
        row["mine"] = from_driver == (convo["side"] == "driver")
        row["sender"] = f"{row['first_name']} {row['last_name'] or ''}".strip()
        row["time"] = local(row["created_at"]).strftime("%b %d, %I:%M %p") if row["created_at"] else ""
    return rows


def _mark_read(cursor, convo):
    if _support_view():
        return
    cursor.execute(
        f"""
        UPDATE Messages
        SET read_at = NOW()
        WHERE driver_id = %s
          AND sponsor_id = %s
          AND read_at IS NULL
          AND {_unread_from_other_side(convo["side"])}
        """,
        (convo["driver_id"], convo["sponsor_id"]),
    )


def _notify(cursor, convo):
    """One notification per batch of unread messages, so a busy chat doesn't flood the bell."""
    cursor.execute(
        f"""
        SELECT COUNT(*) AS c
        FROM Messages
        WHERE driver_id = %s
          AND sponsor_id = %s
          AND read_at IS NULL
          AND {_unread_from_other_side("sponsor" if convo["side"] == "driver" else "driver")}
        """,
        (convo["driver_id"], convo["sponsor_id"]),
    )
    row = cursor.fetchone()
    if row and row["c"] > 1:
        return  # they already have an unread message from this conversation

    sender = f"{session.get('first_name', '')} {session.get('last_name', '')}".strip()
    if convo["side"] == "driver":
        cursor.execute(
            """
            INSERT INTO Notifications
                (user_id, notification_type, subject, message)
            SELECT members.user_id, 'Message', %s, %s
            FROM (
                -- Everyone on the sponsor's team, plus the org's founding user
                SELECT user_id FROM Sponsor_Users WHERE sponsor_id = %s
                UNION
                SELECT %s
            ) AS members
            """,
            ("New message from a driver", f"{sender} sent you a message.",
             convo["sponsor_id"], convo["sponsor_id"]),
        )
    else:
        cursor.execute(
            """
            INSERT INTO Notifications
                (user_id, notification_type, subject, message)
            VALUES (%s, 'Message', %s, %s)
            """,
            (convo["driver_id"], "New message from your sponsor", f"{sender} sent you a message."),
        )


def _require_side():
    if "user_id" not in session:
        return redirect(url_for("login"))
    if _side() is None:
        return redirect(url_for("home"))
    return None


@chat_bp.route("/messages")
def inbox():
    redirect_response = _require_side()
    if redirect_response is not None:
        return redirect_response

    side, my_id = _side()
    if side == "driver":
        other = "Driver_Sponsors.sponsor_id"
        name = "Sponsors.company_name"
        where = "Driver_Sponsors.driver_id = %s"
    else:
        other = "Driver_Sponsors.driver_id"
        name = "CONCAT_WS(' ', Users.first_name, Users.last_name)"
        where = "Driver_Sponsors.sponsor_id = %s"

    locked = session.get("assumed_driver_sponsor_id")
    params = [my_id] + ([locked] if locked is not None else [])

    conn = _website().get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    {other} AS other_id,
                    {name} AS name,
                    (SELECT Messages.body FROM Messages
                     WHERE Messages.driver_id = Driver_Sponsors.driver_id
                       AND Messages.sponsor_id = Driver_Sponsors.sponsor_id
                     ORDER BY Messages.message_id DESC LIMIT 1) AS last_body,
                    (SELECT MAX(Messages.message_id) FROM Messages
                     WHERE Messages.driver_id = Driver_Sponsors.driver_id
                       AND Messages.sponsor_id = Driver_Sponsors.sponsor_id) AS last_id,
                    (SELECT COUNT(*) FROM Messages
                     WHERE Messages.driver_id = Driver_Sponsors.driver_id
                       AND Messages.sponsor_id = Driver_Sponsors.sponsor_id
                       AND Messages.read_at IS NULL
                       AND {_unread_from_other_side(side)}) AS unread
                FROM Driver_Sponsors
                JOIN Users
                    ON Users.user_id = Driver_Sponsors.driver_id
                JOIN Sponsors
                    ON Sponsors.sponsor_id = Driver_Sponsors.sponsor_id
                WHERE {where}
                  AND Driver_Sponsors.status = 'Active'
                  {"AND Driver_Sponsors.sponsor_id = %s" if locked is not None else ""}
                ORDER BY last_id IS NULL, last_id DESC, name
                """,
                tuple(params),
            )
            conversations = cursor.fetchall()
    finally:
        conn.close()

    return render_template("shared/messages.html", conversations=conversations, side=side)


@chat_bp.route("/messages/<int:other_id>", methods=["GET", "POST"])
def conversation(other_id):
    redirect_response = _require_side()
    if redirect_response is not None:
        return redirect_response

    conn = _website().get_db()
    try:
        with conn.cursor() as cursor:
            convo = _conversation(cursor, other_id)
            if convo is None:
                abort(404)

            if request.method == "POST":
                body = request.form.get("body", "").strip()
                if not body:
                    flash("Type a message before sending.", "error")
                elif len(body) > MAX_MESSAGE_LENGTH:
                    flash(f"Messages can be up to {MAX_MESSAGE_LENGTH:,} characters.", "error")
                elif not convo["can_send"]:
                    flash("You can only message drivers and sponsors in an active program.", "error")
                else:
                    cursor.execute(
                        """
                        INSERT INTO Messages
                            (driver_id, sponsor_id, sender_user_id, body)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (convo["driver_id"], convo["sponsor_id"], session["user_id"], body),
                    )
                    _notify(cursor, convo)
                    conn.commit()
                    queue_event("Communication", "Message sent", status="Sent", success=True,
                                sponsor_id=convo["sponsor_id"], driver_id=convo["driver_id"])
                return redirect(url_for("chat.conversation", other_id=other_id) + "#latest")

            messages = _messages(cursor, convo)
            _mark_read(cursor, convo)
        conn.commit()
    finally:
        conn.close()

    return render_template(
        "shared/conversation.html", convo=convo, messages=messages,
        max_length=MAX_MESSAGE_LENGTH,
    )


@chat_bp.route("/messages/<int:other_id>/new")
def new_messages(other_id):
    """Messages after ?after=<message_id>, for the page's polling."""
    if _side() is None:
        abort(403)
    try:
        after = max(0, int(request.args.get("after", "0")))
    except ValueError:
        after = 0

    conn = _website().get_db()
    try:
        with conn.cursor() as cursor:
            convo = _conversation(cursor, other_id)
            if convo is None:
                abort(404)
            messages = _messages(cursor, convo, after)
            if messages:
                _mark_read(cursor, convo)
        conn.commit()
    finally:
        conn.close()

    return jsonify(messages=[
        {"id": m["message_id"], "body": m["body"], "mine": m["mine"],
         "sender": m["sender"], "time": m["time"]}
        for m in messages
    ])


# ==========================================
# ADMIN SUPPORT
# Sponsors and drivers can message "the admins". Each sponsor organization
# has one conversation (its whole team shares it, like driver chats) and each
# driver has one. Every admin sees every conversation and any of them can
# reply. A conversation is identified by its kind and id:
# ("sponsor", sponsor_id) or ("driver", driver_id).
# ==========================================

SUPPORT_KINDS = ("sponsor", "driver")


def _support_role():
    """'admin', 'sponsor' or 'driver' for a logged-in user, else None."""
    if "user_id" not in session:
        return None
    role = (session.get("role") or "").lower()
    return role if role in ("admin",) + SUPPORT_KINDS else None


def _own_support_key():
    """(kind, id) of the logged-in sponsor's or driver's own conversation."""
    role = _support_role()
    if role == "sponsor":
        return "sponsor", _website().current_sponsor_id()
    if role == "driver":
        return "driver", session["user_id"]
    return None


def _key_sql(kind):
    """The column that identifies a conversation of this kind."""
    return "sponsor_id" if kind == "sponsor" else "driver_id"


def count_unread_support(cursor):
    """Unread support messages for the navbar badge: everything from sponsors
    and drivers for admins, admin replies for a sponsor or driver."""
    role = _support_role()
    if role == "admin":
        cursor.execute(
            "SELECT COUNT(*) AS c FROM Admin_Messages WHERE from_admin = FALSE AND read_by_admin_at IS NULL"
        )
    elif role in SUPPORT_KINDS:
        kind, party_id = _own_support_key()
        cursor.execute(
            f"""
            SELECT COUNT(*) AS c
            FROM Admin_Messages
            WHERE {_key_sql(kind)} = %s
              AND from_admin = TRUE
              AND read_by_party_at IS NULL
            """,
            (party_id,),
        )
    else:
        return 0
    row = cursor.fetchone()
    return row["c"] if row else 0


def _support_convo(cursor, kind, party_id):
    """The conversation for (kind, party_id), or None if that sponsor or driver doesn't exist."""
    if kind == "sponsor":
        cursor.execute("SELECT company_name AS name FROM Sponsors WHERE sponsor_id = %s", (party_id,))
    else:
        cursor.execute(
            """
            SELECT CONCAT_WS(' ', first_name, last_name) AS name
            FROM Users
            WHERE user_id = %s
              AND role = 'Driver'
            """,
            (party_id,),
        )
    party = cursor.fetchone()
    if not party:
        return None

    is_admin = _support_role() == "admin"
    if is_admin:
        subtitle = f"Every admin can read and reply to this {kind}'s conversation."
    elif kind == "sponsor":
        subtitle = "Message the program administrators. Anyone on your team can read and reply."
    else:
        subtitle = "Message the program administrators about any issue with your account."

    return {
        "side": "admin" if is_admin else kind,
        "kind": kind,
        "party_id": party_id,
        "title": f"{party['name']} ({kind.title()})" if is_admin else "Admin Support",
        "subtitle": subtitle,
        # A sponsor or driver has just the one conversation, so no inbox to go back to.
        "show_back": is_admin,
        "back_url": url_for("chat.admin_inbox") if is_admin else None,
        "poll_url": (url_for("chat.admin_support_new", kind=kind, party_id=party_id) if is_admin
                     else url_for("chat.support_new")),
        "can_send": True,
    }


def _support_messages(cursor, convo, after=0):
    cursor.execute(
        f"""
        SELECT
            Admin_Messages.message_id,
            Admin_Messages.body,
            Admin_Messages.created_at,
            Admin_Messages.from_admin,
            Users.first_name,
            Users.last_name
        FROM Admin_Messages
        JOIN Users
            ON Users.user_id = Admin_Messages.sender_user_id
        WHERE Admin_Messages.{_key_sql(convo["kind"])} = %s
          AND Admin_Messages.message_id > %s
        ORDER BY Admin_Messages.message_id DESC
        LIMIT %s
        """,
        (convo["party_id"], after, SHOWN_MESSAGES),
    )
    rows = list(reversed(cursor.fetchall()))
    local = _website().to_local_time
    for row in rows:
        row["mine"] = bool(row["from_admin"]) == (convo["side"] == "admin")
        name = f"{row['first_name']} {row['last_name'] or ''}".strip()
        row["sender"] = f"{name} (Admin)" if row["from_admin"] else name
        row["time"] = local(row["created_at"]).strftime("%b %d, %I:%M %p") if row["created_at"] else ""
    return rows


def _support_mark_read(cursor, convo):
    # Someone looking through another account doesn't read it for them.
    if session.get("assumed_sponsor_id") is not None or session.get("assumed_driver_id") is not None:
        return
    if convo["side"] == "admin":
        column, from_other = "read_by_admin_at", "FALSE"
    else:
        column, from_other = "read_by_party_at", "TRUE"
    cursor.execute(
        f"""
        UPDATE Admin_Messages
        SET {column} = NOW()
        WHERE {_key_sql(convo["kind"])} = %s
          AND from_admin = {from_other}
          AND {column} IS NULL
        """,
        (convo["party_id"],),
    )


def _support_notify(cursor, convo):
    """Ping the other side once per batch of unread messages."""
    from_admin = convo["side"] == "admin"
    read_column = "read_by_party_at" if from_admin else "read_by_admin_at"
    cursor.execute(
        f"""
        SELECT COUNT(*) AS c
        FROM Admin_Messages
        WHERE {_key_sql(convo["kind"])} = %s
          AND from_admin = %s
          AND {read_column} IS NULL
        """,
        (convo["party_id"], from_admin),
    )
    row = cursor.fetchone()
    if row and row["c"] > 1:
        return  # they already have an unread message here

    subject = "New message from an admin"
    reply = "An administrator replied to your support conversation."

    if from_admin and convo["kind"] == "sponsor":
        cursor.execute(
            """
            INSERT INTO Notifications
                (user_id, notification_type, subject, message)
            SELECT members.user_id, 'Message', %s, %s
            FROM (
                SELECT user_id FROM Sponsor_Users WHERE sponsor_id = %s
                UNION
                SELECT %s
            ) AS members
            """,
            (subject, reply, convo["party_id"], convo["party_id"]),
        )
    elif from_admin:
        cursor.execute(
            """
            INSERT INTO Notifications
                (user_id, notification_type, subject, message)
            VALUES (%s, 'Message', %s, %s)
            """,
            (convo["party_id"], subject, reply),
        )
    else:
        sender = f"{session.get('first_name', '')} {session.get('last_name', '')}".strip()
        cursor.execute(
            """
            INSERT INTO Notifications
                (user_id, notification_type, subject, message)
            SELECT user_id, 'Message', %s, %s
            FROM Users
            WHERE role = 'Admin'
              AND is_active = TRUE
            """,
            (f"New {convo['kind']} message", f"{sender} ({convo['kind']}) sent the admins a message."),
        )


def _support_page(kind, party_id):
    """Shared GET/POST handling for both sides of a support conversation."""
    conn = _website().get_db()
    try:
        with conn.cursor() as cursor:
            convo = _support_convo(cursor, kind, party_id)
            if convo is None:
                abort(404)

            if request.method == "POST":
                body = request.form.get("body", "").strip()
                if not body:
                    flash("Type a message before sending.", "error")
                elif len(body) > MAX_MESSAGE_LENGTH:
                    flash(f"Messages can be up to {MAX_MESSAGE_LENGTH:,} characters.", "error")
                else:
                    cursor.execute(
                        f"""
                        INSERT INTO Admin_Messages
                            ({_key_sql(kind)}, sender_user_id, from_admin, body)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (party_id, session["user_id"], convo["side"] == "admin", body),
                    )
                    _support_notify(cursor, convo)
                    conn.commit()
                    queue_event("Communication", "Support message sent", status="Sent", success=True,
                                **{_key_sql(kind): party_id})
                return redirect(request.path + "#latest")

            messages = _support_messages(cursor, convo)
            _support_mark_read(cursor, convo)
        conn.commit()
    finally:
        conn.close()

    return render_template(
        "shared/conversation.html", convo=convo, messages=messages,
        max_length=MAX_MESSAGE_LENGTH,
    )


def _support_poll(kind, party_id):
    try:
        after = max(0, int(request.args.get("after", "0")))
    except ValueError:
        after = 0

    conn = _website().get_db()
    try:
        with conn.cursor() as cursor:
            convo = _support_convo(cursor, kind, party_id)
            if convo is None:
                abort(404)
            messages = _support_messages(cursor, convo, after)
            if messages:
                _support_mark_read(cursor, convo)
        conn.commit()
    finally:
        conn.close()

    return jsonify(messages=[
        {"id": m["message_id"], "body": m["body"], "mine": m["mine"],
         "sender": m["sender"], "time": m["time"]}
        for m in messages
    ])


@chat_bp.route("/support", methods=["GET", "POST"])
def support():
    """A sponsor's or driver's conversation with the admins."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    key = _own_support_key()
    if key is None:
        return redirect(url_for("home"))
    return _support_page(*key)


@chat_bp.route("/support/new")
def support_new():
    key = _own_support_key()
    if key is None:
        abort(403)
    return _support_poll(*key)


@chat_bp.route("/admin/messages")
def admin_inbox():
    """Every support conversation, newest first. Shared by all admins. Every
    active sponsor is listed so an admin can start one; drivers appear once
    they've written (an admin can start one from the Drivers page)."""
    if "user_id" not in session:
        return redirect(url_for("login"))
    if _support_role() != "admin":
        abort(403)

    def summary(key_column):
        return f"""
            (SELECT body FROM Admin_Messages
             WHERE Admin_Messages.{key_column} = party.id
             ORDER BY message_id DESC LIMIT 1) AS last_body,
            (SELECT MAX(message_id) FROM Admin_Messages
             WHERE Admin_Messages.{key_column} = party.id) AS last_id,
            (SELECT COUNT(*) FROM Admin_Messages
             WHERE Admin_Messages.{key_column} = party.id
               AND from_admin = FALSE
               AND read_by_admin_at IS NULL) AS unread
        """

    conn = _website().get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT * FROM (
                    SELECT 'sponsor' AS kind, party.id AS other_id, party.name, {summary("sponsor_id")}
                    FROM (SELECT sponsor_id AS id, company_name AS name
                          FROM Sponsors WHERE is_active = TRUE) AS party
                    UNION ALL
                    SELECT 'driver' AS kind, party.id AS other_id, party.name, {summary("driver_id")}
                    FROM (SELECT DISTINCT Users.user_id AS id,
                                 CONCAT_WS(' ', Users.first_name, Users.last_name) AS name
                          FROM Admin_Messages
                          JOIN Users ON Users.user_id = Admin_Messages.driver_id) AS party
                ) AS conversations
                ORDER BY last_id IS NULL, last_id DESC, name
                """
            )
            conversations = cursor.fetchall()
    finally:
        conn.close()

    for c in conversations:
        c["url"] = url_for("chat.admin_support", kind=c["kind"], party_id=c["other_id"])
        c["name"] = f"{c['name']} ({c['kind'].title()})"

    return render_template("shared/messages.html", conversations=conversations, side="admin")


@chat_bp.route("/admin/messages/<kind>/<int:party_id>", methods=["GET", "POST"])
def admin_support(kind, party_id):
    if "user_id" not in session:
        return redirect(url_for("login"))
    if _support_role() != "admin":
        abort(403)
    if kind not in SUPPORT_KINDS:
        abort(404)
    return _support_page(kind, party_id)


@chat_bp.route("/admin/messages/<kind>/<int:party_id>/new")
def admin_support_new(kind, party_id):
    if _support_role() != "admin":
        abort(403)
    if kind not in SUPPORT_KINDS:
        abort(404)
    return _support_poll(kind, party_id)
