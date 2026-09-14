"""
Centralizes all point mutations so balance + PointTransaction + AuditLog
+ alert-triggering always happen together, atomically. Routes should
never touch Driver.points_balance directly.

- add_points(driver_id, amount, reason, changed_by_user_id)
- deduct_points(driver_id, amount, reason, changed_by_user_id)
    both: validate amount>0 and reason is non-empty, wrap in a DB
    transaction, write PointTransaction row, write AuditLog
    (category="point_change"), then trigger the points-changed alert
    (respecting the driver's alert_points_changed toggle)
- get_balance(driver_id)
- get_history(driver_id, start=None, end=None)
"""
