from flask import Flask, render_template, request, redirect, url_for, session
import pymysql
from werkzeug.security import check_password_hash

app = Flask(__name__)
app.secret_key = "change_this_to_a_secure_random_value"

DB_CONFIG = {
    "host": "cpsc4910-f26.cobd8enwsupz.us-east-1.rds.amazonaws.com",
    "user": "Team10",
    "password": "CPSC4910TEAM10",
    "database": "Team10_DB",
}

def get_db():
    return pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor)

@app.route("/")
def home():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template("shared/home.html",
                            first_name=session["first_name"],
                            role=session["role"])

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("username", "")
        password = request.form.get("password", "")

        conn = get_db()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """SELECT Users.user_id, Users.first_name, Users.role, Password.password_hash
                       FROM Users JOIN Password ON Users.user_id = Password.user_id
                       WHERE Users.email = %s""",
                    (email,)
                )
                user = cursor.fetchone()
        finally:
            conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["user_id"]
            session["first_name"] = user["first_name"]
            session["role"] = user["role"]
            return redirect(url_for("home"))

        return render_template("shared/login.html", error="Invalid email or password.")

    return render_template("shared/login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(debug=True)