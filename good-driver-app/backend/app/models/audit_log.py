from datetime import datetime
from app.extensions import db


class AuditLog(db.Model):
    """
    One table for all four required audit categories, distinguished by
    `category`. Keeping it in one table makes the "Select by audit log
    category" report filter trivial (a WHERE clause instead of a UNION
    across four tables).
    """
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(
        db.Enum(
            "driver_application", "point_change", "password_change", "login_attempt",
            name="audit_category",
        ),
        nullable=False,
        index=True,
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # Generic actor/subject references — nullable because not every
    # category needs every field (e.g. login_attempt has no sponsor).
    sponsor_id = db.Column(db.Integer, db.ForeignKey("sponsors.id"), nullable=True)
    driver_id = db.Column(db.Integer, db.ForeignKey("drivers.id"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    # category-specific detail fields
    username_attempted = db.Column(db.String(80))  # login_attempt
    success = db.Column(db.Boolean)                # login_attempt
    status = db.Column(db.String(20))              # driver_application: accept/reject
    reason = db.Column(db.String(255))             # driver_application / point_change
    points_changed = db.Column(db.Integer)          # point_change
    change_type = db.Column(db.String(50))          # password_change: e.g. "self-reset", "forced"
