"""
Extension instances created here (not in __init__.py) to avoid circular
imports between models, routes, and the app factory.
"""
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_bcrypt import Bcrypt
from flask_jwt_extended import JWTManager

db = SQLAlchemy()
migrate = Migrate()
bcrypt = Bcrypt()
jwt = JWTManager()
