"""
- @login_required            (wraps flask_jwt_extended's jwt_required with
                               your own user-loading convenience)
- @role_required("sponsor")  (checks the JWT's role claim before allowing
                               the route to run; use for every sponsor/
                               admin-only endpoint)
- @role_required("admin")

Note on SQL injection: as long as every query goes through SQLAlchemy's
ORM/query builder (never raw string-formatted SQL), parameterization is
automatic. If you ever need raw SQL, always use bound parameters
(db.text("... WHERE id = :id"), {"id": id}) - never an f-string.
"""
