"""
Base user account. Driver / Sponsor / Admin each have a one-to-one row
here PLUS a row in their own role-specific table (driver.py, sponsor.py,
admin.py) for role-specific fields. This lets one `username` login table
serve all three roles without forcing the user to pick a "type" at login
(per the spec's requirement).
"""
from datetime import datetime
from app.extensions import db


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)  # bcrypt hash, never plaintext
    role = db.Column(db.Enum("driver", "sponsor", "admin", name="user_role"), nullable=False)

    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    phone = db.Column(db.String(20))

    is_active = db.Column(db.Boolean, default=True, nullable=False)  # false if "dropped"
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Alert preferences (per spec: points-changed and order-placed are toggleable;
    # "dropped by sponsor" alert cannot be disabled, so it has no flag here)
    alert_points_changed = db.Column(db.Boolean, default=True)
    alert_order_placed = db.Column(db.Boolean, default=True)

    # relationships
    # Note: "sponsor" here means the SponsorUser login record (a sponsor
    # COMPANY can have several of these). The company itself is Sponsor.
    driver = db.relationship("Driver", back_populates="user", uselist=False)
    sponsor_user = db.relationship("SponsorUser", back_populates="user", uselist=False)
    admin = db.relationship("Admin", back_populates="user", uselist=False)
