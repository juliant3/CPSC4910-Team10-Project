"""
- send_password_reset_email(user, token)
- send_dropped_alert(driver)                      (always sent, cannot be disabled)
- send_points_changed_alert(driver, amount, reason) (only if driver.alert_points_changed)
- send_order_placed_alert(driver, purchase)         (only if driver.alert_order_placed)
"""
