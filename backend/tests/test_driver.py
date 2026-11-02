import pytest

import app as app_module


DRIVER_ID = 101


class SettingsDB:
    """Minimal fake database for driver notification settings."""

    def __init__(self):
        self.settings = {}

    def cursor(self, *args, **kwargs):
        return SettingsCursor(self)

    def commit(self):
        pass

    def close(self):
        pass


class SettingsCursor:
    def __init__(self, db):
        self.db = db
        self.result = None

    def execute(self, query, params=None):
        query_lower = query.lower()
        params = params or ()

        if "insert into user_settings" in query_lower:
            self.db.settings.update({
                "user_id": params[0],
                "notify_orders": params[1],
                "notify_points": params[2],
            })

        elif "select * from user_settings" in query_lower:
            self.result = self.db.settings.copy() or None

    def fetchone(self):
        return self.result

    def fetchall(self):
        return []

    def close(self):
        pass


def driver_client(client):
    """Log in as a test driver."""
    with client.session_transaction() as session:
        session["user_id"] = DRIVER_ID
        session["first_name"] = "Test"
        session["role"] = "Driver"


def test_driver_can_disable_order_notifications(client, monkeypatch):
    db = SettingsDB()
    monkeypatch.setattr(app_module, "get_db", lambda: db)

    driver_client(client)

    response = client.post(
        "/settings",
        data={
            "action": "notifications",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert db.settings["notify_orders"] == 0


def test_notification_settings_persist(client, monkeypatch):
    db = SettingsDB()
    monkeypatch.setattr(app_module, "get_db", lambda: db)

    driver_client(client)

    client.post(
        "/settings",
        data={
            "action": "notifications",
            "notify_orders": "on",
        },
    )

    assert db.settings["notify_orders"] == 1


def test_driver_can_reenable_order_notifications(client, monkeypatch):
    db = SettingsDB()
    monkeypatch.setattr(app_module, "get_db", lambda: db)

    driver_client(client)

    response = client.post(
        "/settings",
        data={
            "action": "notifications",
            "notify_orders": "on",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert db.settings["notify_orders"] == 1


def test_order_notification_setting_exists():
    settings = dict(
        (key, notification_types)
        for key, notification_types, _, roles
        in app_module.NOTIFICATION_SETTINGS
    )

    assert "notify_orders" in settings
    assert "Order Placed" in settings["notify_orders"]


def test_dropped_notifications_are_not_mutable():
    settings = dict(
        (key, notification_types)
        for key, notification_types, _, roles
        in app_module.NOTIFICATION_SETTINGS
    )

    # Dropped notifications should always be delivered.
    # This currently fails because app.py maps Dropped to notify_program.
    assert not any(
        "Dropped" in notification_types
        for notification_types in settings.values()
    )


def test_driver_can_disable_point_notifications(client, monkeypatch):
    db = SettingsDB()
    monkeypatch.setattr(app_module, "get_db", lambda: db)

    driver_client(client)

    response = client.post(
        "/settings",
        data={
            "action": "notifications",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert db.settings["notify_points"] == 0


def test_point_notification_setting_persists(client, monkeypatch):
    db = SettingsDB()
    monkeypatch.setattr(app_module, "get_db", lambda: db)

    driver_client(client)

    client.post(
        "/settings",
        data={
            "action": "notifications",
            "notify_points": "on",
        },
    )

    assert db.settings["notify_points"] == 1


def test_driver_can_reenable_point_notifications(client, monkeypatch):
    db = SettingsDB()
    monkeypatch.setattr(app_module, "get_db", lambda: db)

    driver_client(client)

    response = client.post(
        "/settings",
        data={
            "action": "notifications",
            "notify_points": "on",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert db.settings["notify_points"] == 1


def test_point_notification_setting_exists():
    settings = dict(
        (key, notification_types)
        for key, notification_types, _, roles
        in app_module.NOTIFICATION_SETTINGS
    )

    assert "notify_points" in settings
    assert "Point Change" in settings["notify_points"]


def test_notification_settings_include_driver_role():
    for key, notification_types, label, roles in app_module.NOTIFICATION_SETTINGS:
        if key in ("notify_orders", "notify_points"):
            assert "driver" in roles