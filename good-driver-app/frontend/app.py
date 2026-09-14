"""
Thin Flask app for server-rendered pages. Each route fetches data from
the backend API (via requests) and renders a Jinja template - no
business logic here, that all lives in backend/app/services/.
"""
from flask import Flask

app = Flask(__name__)

# GET /                      -> templates/shared/home.html
# GET /login                 -> templates/shared/login.html
# GET /about                 -> templates/shared/about.html   (pulls from /api/about)
# GET /driver/dashboard       -> templates/driver/dashboard.html
# GET /driver/catalog         -> templates/driver/catalog.html
# GET /driver/purchases       -> templates/driver/purchases.html
# GET /driver/profile         -> templates/driver/profile.html
# GET /sponsor/dashboard      -> templates/sponsor/dashboard.html
# GET /sponsor/applications   -> templates/sponsor/applications.html
# GET /sponsor/drivers        -> templates/sponsor/drivers.html
# GET /sponsor/catalog        -> templates/sponsor/catalog.html
# GET /sponsor/reports        -> templates/sponsor/reports.html
# GET /admin/dashboard        -> templates/admin/dashboard.html
# GET /admin/users            -> templates/admin/users.html
# GET /admin/reports          -> templates/admin/reports.html

if __name__ == "__main__":
    app.run(debug=True, port=5001)
