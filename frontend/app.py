# Import assets
from flask import Flask, render_template, request, redirect, url_for, session

app = Flask(__name__)

# Replace this with a secure random value in production.
app.secret_key = "change_this_to_a_secure_random_value"

# Demo creds
USERNAME = "admin"
PASSWORD = "password"

# Routing for home
@app.route("/")
def home():
    if "username" not in session:
        return redirect(url_for("login"))

    return render_template("shared/home.html", username=session["username"])

# Routing for login
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")

        if username == USERNAME and password == PASSWORD:
            session["username"] = username
            return redirect(url_for("home"))

        return render_template(
            "shared/login.html",
            error="Invalid username or password."
        )

    return render_template("shared/login.html")

# Routing for logout
@app.route("/logout")
def logout():
    session.pop("username", None)
    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(debug=True)
