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


def test_register_contains_core_account_fields():
    html = read_template("shared/register.html")
    for field in (
        'name="role"',
        'name="first_name"',
        'name="last_name"',
        'name="email"',
        'name="password"',
        'name="confirm_password"',
        'name="invite_code"',
        'name="company_name"',
    ):
        assert field in html


def test_driver_dashboard_links_to_profile():
    html = read_template("driver/dashboard.html")
    assert "url_for('driver_profile')" in html


def test_sponsor_dashboard_links_to_applications_and_drivers():
    html = read_template("sponsor/dashboard.html")
    assert "url_for('sponsor_applications')" in html
    assert "url_for('sponsor_drivers')" in html
