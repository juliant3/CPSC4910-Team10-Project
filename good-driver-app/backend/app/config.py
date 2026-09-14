"""
Application configuration.
Never hardcode credentials here — everything comes from environment
variables so nothing sensitive gets committed to git.
"""
import os


class Config:
    # --- Database (AWS RDS MySQL) ---
    DB_HOST = os.environ.get("DB_HOST")
    DB_PORT = os.environ.get("DB_PORT", "3306")
    DB_NAME = os.environ.get("DB_NAME")
    DB_USER = os.environ.get("DB_USER")
    DB_PASSWORD = os.environ.get("DB_PASSWORD")

    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- Security ---
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-.env")
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "change-me-too")
    JWT_ACCESS_TOKEN_EXPIRES_MIN = 60  # minutes

    # Password complexity policy (used by utils/validators.py)
    PASSWORD_MIN_LENGTH = 10
    PASSWORD_REQUIRE_UPPER = True
    PASSWORD_REQUIRE_LOWER = True
    PASSWORD_REQUIRE_DIGIT = True
    PASSWORD_REQUIRE_SYMBOL = True

    # --- Points ---
    DEFAULT_POINT_VALUE_USD = 0.01  # sponsors can override per-sponsor

    # --- External product catalog API ---
    CATALOG_API_BASE_URL = os.environ.get("CATALOG_API_BASE_URL", "")
    CATALOG_API_KEY = os.environ.get("CATALOG_API_KEY", "")

    # --- Email (for alerts / password reset) ---
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"  # fast, isolated test DB


config_by_name = {
    "dev": DevelopmentConfig,
    "prod": ProductionConfig,
    "test": TestingConfig,
}
