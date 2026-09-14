from datetime import datetime
from app.extensions import db


class PointTransaction(db.Model):
    """
    Every point change (add or deduct) gets a row here. This is what
    the "Driver Point Tracking" sponsor report reads from — it already
    has date, driver, points, and reason, plus who made the change.
    """
    __tablename__ = "point_transactions"

    id = db.Column(db.Integer, primary_key=True)
    driver_id = db.Column(db.Integer, db.ForeignKey("drivers.id"), nullable=False)
    changed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    points = db.Column(db.Integer, nullable=False)  # positive = added, negative = deducted
    reason = db.Column(db.String(255), nullable=False)  # required per spec
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    driver = db.relationship("Driver", back_populates="point_transactions")
