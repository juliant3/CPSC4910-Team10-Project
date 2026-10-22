"""Sponsor-side tools: points, application questions, invites, and recurring points."""

from datetime import datetime

import pytest

import app as app_for_real_job

# conftest switches the recurring job off for route tests; keep the real one
# here so it can be tested directly.
REAL_PROCESS_RECURRING_POINTS = app_for_real_job.process_recurring_points


def queries(conn):
    return conn.cursor().executed


def sql(conn):
    return [q for q, _ in queries(conn)]


# ------------------------------------------------------------------
# Adding / deducting points: bad input never reaches the database
# ------------------------------------------------------------------

@pytest.mark.parametrize("form", [
    {"action": "add", "amount": "abc", "reason": "Safe week"},
    {"action": "add", "amount": "0", "reason": "Safe week"},
    {"action": "add", "amount": "-5", "reason": "Safe week"},
    {"action": "add", "amount": "10", "reason": "   "},
    {"action": "steal", "amount": "10", "reason": "Safe week"},
    {"amount": "10", "reason": "Safe week"},
])
def test_invalid_point_changes_are_rejected(client, set_session, fake_db, form):
    conn = fake_db()
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    response = client.post("/sponsor/drivers/20/points", data=form, follow_redirects=False)

    assert response.headers["Location"].endswith("/sponsor/drivers")
    assert sql(conn) == []


def test_points_for_driver_outside_program_are_rejected(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[None])  # no Driver_Sponsors row for this sponsor
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/sponsor/drivers/99/points", data={"action": "add", "amount": "10", "reason": "x"})

    assert not any(q.startswith(("UPDATE", "INSERT")) for q in sql(conn))


def test_deduction_records_negative_transaction_by_the_sponsor_user(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[{"points_balance": 50}, {"company_name": "Acme"}])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/sponsor/drivers/20/points", data={"action": "deduct", "amount": "15", "reason": "Late"})

    tx = next(p for q, p in queries(conn) if "INSERT INTO Point_Transactions" in q)
    # (driver, sponsor org, which teammate did it, change, reason)
    assert tx == (20, 3, 50, -15, "Late")
    assert conn.committed


def test_point_reason_is_trimmed_to_255_characters(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[{"points_balance": 0}, {"company_name": "Acme"}])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/sponsor/drivers/20/points", data={"action": "add", "amount": "1", "reason": "x" * 400})

    tx = next(p for q, p in queries(conn) if "INSERT INTO Point_Transactions" in q)
    assert len(tx[4]) == 255


# ------------------------------------------------------------------
# Application questions
# ------------------------------------------------------------------

def test_sponsor_can_add_application_question(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[{"max_order": 2}])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/sponsor/questions", data={"question_text": "CDL state?", "is_required": "on"})

    insert = next(p for q, p in queries(conn) if "INSERT INTO Sponsor_Questions" in q)
    assert insert == (3, "CDL state?", True, 3)  # goes to the end of the list
    assert conn.committed


def test_blank_question_is_ignored(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/sponsor/questions", data={"question_text": "   "})

    assert sql(conn) == []


def test_sponsor_can_only_delete_their_own_questions(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/sponsor/questions/44/delete")

    query, params = queries(conn)[0]
    assert "AND sponsor_id = %s" in query
    assert params == (44, 3)


# ------------------------------------------------------------------
# Rejecting driver applications
# ------------------------------------------------------------------

def test_reject_requires_a_reason(client, set_session, fake_db):
    conn = fake_db()
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/sponsor/applications/4/reject", data={"reason": "  "})

    assert sql(conn) == []


def test_reject_only_works_on_submitted_applications(client, set_session, fake_db):
    conn = fake_db(fetchone_values=[{"application_id": 4, "status": "Accepted", "sponsor_id": 3}])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/sponsor/applications/4/reject", data={"reason": "Not a fit"})

    assert not any(q.startswith("UPDATE") for q in sql(conn))


def test_sponsor_cannot_accept_another_sponsors_application(client, set_session, fake_db):
    # The lookup is scoped to this sponsor, so another sponsor's application isn't found.
    conn = fake_db(fetchone_values=[None])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/sponsor/applications/4/accept")

    lookup_query, lookup_params = queries(conn)[0]
    assert "AND sponsor_id = %s" in lookup_query
    assert lookup_params == (4, 3)
    assert not any(q.startswith(("INSERT", "UPDATE")) for q in sql(conn))


# ------------------------------------------------------------------
# Inviting drivers
# ------------------------------------------------------------------

@pytest.fixture
def sent_invites(monkeypatch, app_module):
    sent = []
    monkeypatch.setattr(app_module, "send_application_invite",
                        lambda email, token: sent.append((email, token)) or True)
    return sent


def test_invite_requires_an_email(client, set_session, fake_db, sent_invites):
    conn = fake_db()
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    response = client.post("/sponsor/applications", data={"driver_email": " "})

    assert b"Please enter a driver" in response.data
    assert not any("Driver_Applications" in q or q.startswith("INSERT") for q in sql(conn))
    assert sent_invites == []


def test_invite_new_driver_creates_pending_application(client, set_session, fake_db, sent_invites):
    conn = fake_db(fetchone_values=[{"sponsor_id": 3}, None, None])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/sponsor/applications", data={"driver_email": "New@Driver.com"})

    insert_query, insert_params = next((q, p) for q, p in queries(conn) if "INSERT INTO Driver_Applications" in q)
    assert "NULL" in insert_query            # no existing account
    assert insert_params[0] == "new@driver.com"
    assert insert_params[1] == 3
    assert sent_invites[0][0] == "new@driver.com"
    assert sent_invites[0][1] == insert_params[2]  # emailed token matches the saved one


def test_invite_existing_driver_links_their_account(client, set_session, fake_db, sent_invites):
    conn = fake_db(fetchone_values=[{"sponsor_id": 3}, {"driver_id": 20}, None, None])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/sponsor/applications", data={"driver_email": "dana@x.com"})

    insert = next(p for q, p in queries(conn) if "INSERT INTO Driver_Applications" in q)
    assert insert[0] == 20
    assert len(sent_invites) == 1


def test_cannot_invite_driver_already_in_program(client, set_session, fake_db, sent_invites):
    conn = fake_db(fetchone_values=[{"sponsor_id": 3}, {"driver_id": 20}, {"1": 1}])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    response = client.post("/sponsor/applications", data={"driver_email": "dana@x.com"})

    assert b"already in your program" in response.data
    assert not any("INSERT" in q for q in sql(conn))
    assert sent_invites == []


def test_cannot_send_duplicate_pending_invite(client, set_session, fake_db, sent_invites):
    conn = fake_db(fetchone_values=[{"sponsor_id": 3}, None, {"application_id": 9}])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    response = client.post("/sponsor/applications", data={"driver_email": "dana@x.com"})

    assert b"already a pending application" in response.data
    assert not any("INSERT" in q for q in sql(conn))
    assert sent_invites == []


# ------------------------------------------------------------------
# Recurring points: creating, pausing, deleting
# ------------------------------------------------------------------

VALID_RULE = {
    "action": "create",
    "point_change": "25",
    "reason": "Weekly bonus",
    "frequency": "Weekly",
    "start_date": "2026-11-01",
    "target": "all",
}


@pytest.mark.parametrize("change, message", [
    ({"point_change": "0"}, b"greater than 0"),
    ({"point_change": "abc"}, b"greater than 0"),
    ({"reason": " "}, b"Enter a reason"),
    ({"frequency": "Hourly"}, b"Choose a frequency"),
    ({"start_date": "next tuesday"}, b"Choose a start date"),
    ({"target": "selected"}, b"Select at least one driver"),
])
def test_invalid_recurring_rule_is_rejected(client, set_session, fake_db, change, message):
    conn = fake_db(fetchall_values=[[{"driver_id": 20}]])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    response = client.post("/sponsor/recurring", data={**VALID_RULE, **change})

    assert message in response.data
    assert not any("INSERT INTO Recurring_Points" in q for q in sql(conn))


def test_valid_recurring_rule_for_all_drivers_is_saved(client, set_session, fake_db):
    conn = fake_db(fetchall_values=[[{"driver_id": 20}]])
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/sponsor/recurring", data=VALID_RULE)

    insert = next(p for q, p in queries(conn) if "INSERT INTO Recurring_Points" in q)
    assert insert == (3, 25, "Weekly bonus", "Weekly", "2026-11-01", 1)
    assert conn.committed


def test_recurring_rule_ignores_drivers_from_other_programs(client, set_session, fake_db):
    # Sponsor 3 only has driver 20; driver 77 belongs to someone else.
    conn = fake_db(fetchall_values=[[{"driver_id": 20}]], lastrowid=900)
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/sponsor/recurring", data={**VALID_RULE, "target": "selected", "driver_ids": ["20", "77"]})

    picked = [p for q, p in queries(conn) if "INSERT INTO Recurring_Point_Drivers" in q]
    assert picked == [(900, 20)]


@pytest.mark.parametrize("action", ["toggle", "delete"])
def test_sponsor_can_only_change_their_own_rules(client, set_session, fake_db, action):
    conn = fake_db()
    set_session(role="Sponsor", user_id=50, sponsor_id=3)

    client.post("/sponsor/recurring", data={"action": action, "recurring_id": "12"})

    query, params = queries(conn)[0]
    assert "AND sponsor_id = %s" in query
    assert params == (12, 3)


# ------------------------------------------------------------------
# Recurring points: schedule math and the background job
# ------------------------------------------------------------------

@pytest.mark.parametrize("frequency, start, now, expected", [
    ("Daily", datetime(2026, 10, 1), datetime(2026, 10, 1, 12), datetime(2026, 10, 2)),
    ("Weekly", datetime(2026, 10, 1), datetime(2026, 10, 20), datetime(2026, 10, 22)),
    ("Monthly", datetime(2026, 1, 31), datetime(2026, 2, 1), datetime(2026, 2, 28)),
    ("Monthly", datetime(2026, 12, 15), datetime(2026, 12, 15), datetime(2027, 1, 15)),
])
def test_next_run_moves_into_the_future(app_module, frequency, start, now, expected):
    assert app_module.advance_run(start, frequency, now) == expected


def test_future_rule_is_left_alone(app_module):
    future = datetime(2027, 1, 1)
    assert app_module.advance_run(future, "Daily", datetime(2026, 10, 22)) == future


def test_recurring_job_awards_points_only_within_that_sponsor(fake_db):
    now = datetime(2026, 10, 22, 9, 0)
    rule = {"recurring_id": 12, "sponsor_id": 3, "point_change": 25, "reason": "Weekly bonus",
            "frequency": "Weekly", "next_run": datetime(2026, 10, 22), "all_drivers": 1}
    conn = fake_db(fetchone_values=[{"now": now}], fetchall_values=[[rule], [{"driver_id": 20}, {"driver_id": 21}]])

    REAL_PROCESS_RECURRING_POINTS()

    updates = [p for q, p in queries(conn) if q.startswith("UPDATE Driver_Sponsors")]
    assert updates == [(25, 20, 3), (25, 21, 3)]
    notes = [p for q, p in queries(conn) if "INSERT INTO Notifications" in q]
    assert [n[0] for n in notes] == [20, 21]
    assert conn.committed


def test_recurring_job_skips_rule_already_claimed(fake_db):
    # Another request already awarded this rule, so the claiming UPDATE matches nothing.
    now = datetime(2026, 10, 22, 9, 0)
    rule = {"recurring_id": 12, "sponsor_id": 3, "point_change": 25, "reason": "Weekly bonus",
            "frequency": "Weekly", "next_run": datetime(2026, 10, 22), "all_drivers": 1}
    conn = fake_db(fetchone_values=[{"now": now}], fetchall_values=[[rule], [{"driver_id": 20}]])
    conn.cursor().rowcount = 0

    REAL_PROCESS_RECURRING_POINTS()

    assert not any(q.startswith("UPDATE Driver_Sponsors") for q in sql(conn))
