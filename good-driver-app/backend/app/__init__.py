import os
from flask import Flask

from app.config import config_by_name
from app.extensions import db, migrate, bcrypt, jwt


def create_app(env=None):
    app = Flask(__name__)
    env = env or os.environ.get("FLASK_ENV", "dev")
    app.config.from_object(config_by_name[env])

    # --- init extensions ---
    db.init_app(app)
    migrate.init_app(app, db)
    bcrypt.init_app(app)
    jwt.init_app(app)

    # --- register blueprints ---
    from app.routes.auth_routes import auth_bp
    from app.routes.driver_routes import driver_bp
    from app.routes.sponsor_routes import sponsor_bp
    from app.routes.admin_routes import admin_bp
    from app.routes.catalog_routes import catalog_bp
    from app.routes.report_routes import report_bp
    from app.routes.about_routes import about_bp

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(driver_bp, url_prefix="/api/driver")
    app.register_blueprint(sponsor_bp, url_prefix="/api/sponsor")
    app.register_blueprint(admin_bp, url_prefix="/api/admin")
    app.register_blueprint(catalog_bp, url_prefix="/api/catalog")
    app.register_blueprint(report_bp, url_prefix="/api/reports")
    app.register_blueprint(about_bp, url_prefix="/api/about")

    return app
