from app.extensions import db


class Sponsor(db.Model):
    __tablename__ = "sponsors"

    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(150), nullable=False, unique=True)
    point_value_usd = db.Column(db.Numeric(6, 4), default=0.01, nullable=False)
    catalog_api_source = db.Column(db.String(100))  # e.g. "ebay", "etsy" - which API this sponsor's catalog pulls from

    # sponsor users (there can be multiple logins per sponsor company)
    sponsor_users = db.relationship("SponsorUser", back_populates="sponsor")
    drivers = db.relationship("Driver", back_populates="sponsor")
    products = db.relationship("Product", back_populates="sponsor")
    applications = db.relationship("DriverApplication", back_populates="sponsor")


class SponsorUser(db.Model):
    """
    A login belonging to a sponsor company. Separate from Sponsor itself
    because a sponsor company can have multiple sponsor user logins.
    """
    __tablename__ = "sponsor_users"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True)
    sponsor_id = db.Column(db.Integer, db.ForeignKey("sponsors.id"), nullable=False)

    user = db.relationship("User", back_populates="sponsor_user")
    sponsor = db.relationship("Sponsor", back_populates="sponsor_users")
