import pytest

import app as app_module


SPONSOR_ID = 20
DRIVER_ID = 101


class PointsDB:
    """Fake database for sponsor point adjustment tests."""

    def __init__(self, points_balance=500):
        self.points_balance = points_balance
        self.transactions = []
        self.notifications = []
        self.committed = False

    def cursor(self, *args, **kwargs):
        return PointsCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class PointsCursor:
    def __init__(self, db):
        self.db = db
        self.result = None

    def execute(self, query, params=None):
        query_lower = " ".join(query.lower().split())
        params = params or ()

        if "select points_balance" in query_lower:
            self.result = {
                "points_balance": self.db.points_balance
            }

        elif "update driver_sponsors" in query_lower:
            amount = params[0]

            if self.db.points_balance + amount >= 0:
                self.db.points_balance += amount

        elif "insert into point_transactions" in query_lower:
            self.db.transactions.append(params)

        elif "insert into notifications" in query_lower:
            self.db.notifications.append(params)

        elif "select company_name from sponsors" in query_lower:
            self.result = {"company_name": "Test Sponsor"}

        else:
            self.result = None

    def fetchone(self):
        return self.result

    def fetchall(self):
        return []

    def close(self):
        pass


def setup_sponsor(client):
    """Log in as a test sponsor."""
    with client.session_transaction() as session:
        session["user_id"] = SPONSOR_ID
        session["sponsor_id"] = SPONSOR_ID
        session["role"] = "Sponsor"


def configure_points_db(monkeypatch, db):
    monkeypatch.setattr(app_module, "get_db", lambda: db)
    monkeypatch.setattr(
        app_module,
        "current_sponsor_id",
        lambda: SPONSOR_ID,
    )


def test_sponsor_can_add_points(client, monkeypatch):
    db = PointsDB(points_balance=500)
    configure_points_db(monkeypatch, db)
    setup_sponsor(client)

    response = client.post(
        f"/sponsor/drivers/{DRIVER_ID}/points",
        data={
            "action": "add",
            "amount": "100",
            "reason": "Safe driving reward",
        },
        follow_redirects=False,
    )

    assert response.status_code in (200, 302)
    assert db.points_balance == 600
    assert len(db.transactions) == 1


def test_sponsor_can_deduct_points(client, monkeypatch):
    db = PointsDB(points_balance=500)
    configure_points_db(monkeypatch, db)
    setup_sponsor(client)

    response = client.post(
        f"/sponsor/drivers/{DRIVER_ID}/points",
        data={
            "action": "deduct",
            "amount": "100",
            "reason": "Policy violation",
        },
        follow_redirects=False,
    )

    assert response.status_code in (200, 302)
    assert db.points_balance == 400
    assert len(db.transactions) == 1


def test_add_points_updates_balance_correctly(client, monkeypatch):
    db = PointsDB(points_balance=250)
    configure_points_db(monkeypatch, db)
    setup_sponsor(client)

    client.post(
        f"/sponsor/drivers/{DRIVER_ID}/points",
        data={
            "action": "add",
            "amount": "50",
            "reason": "Reward",
        },
    )

    assert db.points_balance == 300


def test_deduct_points_updates_balance_correctly(client, monkeypatch):
    db = PointsDB(points_balance=250)
    configure_points_db(monkeypatch, db)
    setup_sponsor(client)

    client.post(
        f"/sponsor/drivers/{DRIVER_ID}/points",
        data={
            "action": "deduct",
            "amount": "50",
            "reason": "Adjustment",
        },
    )

    assert db.points_balance == 200


def test_sponsor_cannot_add_zero_points(client, monkeypatch):
    db = PointsDB(points_balance=250)
    configure_points_db(monkeypatch, db)
    setup_sponsor(client)

    client.post(
        f"/sponsor/drivers/{DRIVER_ID}/points",
        data={
            "action": "add",
            "amount": "0",
            "reason": "Invalid adjustment",
        },
    )

    assert db.points_balance == 250
    assert len(db.transactions) == 0


def test_sponsor_cannot_add_negative_points(client, monkeypatch):
    db = PointsDB(points_balance=250)
    configure_points_db(monkeypatch, db)
    setup_sponsor(client)

    client.post(
        f"/sponsor/drivers/{DRIVER_ID}/points",
        data={
            "action": "add",
            "amount": "-50",
            "reason": "Invalid adjustment",
        },
    )

    assert db.points_balance == 250
    assert len(db.transactions) == 0


def test_sponsor_cannot_deduct_more_points_than_driver_has(
    client, monkeypatch
):
    db = PointsDB(points_balance=50)
    configure_points_db(monkeypatch, db)
    setup_sponsor(client)

    client.post(
        f"/sponsor/drivers/{DRIVER_ID}/points",
        data={
            "action": "deduct",
            "amount": "100",
            "reason": "Adjustment",
        },
    )

    assert db.points_balance == 50
    assert len(db.transactions) == 0


def test_point_adjustment_creates_transaction(client, monkeypatch):
    db = PointsDB(points_balance=500)
    configure_points_db(monkeypatch, db)
    setup_sponsor(client)

    client.post(
        f"/sponsor/drivers/{DRIVER_ID}/points",
        data={
            "action": "add",
            "amount": "75",
            "reason": "Monthly reward",
        },
    )

    assert len(db.transactions) == 1


def test_point_adjustment_creates_notification(client, monkeypatch):
    db = PointsDB(points_balance=500)
    configure_points_db(monkeypatch, db)
    setup_sponsor(client)

    client.post(
        f"/sponsor/drivers/{DRIVER_ID}/points",
        data={
            "action": "add",
            "amount": "75",
            "reason": "Monthly reward",
        },
    )

    assert len(db.notifications) == 1


def test_point_adjustment_commits_database_changes(client, monkeypatch):
    db = PointsDB(points_balance=500)
    configure_points_db(monkeypatch, db)
    setup_sponsor(client)

    client.post(
        f"/sponsor/drivers/{DRIVER_ID}/points",
        data={
            "action": "add",
            "amount": "75",
            "reason": "Monthly reward",
        },
    )

    assert db.committed is True


def test_sponsor_cannot_adjust_points_without_login(client, monkeypatch):
    db = PointsDB(points_balance=500)
    configure_points_db(monkeypatch, db)

    response = client.post(
        f"/sponsor/drivers/{DRIVER_ID}/points",
        data={
            "action": "add",
            "amount": "100",
            "reason": "Reward",
        },
    )

    assert response.status_code in (302, 401, 403)
    assert db.points_balance == 500
    assert len(db.transactions) == 0


def test_sponsor_point_adjustment_uses_correct_driver(client, monkeypatch):
    db = PointsDB(points_balance=500)
    configure_points_db(monkeypatch, db)
    setup_sponsor(client)

    client.post(
        f"/sponsor/drivers/{DRIVER_ID}/points",
        data={
            "action": "add",
            "amount": "100",
            "reason": "Reward",
        },
    )

    assert db.points_balance == 600
    assert len(db.transactions) == 1


def test_sponsor_point_adjustment_rejects_invalid_amount(client, monkeypatch):
    db = PointsDB(points_balance=500)
    configure_points_db(monkeypatch, db)
    setup_sponsor(client)

    client.post(
        f"/sponsor/drivers/{DRIVER_ID}/points",
        data={
            "action": "add",
            "amount": "not-a-number",
            "reason": "Invalid adjustment",
        },
    )

    assert db.points_balance == 500
    assert len(db.transactions) == 0


SPONSOR_ID = 20
SPONSOR_USER_ID = 20
INVITED_USER_ID = 44


class SponsorTeamDB:
    def __init__(self, invite=None):
        self.invite = invite
        self.created_invites = []
        self.users = []
        self.passwords = []
        self.memberships = []
        self.request_updates = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return SponsorTeamCursor(self)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class SponsorTeamCursor:
    def __init__(self, db):
        self.db = db
        self.result = None
        self.lastrowid = INVITED_USER_ID
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        compact = " ".join(sql.split())
        params = params or ()

        if compact.startswith(
            "SELECT user_id FROM Users WHERE LOWER(email)"
        ):
            self.result = None

        elif compact.startswith(
            "SELECT request_id FROM Sponsor_Requests"
        ):
            self.result = None

        elif compact.startswith(
            "SELECT company_name FROM Sponsors"
        ):
            self.result = {"company_name": "Example Sponsor"}

        elif compact.startswith("INSERT INTO Sponsor_Requests"):
            self.db.created_invites.append(params)

        elif compact.startswith(
            "SELECT request_id, email, first_name, last_name, status"
        ):
            self.result = self.db.invite

        elif compact.startswith("INSERT INTO Users"):
            self.db.users.append(params)

        elif compact.startswith("INSERT INTO Password"):
            self.db.passwords.append(params)

        elif compact.startswith("INSERT INTO Sponsor_Users"):
            self.db.memberships.append(params)

        elif compact.startswith("UPDATE Sponsor_Requests"):
            self.db.request_updates.append(params)

        else:
            raise AssertionError(
                f"Unexpected sponsor-team SQL: {compact}"
            )

    def fetchone(self):
        return self.result


def sponsor_session(client):
    with client.session_transaction() as session:
        session.clear()
        session.update(
            user_id=SPONSOR_USER_ID,
            first_name="Primary",
            last_name="Sponsor",
            role="Sponsor",
            sponsor_id=SPONSOR_ID,
        )


def test_sponsor_can_invite_an_additional_team_user(
    client, monkeypatch
):
    db = SponsorTeamDB()

    monkeypatch.setattr(
        app_module, "get_db", lambda: db
    )

    monkeypatch.setattr(
        app_module, "current_sponsor_id", lambda: SPONSOR_ID
    )

    monkeypatch.setattr(
        app_module, "send_sponsor_team_invite", lambda *args: True
    )

    sponsor_session(client)

    response = client.post(
        "/sponsor/team",
        data={
            "action": "invite",
            "first_name": "Second",
            "last_name": "Sponsor",
            "email": "second@example.test",
        },
    )

    assert response.status_code == 302
    assert len(db.created_invites) == 1

    assert db.created_invites[0][4] == SPONSOR_ID
    assert db.created_invites[0][6] == SPONSOR_USER_ID

    assert db.committed


def test_invited_user_account_is_created_and_linked_to_sponsor(
    client, monkeypatch
):
    invite = {
        "request_id": 700,
        "email": "second@example.test",
        "first_name": "Second",
        "last_name": "Sponsor",
        "status": "Started",
        "request_type": "Join",
        "target_sponsor_id": SPONSOR_ID,
        "company_name": "Example Sponsor",
    }

    db = SponsorTeamDB(invite=invite)

    monkeypatch.setattr(
        app_module, "get_db", lambda: db
    )

    monkeypatch.setattr(
        app_module, "render_template",
        lambda *args, **kwargs: "joined"
    )

    response = client.post(
        "/sponsor-apply/test-token",
        data={
            "first_name": "Second",
            "last_name": "Sponsor",
            "password": "ValidPass123!",
            "confirm_password": "ValidPass123!",
        },
    )

    assert response.status_code == 200

    assert db.users == [
        ("second@example.test", "Second", "Sponsor")
    ]

    assert len(db.passwords) == 1

    assert db.memberships == [
        (INVITED_USER_ID, SPONSOR_ID)
    ]

    assert db.request_updates == [
        (INVITED_USER_ID, 700)
    ]

    assert db.committed


def test_invited_user_cannot_create_account_with_mismatched_passwords(
    client, monkeypatch
):
    invite = {
        "request_id": 701,
        "email": "second@example.test",
        "first_name": "Second",
        "last_name": "Sponsor",
        "status": "Started",
        "request_type": "Join",
        "target_sponsor_id": SPONSOR_ID,
        "company_name": "Example Sponsor",
    }

    db = SponsorTeamDB(invite=invite)

    monkeypatch.setattr(
        app_module, "get_db", lambda: db
    )

    monkeypatch.setattr(
        app_module, "render_template",
        lambda *args, **kwargs: "form-error"
    )

    response = client.post(
        "/sponsor-apply/test-token",
        data={
            "first_name": "Second",
            "last_name": "Sponsor",
            "password": "ValidPass123!",
            "confirm_password": "DifferentPass123!",
        },
    )

    assert response.status_code == 200

    assert db.users == []
    assert db.passwords == []
    assert db.memberships == []
    assert db.request_updates == []
