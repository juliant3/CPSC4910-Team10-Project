import os
import pymysql

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "cpsc4910-f26.cobd8enwsupz.us-east-1.rds.amazonaws.com"),
    "user": os.environ.get("DB_USER", "Team10"),
    "password": os.environ.get("DB_PASSWORD"),
    "database": os.environ.get("DB_NAME", "Team10_DB"),
}

email = "testdriver@test.com"

conn = pymysql.connect(
    **DB_CONFIG,
    cursorclass=pymysql.cursors.DictCursor
)

try:
    with conn.cursor() as cursor:

        cursor.execute(
            """
            UPDATE Drivers
            JOIN Users
                ON Drivers.driver_id = Users.user_id
            SET Drivers.status = 'Active'
            WHERE Users.email = %s
            """,
            (email,)
        )

        conn.commit()

        print("Rows updated:", cursor.rowcount)

        # Verify the change
        cursor.execute(
            """
            SELECT
                Users.user_id,
                Users.email,
                Users.role,
                Drivers.points_balance,
                Drivers.sponsor_id,
                Drivers.status
            FROM Users
            JOIN Drivers
                ON Users.user_id = Drivers.driver_id
            WHERE Users.email = %s
            """,
            (email,)
        )

        driver = cursor.fetchone()

        print("Driver:", driver)

finally:
    conn.close()