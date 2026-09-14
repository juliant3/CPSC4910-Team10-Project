from datetime import datetime
from app.extensions import db


class DriverApplication(db.Model):
    __tablename__ = "driver_applications"

    id = db.Column(db.Integer, primary_key=True)
    driver_id = db.Column(db.Integer, db.ForeignKey("drivers.id"), nullable=False)
    sponsor_id = db.Column(db.Integer, db.ForeignKey("sponsors.id"), nullable=False)

    status = db.Column(
        db.Enum("pending", "accepted", "rejected", name="application_status"),
        default="pending", nullable=False,
    )
    reason = db.Column(db.String(255))  # reason for accept/reject decision
    reviewed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime)

    driver = db.relationship("Driver", back_populates="applications")
    sponsor = db.relationship("Sponsor", back_populates="applications")
