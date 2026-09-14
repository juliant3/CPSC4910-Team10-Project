from app.extensions import db


class AboutInfo(db.Model):
    """
    Single-row (or one-row-per-sprint) table backing the About page.
    Spec requires this data come from the database, not be hardcoded,
    and be updated every sprint.
    """
    __tablename__ = "about_info"

    id = db.Column(db.Integer, primary_key=True)
    team_number = db.Column(db.String(20), nullable=False)
    version_number = db.Column(db.String(20), nullable=False)  # aka Sprint #
    release_date = db.Column(db.Date, nullable=False)
    product_name = db.Column(db.String(150), nullable=False)
    product_description = db.Column(db.Text, nullable=False)
