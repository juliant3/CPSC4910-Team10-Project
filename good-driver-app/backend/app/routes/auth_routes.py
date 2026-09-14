"""
Shared login for all roles. Per spec: the UI must NOT ask the user to
pick their "type" to log in — look up the username, find its role, and
issue a token/session accordingly. All login attempts (success or
failure) get written to the audit log here.
"""
from flask import Blueprint

auth_bp = Blueprint("auth", __name__)

# POST /api/auth/register            (driver self-registration -> creates a pending DriverApplication)
# POST /api/auth/login               (username + password only, no role selector)
# POST /api/auth/logout
# POST /api/auth/password/forgot     (sends reset link/token via email_service)
# POST /api/auth/password/reset      (consumes reset token, sets new password, logs password_change)
# GET  /api/auth/me                  (returns current user + role, for frontend routing)
