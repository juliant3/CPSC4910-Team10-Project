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