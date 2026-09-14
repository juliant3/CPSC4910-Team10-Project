from flask import Blueprint

about_bp = Blueprint("about", __name__)

# GET /api/about   -> reads the single AboutInfo row from the database
