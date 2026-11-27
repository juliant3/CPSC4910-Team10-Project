"""Every page loads for each role without a template or code error."""

import pytest
from datetime import datetime
from decimal import Decimal

class Row(dict):
    """A database row that answers 0 for any column a page asks for."""
    def __missing__(self, key):
        return 0

class Cur:
    rowcount = 1; lastrowid = 1
    def __init__(self): self.executed = []
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def execute(self, q, p=None): self.executed.append(q)
    def executemany(self, q, p): pass
    def fetchone(self): return Row()
    def fetchall(self): return []

class Conn:
    def cursor(self, *a, **k): return Cur()
    def commit(self): pass
    def rollback(self): pass
    def close(self): pass

ROLE_PAGES = {
    "Admin": ["/", "/admin/sponsors", "/admin/sponsors/1", "/admin/drivers", "/admin/users", "/admin/applications",
              "/admin/sponsor-requests", "/admin/orders", "/admin/messages", "/admin/messages/sponsor/1", "/admin/messages/driver/1", "/admin/invoices", "/admin/invoices/1", "/admin/invoices/report",
              "/admin/reports", "/admin/audit-logs", "/reports/driver-points", "/reports/sales-by-sponsor",
              "/reports/sales-by-driver", "/reports/audit-logs", "/settings", "/bulk-upload", "/about"],
    "Sponsor": ["/", "/sponsor/dashboard", "/sponsor/drivers", "/sponsor/drivers/1", "/sponsor/drivers/1/edit",
                "/sponsor/applications", "/sponsor/team", "/sponsor/recurring", "/sponsor/reports", "/support",
                "/reports/driver-points", "/messages", "/messages/1", "/settings", "/bulk-upload", "/about"],
    "Driver": ["/driver/dashboard", "/driver/points", "/driver/sponsors", "/driver/orders", "/driver/profile",
               "/driver/profile/edit", "/driver/cart", "/support", "/messages", "/messages/1", "/settings", "/about"],
}
CASES = [(r, p) for r, ps in ROLE_PAGES.items() for p in ps]

@pytest.mark.parametrize("role,path", CASES)
def test_page_renders(client, set_session, monkeypatch, app_module, role, path):
    monkeypatch.setattr(app_module, "get_db", lambda: Conn())
    set_session(role=role, user_id=1, sponsor_id=1, last_name="X")
    r = client.get(path)
    assert r.status_code in (200, 302, 404), f"{r.status_code}"

@pytest.mark.parametrize("path", ["/login", "/register", "/forgot-password", "/sponsor-request", "/about", "/apply/abc", "/set-password/abc"])
def test_public_page_renders(client, monkeypatch, app_module, path):
    monkeypatch.setattr(app_module, "get_db", lambda: Conn())
    monkeypatch.setattr(app_module, "get_login_stats", lambda: (0, 0))
    r = client.get(path)
    assert r.status_code in (200, 302, 404), f"{r.status_code}"


def test_support_view_greys_out_blocked_buttons(client, set_session, monkeypatch, app_module):
    monkeypatch.setattr(app_module, "get_db", lambda: Conn())
    set_session(role="sponsor", user_id=14, sponsor_id=14, assumed_sponsor_id=14,
                _assume_sponsor_original={"user_id": 1, "role": "Admin"})

    html = client.get("/sponsor/applications").get_data(as_text=True)

    assert "Read-only while viewing as another account" in html
    assert "/sponsor/applications/[^/]+/accept" in html.replace("\\/", "/")
