"""
- hash_password(plain) / check_password(plain, hash)   (via flask_bcrypt)
- validate_password_complexity(plain) -> raises/returns errors based on
    Config.PASSWORD_MIN_LENGTH / REQUIRE_UPPER / REQUIRE_LOWER /
    REQUIRE_DIGIT / REQUIRE_SYMBOL
- encrypt_field(plain) / decrypt_field(cipher)
    for any additional sensitive fields you add later (e.g. phone,
    DL number) - use a symmetric scheme (e.g. Fernet) keyed off an
    env-var secret, never store that key in git
- generate_password_reset_token(user) / verify_password_reset_token(token)
"""
