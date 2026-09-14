from flask import Blueprint

driver_bp = Blueprint("driver", __name__)

# GET   /api/driver/profile
# PUT   /api/driver/profile
# PUT   /api/driver/password
# GET   /api/driver/catalog                 (browse sponsor's product catalog)
# POST  /api/driver/purchases               ("purchase" a product with points)
# GET   /api/driver/purchases                (review purchase status/history)
# PUT   /api/driver/purchases/<id>           (update a pending purchase)
# DELETE /api/driver/purchases/<id>          (cancel a purchase)
# GET   /api/driver/points                   (current balance + history)
# GET   /api/driver/alerts/settings
# PUT   /api/driver/alerts/settings          (enable/disable points-changed / order-placed alerts)
