from flask import Blueprint

catalog_bp = Blueprint("catalog", __name__)

# GET  /api/catalog/<sponsor_id>                 (sponsor's catalog, cached copy)
# GET  /api/catalog/<sponsor_id>/refresh          (force re-sync from external API)
# GET  /api/catalog/<sponsor_id>/products/<id>/live  (live price+availability check,
#                                                      called when item added to cart)
