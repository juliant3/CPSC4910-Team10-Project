from flask import Blueprint

admin_bp = Blueprint("admin", __name__)

# GET    /api/admin/users                     (list all users, filter by role)
# GET    /api/admin/users/<id>
# PUT    /api/admin/users/<id>                 (update any sponsor/driver/admin)
# POST   /api/admin/users                      (create new admin/driver/sponsor user)
# DELETE /api/admin/users/<id>                 ("drop" a user - triggers uncancellable alert if driver)
# POST   /api/admin/impersonate/driver/<id>
# POST   /api/admin/impersonate/sponsor/<id>
# GET    /api/admin/about                      (view current About page data)
# PUT    /api/admin/about                      (update team #/version/release date each sprint)
