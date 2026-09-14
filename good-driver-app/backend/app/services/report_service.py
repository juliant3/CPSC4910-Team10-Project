"""
Builds the data behind every report in report_routes.py, and a
to_csv(rows, columns) helper reused by all of them so every report can
be exported the same way.

- driver_point_tracking(sponsor_id, driver_id=None, start=None, end=None)
- sponsor_audit_log(sponsor_id, start=None, end=None, category=None)
- sales_by_sponsor(sponsor_id=None, start=None, end=None, detailed=False)
- sales_by_driver(sponsor_id=None, driver_id=None, start=None, end=None, detailed=False)
- sponsor_invoice(sponsor_id=None, start=None, end=None)
    -> purchases grouped by driver, computed fee = 1% of $ value sold
- admin_audit_log(sponsor_id=None, start=None, end=None, category=None)
- to_csv(rows, columns) -> streams a downloadable CSV response
"""
