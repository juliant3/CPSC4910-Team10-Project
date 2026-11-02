"""Chat between drivers and sponsors.

There is one conversation per driver and sponsor organization; everyone on the
sponsor's team shares the sponsor side. Only drivers in a sponsor's program
(an Active Driver_Sponsors row) can message with that sponsor. The page asks
for new messages every few seconds instead of keeping a live connection open.
"""

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for

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
