import pymysql
from werkzeug.security import generate_password_hash
import getpass

DB_CONFIG = {
    "host": "cpsc4910-f26.cobd8enwsupz.us-east-1.rds.amazonaws.com",
    "user": "Team10",
    "password": "CPSC4910TEAM10",
    "database": "Team10_DB",
}

def add_user(email, first_name, last_name, role, password):
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO Users (email, first_name, last_name, role) VALUES (%s, %s, %s, %s)",
                (email, first_name, last_name, role)
            )
            user_id = cursor.lastrowid

            password_hash = generate_password_hash(password, method='pbkdf2:sha256')
            cursor.execute(
                "INSERT INTO Password (user_id, password_hash) VALUES (%s, %s)",
                (user_id, password_hash)
            )
        conn.commit()
        print(f"User '{email}' created with ID {user_id}")
    finally:
        conn.close()

if __name__ == "__main__":
    email = input("Email: ")
    first_name = input("First name: ")
    last_name = input("Last name: ")
    role = input("Role (Admin/Driver/Sponsor): ")
    password = getpass.getpass("Password: ")
    add_user(email, first_name, last_name, role, password)