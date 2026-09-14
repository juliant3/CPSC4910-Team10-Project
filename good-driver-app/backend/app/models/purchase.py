from datetime import datetime
from app.extensions import db


class Purchase(db.Model):
    __tablename__ = "purchases"

    id = db.Column(db.Integer, primary_key=True)
    driver_id = db.Column(db.Integer, db.ForeignKey("drivers.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)

    quantity = db.Column(db.Integer, default=1, nullable=False)
    points_spent = db.Column(db.Integer, nullable=False)  # snapshot at time of purchase
    status = db.Column(
        db.Enum("pending", "processing", "shipped", "cancelled", name="purchase_status"),
        default="pending", nullable=False,
    )
    # who placed it — usually the driver, but a sponsor user can purchase
    # on the driver's behalf per the spec
    placed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    driver = db.relationship("Driver", back_populates="purchases")
    product = db.relationship("Product", back_populates="purchases")
