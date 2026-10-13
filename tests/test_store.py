import re
from io import BytesIO

import pytest

from app import create_app
from database import PostgresConnection, get_db, init_db
from security import hash_password


@pytest.fixture()
def app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "store.sqlite3"),
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "SECRET_KEY": "test-secret",
            "SESSION_COOKIE_SECURE": False,
        }
    )
    with application.app_context():
        init_db()
        db = get_db()
        category_id = db.execute(
            "INSERT INTO categories (name) VALUES ('Accessoires')"
        ).lastrowid
        product_id = db.execute(
            """INSERT INTO products
               (name, description, purchase_price, sale_price, category_id,
                stock_quantity, low_stock_threshold)
               VALUES ('Sac test', 'Description', 25000, 49900, ?, 5, 1)""",
            (category_id,),
        ).lastrowid
        db.execute(
            "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
            ("admin", hash_password("correct horse battery")),
        )
        db.commit()
        application.config["TEST_PRODUCT_ID"] = product_id
    yield application


@pytest.fixture()
def client(app):
    return app.test_client()


def token_from(response):
    match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
    assert match
    return match.group(1).decode()


def test_postgres_adapter_translates_placeholders_and_case_insensitive_search():
    assert PostgresConnection._sql(
        "SELECT * FROM products WHERE name LIKE ? AND id = ?"
    ) == "SELECT * FROM products WHERE name ILIKE %s AND id = %s"
    assert PostgresConnection._sql("BEGIN IMMEDIATE") == "SELECT 1"


def test_init_db_adds_cost_columns_to_existing_store_without_losing_products(app):
    product_id = app.config["TEST_PRODUCT_ID"]
    with app.app_context():
        db = get_db()
        db.execute("ALTER TABLE products DROP COLUMN advertising_cost")
        db.execute("ALTER TABLE products DROP COLUMN packaging_cost")
        db.execute("ALTER TABLE order_items DROP COLUMN advertising_cost")
        db.execute("ALTER TABLE order_items DROP COLUMN packaging_cost")
        db.execute("ALTER TABLE orders DROP COLUMN return_fee")
        db.execute(
            "INSERT INTO settings (key, value) VALUES ('notification_emails', 'old@example.com')"
        )
        db.commit()

        init_db()

        product = get_db().execute(
            "SELECT name, advertising_cost, packaging_cost FROM products WHERE id=?",
            (product_id,),
        ).fetchone()
        assert tuple(product) == ("Sac test", 0, 0)
        assert get_db().execute(
            "SELECT value FROM settings WHERE key='return_fee'"
        ).fetchone()[0] == "0"
        assert get_db().execute(
            "SELECT COUNT(*) FROM settings WHERE key='notification_emails'"
        ).fetchone()[0] == 0


def test_app_startup_migrates_legacy_product_columns_before_ordering(client, app):
    product_id = app.config["TEST_PRODUCT_ID"]
    with app.app_context():
        db = get_db()
        db.execute("ALTER TABLE products DROP COLUMN advertising_cost")
        db.execute("ALTER TABLE products DROP COLUMN packaging_cost")
        db.commit()

    restarted_app = create_app(
        {
            "TESTING": True,
            "DATABASE": app.config["DATABASE"],
            "SECRET_KEY": "test-secret",
            "SESSION_COOKIE_SECURE": False,
        }
    )
    restarted_client = restarted_app.test_client()
    product_page = restarted_client.get(f"/produit/{product_id}")
    restarted_client.post(
        f"/panier/ajouter/{product_id}",
        data={"csrf_token": token_from(product_page), "quantity": "1"},
    )
    checkout_page = restarted_client.get("/commande")
    response = restarted_client.post(
        "/commande",
        data={
            "csrf_token": token_from(checkout_page),
            "full_name": "Client Migration",
            "phone": "+216 22111445",
            "address": "12 rue Migration",
            "city": "Tunis",
        },
    )

    assert response.status_code == 200
    with restarted_app.app_context():
        product = get_db().execute(
            "SELECT advertising_cost, packaging_cost FROM products WHERE id=?",
            (product_id,),
        ).fetchone()
        order_line = get_db().execute(
            "SELECT advertising_cost, packaging_cost FROM order_items"
        ).fetchone()
        assert tuple(product) == (0, 0)
        assert tuple(order_line) == (0, 0)


def test_public_order_confirmation_prints_driver_slip_and_updates_stock(client, app):
    product_id = app.config["TEST_PRODUCT_ID"]
    product_page = client.get(f"/produit/{product_id}")
    assert product_page.status_code == 200
    response = client.post(
        f"/panier/ajouter/{product_id}",
        data={"csrf_token": token_from(product_page), "quantity": "2"},
        follow_redirects=True,
    )
    assert b"Sac test" in response.data
    token = token_from(response)
    response = client.post(
        "/commande",
        data={
            "csrf_token": token,
            "full_name": "Client Test",
            "phone": "+216 22111222",
            "address": "10 rue Test",
            "city": "Tunis",
        },
    )
    assert response.status_code == 200
    assert b"CS-" in response.data
    with app.app_context():
        db = get_db()
        order = db.execute("SELECT * FROM orders").fetchone()
        line = db.execute("SELECT * FROM order_items").fetchone()
        assert order["status"] == "Nouvelle"
        assert order["total_amount"] == 107800
        assert order["delivery_address"] == "10 rue Test"
        assert line["product_name"] == "Sac test"
        assert line["unit_price"] == 49900
        assert db.execute("SELECT stock_quantity FROM products").fetchone()[0] == 5
        order_id = order["id"]

    login_page = client.get("/admin/login")
    client.post(
        "/admin/login",
        data={
            "csrf_token": token_from(login_page),
            "username": "admin",
            "password": "correct horse battery",
        },
    )
    order_page = client.get("/admin/commandes")
    assert "Refus ou retour".encode() in order_page.data
    assert "En préparation".encode() not in order_page.data
    assert "Expédiée".encode() not in order_page.data
    assert client.get(f"/admin/commandes/{order_id}/bon-livraison").status_code == 409
    invalid_transition = client.post(
        f"/admin/commandes/{order_id}/statut",
        data={"csrf_token": token_from(order_page), "status": "En préparation"},
        follow_redirects=True,
    )
    assert "Statut de commande invalide.".encode() in invalid_transition.data
    response = client.post(
        f"/admin/commandes/{order_id}/statut",
        data={"csrf_token": token_from(order_page), "status": "Confirmée"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "En préparation".encode() not in response.data
    assert "Livrée".encode() in response.data
    delivery_note = client.get(f"/admin/commandes/{order_id}/bon-livraison")
    assert delivery_note.status_code == 200
    assert b"Bon de livraison" in delivery_note.data
    assert b"Client Test" in delivery_note.data
    assert b"+216 22111222" in delivery_note.data
    assert b"10 rue Test" in delivery_note.data
    assert b"Sac test" in delivery_note.data
    assert b"99,800 DT" in delivery_note.data
    assert b"8,000 DT" in delivery_note.data
    assert b"107,800 DT" in delivery_note.data
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT stock_quantity FROM products").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0] == 1
        assert db.execute("SELECT stock_deducted FROM orders").fetchone()[0] == 1
    order_page = client.get("/admin/commandes")
    assert "En préparation".encode() not in order_page.data
    assert "Expédiée".encode() not in order_page.data
    response = client.post(
        f"/admin/commandes/{order_id}/statut",
        data={
            "csrf_token": token_from(order_page),
            "status": "Colis refusé ou retour",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT stock_quantity FROM products").fetchone()[0] == 5
        assert db.execute("SELECT stock_restored FROM orders").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0] == 2


def test_stock_restored_once_on_return_after_delivery(client, app):
    product_id = app.config["TEST_PRODUCT_ID"]
    product_page = client.get(f"/produit/{product_id}")
    client.post(
        f"/panier/ajouter/{product_id}",
        data={"csrf_token": token_from(product_page), "quantity": "1"},
    )
    checkout_page = client.get("/commande")
    client.post(
        "/commande",
        data={
            "csrf_token": token_from(checkout_page),
            "full_name": "Client Test",
            "phone": "+216 22111222",
            "address": "Adresse",
            "city": "Sfax",
        },
    )
    login_page = client.get("/admin/login")
    client.post(
        "/admin/login",
        data={
            "csrf_token": token_from(login_page),
            "username": "admin",
            "password": "correct horse battery",
        },
    )
    with app.app_context():
        order_id = get_db().execute("SELECT id FROM orders").fetchone()["id"]
    for next_status in ("Confirmée", "Livrée", "Colis refusé ou retour"):
        page = client.get("/admin/commandes")
        client.post(
            f"/admin/commandes/{order_id}/statut",
            data={"csrf_token": token_from(page), "status": next_status},
        )
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT stock_quantity FROM products").fetchone()[0] == 5
        assert db.execute("SELECT stock_restored FROM orders").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0] == 2
        assert db.execute("SELECT SUM(line_total) FROM order_items").fetchone()[0] == 49900


def test_confirmation_refuses_insufficient_stock_without_partial_changes(client, app):
    product_id = app.config["TEST_PRODUCT_ID"]
    product_page = client.get(f"/produit/{product_id}")
    client.post(
        f"/panier/ajouter/{product_id}",
        data={"csrf_token": token_from(product_page), "quantity": "1"},
    )
    checkout_page = client.get("/commande")
    client.post(
        "/commande",
        data={
            "csrf_token": token_from(checkout_page),
            "full_name": "Client Test",
            "phone": "+216 22111222",
            "address": "Adresse",
            "city": "Sousse",
        },
    )
    with app.app_context():
        db = get_db()
        db.execute("UPDATE products SET stock_quantity = 0 WHERE id = ?", (product_id,))
        db.commit()
    login_page = client.get("/admin/login")
    client.post(
        "/admin/login",
        data={
            "csrf_token": token_from(login_page),
            "username": "admin",
            "password": "correct horse battery",
        },
    )
    with app.app_context():
        order_id = get_db().execute("SELECT id FROM orders").fetchone()["id"]
    order_page = client.get("/admin/commandes")
    response = client.post(
        f"/admin/commandes/{order_id}/statut",
        data={"csrf_token": token_from(order_page), "status": "Confirmée"},
        follow_redirects=True,
    )
    assert "Stock insuffisant".encode() in response.data
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT stock_quantity FROM products").fetchone()[0] == 0
        assert db.execute("SELECT status FROM orders").fetchone()[0] == "Nouvelle"
        assert db.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0] == 0


def test_admin_requires_login_and_post_requires_csrf(client):
    protected_pages = (
        "/admin/",
        "/admin/produits",
        "/admin/produits/nouveau",
        "/admin/categories",
        "/admin/commandes",
        "/admin/commandes/1/bon-livraison",
        "/admin/clients",
        "/admin/stocks",
        "/admin/parametres",
        "/admin/statistiques",
        "/admin/statistiques/export.csv",
    )
    for path in protected_pages:
        response = client.get(path)
        assert response.status_code == 302, path
        assert "/admin/login" in response.location, path

    assert client.get("/").status_code == 200
    assert client.get("/admin/login").status_code == 200
    assert client.get("/healthz").json == {"status": "ok"}
    response = client.post("/admin/login", data={"username": "admin", "password": "bad"})
    assert response.status_code == 400
    login_page = client.get("/admin/login")
    response = client.post(
        "/admin/login",
        data={
            "csrf_token": token_from(login_page),
            "username": "admin",
            "password": "incorrect",
        },
    )
    assert b"Identifiants incorrects" in response.data


def test_local_sqlite_environment_uses_configured_paths_and_secure_session_cookie(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "persistent" / "celina_store.sqlite3"
    upload_folder = tmp_path / "persistent" / "uploads"
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-render-secret")
    monkeypatch.setenv("FLASK_DEBUG", "0")
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("UPLOAD_FOLDER", str(upload_folder))

    render_app = create_app()

    assert render_app.config["DATABASE"] == str(database_path)
    assert render_app.config["UPLOAD_FOLDER"] == str(upload_folder)
    assert render_app.config["SESSION_COOKIE_SECURE"] is True
    assert database_path.exists()
    assert upload_folder.is_dir()


def test_statistics_only_count_delivered_sales_and_export(client, app):
    with app.app_context():
        get_db().execute(
            "UPDATE products SET advertising_cost=3500, packaging_cost=1200"
        )
        get_db().commit()
    product_id = app.config["TEST_PRODUCT_ID"]
    product_page = client.get(f"/produit/{product_id}")
    client.post(
        f"/panier/ajouter/{product_id}",
        data={"csrf_token": token_from(product_page), "quantity": "1"},
    )
    checkout_page = client.get("/commande")
    client.post(
        "/commande",
        data={
            "csrf_token": token_from(checkout_page),
            "full_name": "Client Test",
            "phone": "+216 22111222",
            "address": "Adresse",
            "city": "Tunis",
        },
    )
    login_page = client.get("/admin/login")
    client.post(
        "/admin/login",
        data={
            "csrf_token": token_from(login_page),
            "username": "admin",
            "password": "correct horse battery",
        },
    )
    with app.app_context():
        order_id = get_db().execute("SELECT id FROM orders").fetchone()["id"]
    for next_status in ("Confirmée", "Livrée"):
        page = client.get("/admin/commandes")
        client.post(
            f"/admin/commandes/{order_id}/statut",
            data={"csrf_token": token_from(page), "status": next_status},
        )
    stats = client.get("/admin/statistiques")
    assert b"49,900 DT" in stats.data
    assert b"25,000 DT" in stats.data
    assert b"3,500 DT" in stats.data
    assert b"1,200 DT" in stats.data
    assert b"20,200 DT" in stats.data
    export = client.get("/admin/statistiques/export.csv")
    assert export.status_code == 200
    assert "Publicité unitaire DT".encode() in export.data
    assert b"Client Test" in export.data
    assert b"Sac test" in export.data


def test_return_expenses_are_snapshotted_and_deducted_from_real_margin(client, app):
    with app.app_context():
        db = get_db()
        db.execute(
            "UPDATE products SET advertising_cost=3500, packaging_cost=1200"
        )
        db.execute(
            "INSERT INTO settings (key, value) VALUES ('return_fee', '6500') "
            "ON CONFLICT(key) DO UPDATE SET value='6500'"
        )
        db.commit()
    product_id = app.config["TEST_PRODUCT_ID"]
    product_page = client.get(f"/produit/{product_id}")
    client.post(
        f"/panier/ajouter/{product_id}",
        data={"csrf_token": token_from(product_page), "quantity": "1"},
    )
    checkout_page = client.get("/commande")
    client.post(
        "/commande",
        data={
            "csrf_token": token_from(checkout_page),
            "full_name": "Client Retour",
            "phone": "+216 22111333",
            "address": "Adresse retour",
            "city": "Tunis",
        },
    )
    login_page = client.get("/admin/login")
    client.post(
        "/admin/login",
        data={
            "csrf_token": token_from(login_page),
            "username": "admin",
            "password": "correct horse battery",
        },
    )
    with app.app_context():
        order_id = get_db().execute("SELECT id FROM orders").fetchone()["id"]
    order_page = client.get("/admin/commandes")
    client.post(
        f"/admin/commandes/{order_id}/statut",
        data={"csrf_token": token_from(order_page), "status": "Confirmée"},
    )
    order_page = client.get("/admin/commandes")
    client.post(
        f"/admin/commandes/{order_id}/statut",
        data={
            "csrf_token": token_from(order_page),
            "status": "Colis refusé ou retour",
        },
    )
    with app.app_context():
        db = get_db()
        order = db.execute(
            "SELECT return_fee FROM orders WHERE id=?", (order_id,)
        ).fetchone()
        item = db.execute(
            "SELECT advertising_cost, packaging_cost FROM order_items WHERE order_id=?",
            (order_id,),
        ).fetchone()
        assert order["return_fee"] == 6500
        assert item["advertising_cost"] == 3500
        assert item["packaging_cost"] == 1200
        db.execute("UPDATE settings SET value='9000' WHERE key='return_fee'")
        db.commit()
        assert get_db().execute(
            "SELECT return_fee FROM orders WHERE id=?", (order_id,)
        ).fetchone()[0] == 6500

    stats = client.get("/admin/statistiques")
    assert b"11,200 DT" in stats.data
    assert b"-11,200 DT" in stats.data


def test_store_rejects_missing_csrf_token(client, app):
    response = client.post(
        f"/panier/ajouter/{app.config['TEST_PRODUCT_ID']}", data={"quantity": "1"}
    )
    assert response.status_code == 400


def test_admin_pages_render_after_login(client):
    login_page = client.get("/admin/login")
    client.post(
        "/admin/login",
        data={
            "csrf_token": token_from(login_page),
            "username": "admin",
            "password": "correct horse battery",
        },
    )
    for path in ("/admin/", "/admin/produits", "/admin/categories", "/admin/produits/nouveau", "/admin/stocks", "/admin/commandes", "/admin/clients", "/admin/statistiques", "/admin/parametres"):
        response = client.get(path)
        assert response.status_code == 200, path


def test_delivery_fee_setting_updates_the_checkout_total(client, app):
    login_page = client.get("/admin/login")
    client.post(
        "/admin/login",
        data={
            "csrf_token": token_from(login_page),
            "username": "admin",
            "password": "correct horse battery",
        },
    )
    settings_page = client.get("/admin/parametres")
    response = client.post(
        "/admin/parametres",
        data={
            "csrf_token": token_from(settings_page),
            "delivery_fee": "12.500",
            "return_fee": "6.500",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        assert get_db().execute(
            "SELECT value FROM settings WHERE key='delivery_fee'"
        ).fetchone()[0] == "12500"
        assert get_db().execute(
            "SELECT value FROM settings WHERE key='return_fee'"
        ).fetchone()[0] == "6500"


def test_admin_can_create_promotion_and_upload_product_image(client, app):
    login_page = client.get("/admin/login")
    client.post(
        "/admin/login",
        data={
            "csrf_token": token_from(login_page),
            "username": "admin",
            "password": "correct horse battery",
        },
    )
    form = client.get("/admin/produits/nouveau")
    response = client.post(
        "/admin/produits/nouveau",
        data={
            "csrf_token": token_from(form),
            "name": "Produit promotionnel",
            "description": "Offre du test",
            "purchase_price": "10.000",
            "advertising_cost": "2.500",
            "packaging_cost": "0.800",
            "sale_price": "15.000",
            "compare_at_price": "20.000",
            "category_id": "",
            "stock_quantity": "4",
            "low_stock_threshold": "2",
            "image": (BytesIO(b"\x89PNG\r\n\x1a\nimage-data"), "offre.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Produit promotionnel" in client.get("/").data
    with app.app_context():
        db = get_db()
        product = db.execute(
            "SELECT * FROM products WHERE name='Produit promotionnel'"
        ).fetchone()
        assert product["sale_price"] == 15000
        assert product["advertising_cost"] == 2500
        assert product["packaging_cost"] == 800
        assert product["compare_at_price"] == 20000
        assert product["image_filename"].endswith(".png")
        uploaded_image = client.get(
            f"/uploads/{product['image_filename']}"
        )
        assert uploaded_image.status_code == 200
        assert uploaded_image.data == b"\x89PNG\r\n\x1a\nimage-data"
        movement = db.execute(
            "SELECT movement_type, quantity FROM stock_movements WHERE product_id=?",
            (product["id"],),
        ).fetchone()
        assert tuple(movement) == ("ADJUSTMENT", 4)
    assert b"Produit promotionnel" in client.get("/admin/stocks").data


def test_admin_can_add_multiple_product_photos_and_customer_can_view_gallery(client, app):
    login_page = client.get("/admin/login")
    client.post(
        "/admin/login",
        data={
            "csrf_token": token_from(login_page),
            "username": "admin",
            "password": "correct horse battery",
        },
    )
    product_id = app.config["TEST_PRODUCT_ID"]
    form = client.get(f"/admin/produits/{product_id}/modifier")
    response = client.post(
        f"/admin/produits/{product_id}/modifier",
        data={
            "csrf_token": token_from(form),
            "name": "Sac test",
            "description": "Description",
            "purchase_price": "25",
            "sale_price": "49.900",
            "category_id": "",
            "stock_quantity": "5",
            "low_stock_threshold": "1",
            "images": [
                (BytesIO(b"\x89PNG\r\n\x1a\nphoto-one"), "face.png"),
                (BytesIO(b"\x89PNG\r\n\x1a\nphoto-two"), "detail.png"),
            ],
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 302
    with app.app_context():
        db = get_db()
        product = db.execute(
            "SELECT image_filename FROM products WHERE id=?", (product_id,)
        ).fetchone()
        extra_images = db.execute(
            "SELECT image_filename FROM product_images WHERE product_id=? ORDER BY id",
            (product_id,),
        ).fetchall()
        assert product["image_filename"].endswith(".png")
        assert len(extra_images) == 1

    product_page = client.get(f"/produit/{product_id}")
    assert product_page.status_code == 200
    assert b"product-thumbnails" in product_page.data
    assert b"Afficher la photo 1" in product_page.data
    assert b"Afficher la photo 2" in product_page.data


def test_admin_rejects_promotion_price_below_sale_price(client):
    login_page = client.get("/admin/login")
    client.post(
        "/admin/login",
        data={
            "csrf_token": token_from(login_page),
            "username": "admin",
            "password": "correct horse battery",
        },
    )
    form = client.get("/admin/produits/nouveau")
    response = client.post(
        "/admin/produits/nouveau",
        data={
            "csrf_token": token_from(form),
            "name": "Promotion invalide",
            "purchase_price": "10",
            "sale_price": "20",
            "compare_at_price": "15",
            "stock_quantity": "1",
        },
    )
    assert response.status_code == 200
    assert "prix barré".encode() in response.data


def test_manual_stock_reduction_is_logged_as_stock_out(client, app):
    login_page = client.get("/admin/login")
    client.post(
        "/admin/login",
        data={
            "csrf_token": token_from(login_page),
            "username": "admin",
            "password": "correct horse battery",
        },
    )
    product_id = app.config["TEST_PRODUCT_ID"]
    form = client.get(f"/admin/produits/{product_id}/modifier")
    response = client.post(
        f"/admin/produits/{product_id}/modifier",
        data={
            "csrf_token": token_from(form),
            "name": "Sac test",
            "description": "Description",
            "purchase_price": "25",
            "sale_price": "49.900",
            "advertising_cost": "4.250",
            "packaging_cost": "1.250",
            "compare_at_price": "",
            "category_id": "",
            "stock_quantity": "3",
            "low_stock_threshold": "1",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        db = get_db()
        movement = db.execute(
            "SELECT movement_type, quantity FROM stock_movements WHERE product_id=?",
            (product_id,),
        ).fetchone()
        assert tuple(movement) == ("OUT", 2)
        assert db.execute(
            "SELECT stock_quantity FROM products WHERE id=?", (product_id,)
        ).fetchone()[0] == 3
        costs = db.execute(
            "SELECT advertising_cost, packaging_cost FROM products WHERE id=?",
            (product_id,),
        ).fetchone()
        assert tuple(costs) == (4250, 1250)


def test_category_management_and_product_creation_flow(client, app):
    login_page = client.get("/admin/login")
    client.post(
        "/admin/login",
        data={
            "csrf_token": token_from(login_page),
            "username": "admin",
            "password": "correct horse battery",
        },
    )
    dashboard = client.get("/admin/")
    assert b"Ajouter un produit" in dashboard.data
    assert "Créer une catégorie".encode() in dashboard.data

    categories_page = client.get("/admin/categories")
    assert categories_page.status_code == 200
    response = client.post(
        "/admin/categories",
        data={
            "csrf_token": token_from(categories_page),
            "name": "Nouvelle collection",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Nouvelle collection" in response.data

    with app.app_context():
        category_id = get_db().execute(
            "SELECT id FROM categories WHERE name='Nouvelle collection'"
        ).fetchone()["id"]
    product_page = client.get("/admin/produits/nouveau")
    assert b"Nouvelle collection" in product_page.data
    response = client.post(
        "/admin/produits/nouveau",
        data={
            "csrf_token": token_from(product_page),
            "name": "Produit de la collection",
            "description": "Produit ajouté depuis le formulaire.",
            "purchase_price": "12",
            "sale_price": "24",
            "category_id": str(category_id),
            "stock_quantity": "5",
            "low_stock_threshold": "2",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Produit de la collection" in response.data
    storefront = client.get(f"/?category={category_id}")
    assert b"Produit de la collection" in storefront.data
    assert b"Nouvelle collection" in storefront.data
