
"""Unit tests for password-change audit visibility across sponsor programs."""
import app as app_module


class AuditCursor:
    def __init__(self, memberships):
        self.memberships = memberships
        self.audit_rows = []
        self.result = None

    def execute(self, sql, params=None):
        compact = " ".join(sql.split())
        params = params or ()

        if compact.startswith("SELECT sponsor_id FROM Driver_Sponsors"):
            self.result = list(self.memberships)

        elif compact.startswith("INSERT INTO Audit_Logs"):
            self.audit_rows.append((compact, params))

        else:
            raise AssertionError(f"Unexpected audit SQL: {compact}")

    def fetchall(self):
        return self.result


def test_driver_password_change_is_logged_for_each_sponsor():
    cursor = AuditCursor([
        {"sponsor_id": 20},
        {"sponsor_id": 30}
    ])

    app_module.record_password_change(
        cursor,
        101,
        "Password changed in settings",
        "driver@example.test"
    )

    assert len(cursor.audit_rows) == 2

    assert [row[1][1] for row in cursor.audit_rows] == [20, 30]

    assert all(row[1][0] == 101 for row in cursor.audit_rows)
    assert all(row[1][2] == 101 for row in cursor.audit_rows)

    assert all(
        row[1][3] == "Password changed in settings"
        for row in cursor.audit_rows
    )


def test_non_driver_password_change_keeps_one_unscoped_audit_row():
    cursor = AuditCursor([])

    app_module.record_password_change(
        cursor,
        20,
        "Password changed in settings",
        "sponsor@example.test"
    )

    assert len(cursor.audit_rows) == 1

    assert "sponsor_id" not in cursor.audit_rows[0][0]

    assert cursor.audit_rows[0][1] == (
        20,
        "Password changed in settings",
        "sponsor@example.test"
    )
