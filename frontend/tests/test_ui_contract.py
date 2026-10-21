from pathlib import Path


TEMPLATES = Path(__file__).resolve().parents[1] / "templates"


def read_template(relative_path):
    return (TEMPLATES / relative_path).read_text(encoding="utf-8")


def test_login_has_show_hide_password_control():
    html = read_template("shared/login.html")
    assert 'id="password"' in html
    assert "togglePassword('password', this)" in html
    assert "Show" in html
    assert "Hide" in html


def test_login_does_not_offer_public_sign_up():
    html = read_template("shared/login.html")
    assert "url_for('register')" not in html
    assert "url_for('sponsor_request')" in html


def test_driver_dashboard_links_to_profile():
    html = read_template("driver/dashboard.html")
    assert "url_for('driver_profile')" in html


def test_sponsor_dashboard_links_to_applications_and_drivers():
    html = read_template("sponsor/dashboard.html")
    assert "url_for('sponsor_applications')" in html
    assert "url_for('sponsor_drivers')" in html
