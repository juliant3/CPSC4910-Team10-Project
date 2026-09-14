from app.extensions import db


class Driver(db.Model):
    __tablename__ = "drivers"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    sponsor_id = db.Column(db.Integer, db.ForeignKey("sponsors.id"), nullable=True)
    # nullable because a driver may apply before being accepted by a sponsor

    points_balance = db.Column(db.Integer, default=0, nullable=False)

    user = db.relationship("User", back_populates="driver")
    sponsor = db.relationship("Sponsor", back_populates="drivers")
    purchases = db.relationship("Purchase", back_populates="driver")
    point_transactions = db.relationship("PointTransaction", back_populates="driver")
    applications = db.relationship("DriverApplication", back_populates="driver")
