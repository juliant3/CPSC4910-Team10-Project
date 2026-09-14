"""
- authenticate(username, password) -> User or None
    looks up by username only (no role picker), checks bcrypt hash,
    logs a login_attempt audit row either way
- issue_token(user) -> JWT containing user_id + role
- request_password_reset(email) -> generates a time-limited token,
    emails it via email_service
- reset_password(token, new_password) -> validates token + complexity
    (utils/validators.py), updates password_hash, logs password_change
"""
