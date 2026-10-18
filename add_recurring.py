import os

APP = "frontend/app.py"
TEMPLATE = "frontend/templates/sponsor/recurring.html"

CODE = r'''def advance_run(next_run, frequency, now):
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
                       frequency, next_run
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
                    SELECT driver_id FROM Drivers
                    WHERE sponsor_id = %s AND status = 'Active'
                    """,
                    (rule["sponsor_id"],)
                )

                for d in cursor.fetchall():

                    cursor.execute(
                        """
                        UPDATE Drivers
                        SET points_balance = points_balance + %s
                        WHERE driver_id = %s
                        """,
                        (rule["point_change"], d["driver_id"])
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
                        (recurring_id, session["user_id"])
                    )
                    conn.commit()
                    return redirect(url_for("sponsor_recurring"))

                if action == "delete" and recurring_id:
                    cursor.execute(
                        """
                        DELETE FROM Recurring_Points
                        WHERE recurring_id = %s AND sponsor_id = %s
                        """,
                        (recurring_id, session["user_id"])
                    )
                    conn.commit()
                    return redirect(url_for("sponsor_recurring"))

                points = request.form.get("point_change", type=int)
                reason = request.form.get("reason", "").strip()[:255]
                frequency = request.form.get("frequency", "")
                start_date = request.form.get("start_date", "")

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
                else:
                    cursor.execute(
                        """
                        INSERT INTO Recurring_Points
                            (sponsor_id, point_change, reason, frequency, next_run)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (session["user_id"], points, reason, frequency, start_date)
                    )
                    conn.commit()
                    return redirect(url_for("sponsor_recurring"))

            cursor.execute(
                """
                SELECT recurring_id, point_change, reason, frequency,
                       next_run, last_run, is_active
                FROM Recurring_Points
                WHERE sponsor_id = %s
                ORDER BY recurring_id DESC
                """,
                (session["user_id"],)
            )
            rules = cursor.fetchall()

    finally:
        conn.close()

    return render_template(
        "sponsor/recurring.html",
        first_name=session["first_name"],
        role=session["role"],
        rules=rules,
        error=error
    )


'''

PAGE = r'''{% extends "shared/base.html" %}

{% block title %}Recurring Points{% endblock %}

{% block head %}
<link rel="stylesheet" href="{{ url_for('static', filename='css/home.css') }}">
{% endblock %}

{% block content %}

<nav class="navbar">
    <a href="{{ url_for('home') }}" class="logo" style="text-decoration: none;">
        Good Driver Program
    </a>

    <div class="nav-links">
        <a href="{{ url_for('home') }}">Home</a>
        <a href="{{ url_for('sponsor_applications') }}">Applications</a>
        <a href="{{ url_for('sponsor_drivers') }}">My Drivers</a>
        <a href="{{ url_for('sponsor_recurring') }}">Recurring Points</a>
        <a href="{{ url_for('about') }}">About</a>
    </div>

    <a href="{{ url_for('logout') }}"
       class="logout-btn"
       onclick="return confirm('Are you sure you want to log out?');">
        Logout
    </a>
</nav>

<div class="hero" style="max-width: 780px;">
    <span class="role-badge">Sponsor</span>

    <h1>Recurring Points</h1>
    <p>Automatically give points to all of your active drivers on a schedule.</p>

    {% if error %}
    <p style="color: #A3413A;"><strong>{{ error }}</strong></p>
    {% endif %}

    <form method="POST" style="display: flex; flex-direction: column; gap: 10px; text-align: left; margin-top: 16px;">
        <input type="hidden" name="action" value="create">

        <label>Points per driver
            <input type="number" name="point_change" min="1" required style="width: 100%;">
        </label>

        <label>Reason
            <input type="text" name="reason" maxlength="255" required style="width: 100%;">
        </label>

        <label>Frequency
            <select name="frequency" style="width: 100%;">
                <option value="Daily">Daily</option>
                <option value="Weekly" selected>Weekly</option>
                <option value="Monthly">Monthly</option>
            </select>
        </label>

        <label>First award date
            <input type="date" name="start_date" required style="width: 100%;">
        </label>

        <button type="submit" class="dashboard-button">Create Recurring Points</button>
    </form>
</div>

<div class="hero" style="max-width: 780px;">
    <h1>Your Schedules</h1>

    {% if rules %}
        {% for r in rules %}
        <div class="application-card" style="flex-direction: column; align-items: stretch; text-align: left;">
            <h3>+{{ r.point_change }} points {{ r.frequency|lower }}</h3>
            <p>{{ r.reason }}</p>
            <p style="font-size: 12px; color: #6B6478;">
                {% if r.is_active %}Next: {{ r.next_run.strftime('%b %d, %Y') }}{% else %}Paused{% endif %}
                {% if r.last_run %} &middot; Last: {{ r.last_run.strftime('%b %d, %Y') }}{% endif %}
            </p>

            <div style="display: flex; gap: 10px;">
                <form method="POST">
                    <input type="hidden" name="action" value="toggle">
                    <input type="hidden" name="recurring_id" value="{{ r.recurring_id }}">
                    <button type="submit" class="dashboard-button">{% if r.is_active %}Pause{% else %}Resume{% endif %}</button>
                </form>

                <form method="POST" onsubmit="return confirm('Delete this schedule?');">
                    <input type="hidden" name="action" value="delete">
                    <input type="hidden" name="recurring_id" value="{{ r.recurring_id }}">
                    <button type="submit" class="dashboard-button">Delete</button>
                </form>
            </div>
        </div>
        {% endfor %}
    {% else %}
        <p>No recurring schedules yet.</p>
    {% endif %}
</div>

{% endblock %}
'''

text = open(APP).read()
anchor = '@app.route("/driver/dashboard")'

if "def sponsor_recurring" in text:
    print("STOPPED: recurring routes already exist")
elif text.count(anchor) != 1:
    print("STOPPED: anchor found", text.count(anchor), "times")
else:
    open(APP, "w").write(text.replace(anchor, CODE + anchor))
    os.makedirs(os.path.dirname(TEMPLATE), exist_ok=True)
    open(TEMPLATE, "w").write(PAGE)
    print("DONE: routes and template added")
