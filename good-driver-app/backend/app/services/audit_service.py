"""
Every other service (auth, points, sponsor application review) should
call into this rather than writing AuditLog rows itself, so the shape
of an audit entry stays consistent.

- log_login_attempt(username_attempted, success)
- log_password_change(user_id, change_type)
- log_driver_application(driver_id, sponsor_id, status, reason)
- log_point_change(driver_id, changed_by_user_id, points, reason)
"""
