from functools import wraps

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for

from database import get_db
from security import hash_password, verify_password

auth_bp = Blueprint("auth", __name__)


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_id"):
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = get_db().execute(
            "SELECT * FROM admin_users WHERE username = ?", (username,)
        ).fetchone()
        if user is not None and verify_password(password, user["password_hash"]):
            session.clear()
            session["admin_id"] = user["id"]
            session["admin_username"] = user["username"]
            flash("Connexion réussie.", "success")
            return redirect(url_for("admin.dashboard"))
        flash("Identifiants incorrects.", "error")
    return render_template("admin/login.html")


@auth_bp.post("/logout")
@admin_required
def logout():
    session.clear()
    flash("Vous êtes déconnecté·e.", "success")
    return redirect(url_for("auth.login"))
