from flask import Blueprint

sponsor_bp = Blueprint("sponsor", __name__)

# GET   /api/sponsor/profile
# PUT   /api/sponsor/profile                 (incl. point_value_usd)
# GET   /api/sponsor/applications             (pending driver applications)
# PUT   /api/sponsor/applications/<id>         (approve/reject, requires reason)
# GET   /api/sponsor/drivers                  (list of participating drivers + status)
# PUT   /api/sponsor/drivers/<id>              (update driver info / drop driver)
# POST  /api/sponsor/drivers/<id>/points       (add/deduct points, requires reason)
# GET   /api/sponsor/catalog
# PUT   /api/sponsor/catalog                  (add/remove/update catalog items)
# POST  /api/sponsor/drivers/<id>/purchases    (purchase on behalf of a driver)
# POST  /api/sponsor/users                     (create additional sponsor user logins)
# GET   /api/sponsor/users
# POST  /api/sponsor/impersonate/<driver_id>   (view-as-driver / act-as-driver)
