import os
import hmac
import sqlite3
from pathlib import Path
from decimal import Decimal
from datetime import datetime

import click
from flask import Flask, jsonify

from database import close_db, get_db, init_db
from routes.admin import admin_bp
from routes.auth import auth_bp
from routes.store import store_bp


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("FLASK_SECRET_KEY"),
        DATABASE=os.environ.get("DATABASE_PATH", "instance/celina_store.sqlite3"),
        UPLOAD_FOLDER=os.environ.get("UPLOAD_FOLDER"),
        MAX_CONTENT_LENGTH=42 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("FLASK_DEBUG", "0") != "1",
    )
    if test_config:
        app.config.update(test_config)
    if not app.config.get("SECRET_KEY"):
        if app.config.get("TESTING"):
            app.config["SECRET_KEY"] = "test-only-secret"
        else:
            raise RuntimeError("Configurez FLASK_SECRET_KEY dans l'environnement.")

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    database_path = app.config["DATABASE"]
    if not os.path.isabs(database_path):
        app.config["DATABASE"] = str(Path(app.root_path, database_path).resolve())
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    upload_folder = app.config["UPLOAD_FOLDER"] or str(Path(app.static_folder, "uploads"))
    if not os.path.isabs(upload_folder):
        upload_folder = str(Path(app.root_path, upload_folder).resolve())
    app.config["UPLOAD_FOLDER"] = upload_folder
    Path(upload_folder).mkdir(parents=True, exist_ok=True)

    with app.app_context():
        init_db()

    @app.template_filter("dt")
    def format_dt(value):
        return f"{Decimal(str(value or 0)) / Decimal(1000):,.3f}".replace(",", " ").replace(".", ",")

    app.teardown_appcontext(close_db)
    app.register_blueprint(store_bp)
    app.register_blueprint(auth_bp, url_prefix="/admin")
    app.register_blueprint(admin_bp, url_prefix="/admin")

    @app.get("/healthz")
    def health_check():
        get_db().execute("SELECT 1")
        return jsonify(status="ok")

    @app.before_request
    def enforce_csrf():
        from flask import abort, request, session

        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
            expected = session.get("csrf_token")
            if not expected or not supplied or not hmac.compare_digest(supplied, expected):
                abort(400, description="Jeton de sécurité invalide. Rechargez la page.")

    @app.context_processor
    def inject_csrf_token():
        from security import csrf_token

        def order_lines(order_id):
            return get_db().execute(
                "SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (order_id,)
            ).fetchall()

        def customer_address(order_id):
            row = get_db().execute(
                "SELECT delivery_address, delivery_city FROM orders WHERE id = ?", (order_id,)
            ).fetchone()
            return f"{row['delivery_address']}, {row['delivery_city']}" if row else ""

        return {
            "csrf_token": csrf_token,
            "get_db_lines": order_lines,
            "get_customer_address": customer_address,
        }

    @app.cli.command("init-db")
    def init_db_command():
        init_db()
        click.echo("Base CELINA-STORE initialisée.")

    @app.cli.command("create-admin")
    @click.argument("username")
    @click.password_option()
    def create_admin_command(username, password):
        from security import hash_password

        db = get_db()
        db.execute(
            "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
            (username.strip(), hash_password(password)),
        )
        db.commit()
        click.echo(f"Administrateur {username.strip()} créé.")

    @app.cli.command("seed-demo")
    def seed_demo_command():
        init_db()
        db = get_db()
        category = db.execute(
            "INSERT INTO categories (name) VALUES (?)", ("Démo - Accessoires",)
        ).lastrowid
        db.executemany(
            """INSERT INTO products
               (name, description, purchase_price, sale_price, category_id, stock_quantity,
                low_stock_threshold, is_active, is_demo)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1)""",
            [
                ("Démo - Sac Celina", "Produit de démonstration.", 35000, 59000, category, 12, 3),
                ("Démo - Écharpe Celina", "Produit de démonstration.", 12000, 29000, category, 8, 2),
            ],
        )
        db.commit()
        click.echo("Produits de démonstration créés (clairement identifiés).")

    @app.cli.command("backup-db")
    def backup_db_command():
        source = sqlite3.connect(app.config["DATABASE"])
        backup_dir = Path(app.instance_path, "backups")
        backup_dir.mkdir(parents=True, exist_ok=True)
        destination_path = backup_dir / f"celina-store-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite3"
        try:
            destination = sqlite3.connect(destination_path)
            try:
                source.backup(destination)
            finally:
                destination.close()
        finally:
            source.close()
        click.echo(f"Sauvegarde créée : {destination_path}")

    return app


if __name__ == "__main__":
    create_app().run()
