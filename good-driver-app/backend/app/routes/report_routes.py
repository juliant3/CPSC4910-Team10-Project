from flask import Blueprint

report_bp = Blueprint("reports", __name__)

# --- Sponsor reports ---
# GET /api/reports/sponsor/points          (Driver Point Tracking; ?driver_id=&start=&end=)
# GET /api/reports/sponsor/audit           (audit log restricted to this sponsor's drivers)

# --- Admin reports ---
# GET /api/reports/admin/sales-by-sponsor  (?sponsor_id=&start=&end=&view=detailed|summary)
# GET /api/reports/admin/sales-by-driver   (?sponsor_id=&driver_id=&start=&end=&view=)
# GET /api/reports/admin/invoice           (?sponsor_id=&start=&end=)
# GET /api/reports/admin/audit             (?sponsor_id=&start=&end=&category=)

# Every report route above should also support a `?format=csv` query
# param that streams a CSV instead of the JSON used to render the
# visual report in the frontend.
