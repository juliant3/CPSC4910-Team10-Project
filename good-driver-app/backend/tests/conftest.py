"""
- app fixture: create_app(env="test") -> uses the in-memory SQLite config
    from TestingConfig so tests never touch real RDS data
- client fixture: app.test_client()
- db fixture: creates all tables before each test, drops after
- factory fixtures for a sample sponsor/driver/admin/product, so each
  test file isn't rebuilding the same setup
"""
