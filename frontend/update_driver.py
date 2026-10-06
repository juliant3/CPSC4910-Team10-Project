import pymysql

DB_CONFIG = {
    "host": "cpsc4910-f26.cobd8enwsupz.us-east-1.rds.amazonaws.com",
    "user": "Team10",
    "password": "CPSC4910TEAM10",
    "database": "Team10_DB",
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