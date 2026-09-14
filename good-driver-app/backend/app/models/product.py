from datetime import datetime
from app.extensions import db


class Product(db.Model):
    """
    A cached snapshot of a product pulled from the external catalog API
    (eBay/Etsy/Overstock/etc). Price and availability are refreshed live
    via catalog_api_service.py when a driver adds it to their cart/purchase
    set — this table is a display cache, not the source of truth for price.
    """
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    sponsor_id = db.Column(db.Integer, db.ForeignKey("sponsors.id"), nullable=False)

    external_product_id = db.Column(db.String(120), nullable=False)  # id from the 3rd-party API
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    image_url = db.Column(db.String(500))
    price_points = db.Column(db.Integer, nullable=False)  # converted from $ using sponsor.point_value_usd
    is_available = db.Column(db.Boolean, default=True)
    last_synced_at = db.Column(db.DateTime, default=datetime.utcnow)

    sponsor = db.relationship("Sponsor", back_populates="products")
    purchases = db.relationship("Purchase", back_populates="product")

    __table_args__ = (
        db.UniqueConstraint("sponsor_id", "external_product_id", name="uq_sponsor_product"),
    )
