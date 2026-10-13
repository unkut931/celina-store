import csv
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import StringIO
from pathlib import Path
from uuid import uuid4

from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from database import get_db
from orders import ORDER_STATUSES, TRANSITIONS, change_order_status
from routes.auth import admin_required

admin_bp = Blueprint("admin", __name__)
MAX_PRODUCT_IMAGES = 8
MAX_IMAGE_SIZE = 5 * 1024 * 1024


def _money(value):
    try:
        amount = Decimal(value)
    except (TypeError, ValueError, InvalidOperation):
        raise ValueError("Les montants doivent être des nombres positifs en DT.")
    if not amount.is_finite() or amount < 0 or amount > 100_000:
        raise ValueError("Le montant dépasse la limite autorisée.")
    return int((amount * 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _save_product_image(image):
    if not image or not image.filename:
        return None
    safe_name = secure_filename(image.filename)
    content = image.stream.read(12)
    image.stream.seek(0)
    signatures = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF8")
    is_webp = content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    if not safe_name or not (content.startswith(signatures) or is_webp):
        raise ValueError("Image invalide : formats PNG, JPG, GIF ou WEBP acceptés.")
    extension = safe_name.rsplit(".", 1)[-1].lower()
    if extension not in {"png", "jpg", "jpeg", "gif", "webp"}:
        raise ValueError("Extension d'image non autorisée.")
    filename = f"{uuid4().hex}.{extension}"
    image.save(Path(current_app.config["UPLOAD_FOLDER"], filename))
    return filename


def _save_product_images(images):
    selected = [image for image in images if image and image.filename]
    if len(selected) > MAX_PRODUCT_IMAGES:
        raise ValueError(f"Vous pouvez ajouter au maximum {MAX_PRODUCT_IMAGES} photos par produit.")

    for image in selected:
        image.stream.seek(0, 2)
        size = image.stream.tell()
        image.stream.seek(0)
        if size > MAX_IMAGE_SIZE:
            raise ValueError("Chaque photo doit faire 5 Mo maximum.")
        safe_name = secure_filename(image.filename)
        content = image.stream.read(12)
        image.stream.seek(0)
        signatures = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF8")
        is_webp = content.startswith(b"RIFF") and content[8:12] == b"WEBP"
        extension = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
        if not safe_name or extension not in {"png", "jpg", "jpeg", "gif", "webp"}:
            raise ValueError("Image invalide : formats PNG, JPG, GIF ou WEBP acceptés.")
        if not (content.startswith(signatures) or is_webp):
            raise ValueError("Image invalide : formats PNG, JPG, GIF ou WEBP acceptés.")

    return [_save_product_image(image) for image in selected]


@admin_bp.get("/")
@admin_required
def dashboard():
    db = get_db()
    period = request.args.get("period", "monthly")
    if period not in {"daily", "weekly", "monthly"}:
        period = "monthly"
    if period == "daily":
        bucket, since = "date(o.created_at)", "-13 days"
    elif period == "weekly":
        bucket, since = "strftime('%Y-W%W', o.created_at)", "-77 days"
    else:
        bucket, since = "strftime('%Y-%m', o.created_at)", "-11 months"
    sales = db.execute(
        """SELECT COALESCE(SUM(oi.line_total), 0) AS revenue,
                  COALESCE(SUM(
                      (oi.purchase_price + oi.advertising_cost + oi.packaging_cost) * oi.quantity
                  ), 0) AS cost,
                  COALESCE(SUM(oi.quantity), 0) AS units
           FROM orders o JOIN order_items oi ON oi.order_id = o.id
           WHERE o.status = 'Livrée'"""
    ).fetchone()
    return_expenses = db.execute(
        """SELECT COALESCE(SUM(
                   o.return_fee + COALESCE((
                       SELECT SUM((oi.advertising_cost + oi.packaging_cost) * oi.quantity)
                       FROM order_items oi WHERE oi.order_id = o.id
                   ), 0)
               ), 0)
           FROM orders o
           WHERE o.status = 'Colis refusé ou retour'
             AND o.created_at >= date('now', ?)""",
        (since,),
    ).fetchone()[0]
    counts = db.execute(
        "SELECT COUNT(*) AS total, SUM(status IN ('Nouvelle','À confirmer')) AS pending FROM orders"
    ).fetchone()
    stock = db.execute(
        """SELECT COUNT(*) AS products,
                  SUM(stock_quantity = 0) AS out_of_stock,
                  SUM(stock_quantity <= low_stock_threshold AND stock_quantity > 0) AS low_stock
           FROM products WHERE is_active = 1"""
    ).fetchone()
    recent = db.execute(
        """SELECT o.*, o.customer_name AS full_name FROM orders o JOIN customers c ON c.id = o.customer_id
           ORDER BY o.created_at DESC LIMIT 8"""
    ).fetchall()
    popular = db.execute(
        """SELECT oi.product_name, SUM(oi.quantity) AS units
           FROM order_items oi JOIN orders o ON o.id = oi.order_id
           WHERE o.status = 'Livrée' GROUP BY oi.product_name
           ORDER BY units DESC LIMIT 5"""
    ).fetchall()
    statuses = db.execute("SELECT status, COUNT(*) AS count FROM orders GROUP BY status").fetchall()
    sales_trend = db.execute(
        f"""SELECT {bucket} AS period, SUM(oi.line_total) AS revenue
            FROM orders o JOIN order_items oi ON oi.order_id=o.id
            WHERE o.status='Livrée' AND o.created_at >= date('now', ?)
            GROUP BY period ORDER BY period""",
        (since,),
    ).fetchall()
    alerts = db.execute(
        """SELECT name, stock_quantity, low_stock_threshold FROM products
           WHERE is_active = 1 AND stock_quantity <= low_stock_threshold
           ORDER BY stock_quantity LIMIT 5"""
    ).fetchall()
    return render_template(
        "admin/dashboard.html", sales=sales, counts=counts, stock=stock,
        recent=recent, popular=popular, statuses=statuses, alerts=alerts,
        sales_trend=sales_trend, period=period, return_expenses=return_expenses,
    )


@admin_bp.get("/produits")
@admin_required
def products():
    db = get_db()
    query = request.args.get("q", "").strip()[:100]
    sort = request.args.get("sort", "newest")
    sort_sql = {
        "newest": "p.created_at DESC",
        "price": "p.sale_price ASC",
        "stock": "p.stock_quantity ASC",
        "name": "p.name COLLATE NOCASE ASC",
    }.get(sort, "p.created_at DESC")
    page = max(1, min(10000, request.args.get("page", 1, type=int)))
    condition = " WHERE p.name LIKE ? OR p.description LIKE ?" if query else ""
    params = (f"%{query}%", f"%{query}%") if query else ()
    total = db.execute("SELECT COUNT(*) FROM products p" + condition, params).fetchone()[0]
    rows = db.execute(
        """SELECT p.*, c.name AS category_name FROM products p
           LEFT JOIN categories c ON c.id = p.category_id""" + condition +
        f" ORDER BY {sort_sql} LIMIT 50 OFFSET ?",
        (*params, (page - 1) * 50),
    ).fetchall()
    categories = db.execute("SELECT * FROM categories ORDER BY name").fetchall()
    return render_template(
        "admin/products.html", products=rows, categories=categories, query=query,
        sort=sort, page=page, pages=max(1, (total + 49) // 50),
    )


@admin_bp.route("/produits/nouveau", methods=["GET", "POST"])
@admin_required
def new_product():
    categories = get_db().execute("SELECT * FROM categories ORDER BY name").fetchall()
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            if not name or len(name) > 160:
                raise ValueError("Le nom du produit est obligatoire (160 caractères maximum).")
            description = request.form.get("description", "").strip()[:5000]
            purchase_price = _money(request.form.get("purchase_price"))
            advertising_cost = _money(request.form.get("advertising_cost", "0"))
            packaging_cost = _money(request.form.get("packaging_cost", "0"))
            sale_price = _money(request.form.get("sale_price"))
            compare_at_value = request.form.get("compare_at_price", "").strip()
            compare_at_price = _money(compare_at_value) if compare_at_value else None
            if compare_at_price is not None and compare_at_price < sale_price:
                raise ValueError("Le prix barré doit être supérieur ou égal au prix de vente.")
            stock = int(request.form.get("stock_quantity", ""))
            threshold = int(request.form.get("low_stock_threshold", "3"))
            category = request.form.get("category_id", "").strip()
            category_id = int(category) if category else None
            if stock < 0 or threshold < 0 or stock > 1_000_000:
                raise ValueError("Stock ou seuil d'alerte invalide.")
            if category_id is not None and not get_db().execute(
                "SELECT 1 FROM categories WHERE id = ?", (category_id,)
            ).fetchone():
                raise ValueError("Catégorie introuvable.")
            uploaded_images = request.files.getlist("images")
            legacy_image = request.files.get("image")
            if legacy_image and legacy_image.filename:
                uploaded_images.append(legacy_image)
            filenames = _save_product_images(uploaded_images)
            db = get_db()
            db.execute(
                """INSERT INTO products
                   (name, description, image_filename, purchase_price, advertising_cost, packaging_cost,
                    sale_price, compare_at_price, category_id,
                    stock_quantity, low_stock_threshold)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (name, description, filenames[0] if filenames else None, purchase_price,
                 advertising_cost, packaging_cost, sale_price, compare_at_price,
                 category_id, stock, threshold),
            )
            product_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.executemany(
                "INSERT INTO product_images (product_id, image_filename) VALUES (?, ?)",
                ((product_id, filename) for filename in filenames[1:]),
            )
            if stock:
                db.execute(
                    """INSERT INTO stock_movements
                       (product_id, movement_type, quantity, reason)
                       VALUES (?, 'ADJUSTMENT', ?, 'Stock initial')""",
                    (product_id, stock),
                )
            db.commit()
            flash("Produit créé.", "success")
            return redirect(url_for("admin.products"))
        except (ValueError, TypeError, OSError) as error:
            flash(str(error), "error")
    return render_template(
        "admin/product_form.html", product=None, categories=categories,
        extra_images=(), image_limit=MAX_PRODUCT_IMAGES,
    )


@admin_bp.route("/produits/<int:product_id>/modifier", methods=["GET", "POST"])
@admin_required
def edit_product(product_id):
    db = get_db()
    product = db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if product is None:
        abort(404)
    categories = db.execute("SELECT * FROM categories ORDER BY name").fetchall()
    extra_images = db.execute(
        "SELECT image_filename FROM product_images WHERE product_id = ? ORDER BY id",
        (product_id,),
    ).fetchall()
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            purchase_price = _money(request.form.get("purchase_price"))
            advertising_cost = (
                _money(request.form["advertising_cost"])
                if "advertising_cost" in request.form else product["advertising_cost"]
            )
            packaging_cost = (
                _money(request.form["packaging_cost"])
                if "packaging_cost" in request.form else product["packaging_cost"]
            )
            sale_price = _money(request.form.get("sale_price"))
            compare_at_value = request.form.get("compare_at_price", "").strip()
            compare_at_price = _money(compare_at_value) if compare_at_value else None
            if compare_at_price is not None and compare_at_price < sale_price:
                raise ValueError("Le prix barré doit être supérieur ou égal au prix de vente.")
            stock = int(request.form.get("stock_quantity", ""))
            threshold = int(request.form.get("low_stock_threshold", "3"))
            category = request.form.get("category_id", "").strip()
            category_id = int(category) if category else None
            if not name or len(name) > 160 or min(stock, threshold) < 0 or stock > 1_000_000:
                raise ValueError("Vérifiez le nom, le stock et le seuil.")
            if category_id is not None and not db.execute(
                "SELECT 1 FROM categories WHERE id = ?", (category_id,)
            ).fetchone():
                raise ValueError("Catégorie introuvable.")
            current_image_count = int(bool(product["image_filename"])) + len(extra_images)
            uploaded_images = [
                image for image in request.files.getlist("images")
                if image and image.filename
            ]
            legacy_image = request.files.get("image")
            if legacy_image and legacy_image.filename:
                uploaded_images.append(legacy_image)
            if current_image_count + len(uploaded_images) > MAX_PRODUCT_IMAGES:
                raise ValueError(
                    f"Un produit peut avoir au maximum {MAX_PRODUCT_IMAGES} photos. "
                    "Ajoutez moins de photos ou contactez l'administrateur pour les remplacer."
                )
            filenames = _save_product_images(uploaded_images)
            image_filename = product["image_filename"] or (filenames[0] if filenames else None)
            db.execute(
                """UPDATE products SET name=?, description=?, image_filename=?, purchase_price=?,
                   advertising_cost=?, packaging_cost=?, sale_price=?, compare_at_price=?, category_id=?, stock_quantity=?,
                   low_stock_threshold=? WHERE id=?""",
                (name, request.form.get("description", "").strip()[:5000], image_filename,
                 purchase_price, advertising_cost, packaging_cost, sale_price, compare_at_price,
                 category_id, stock, threshold, product_id),
            )
            extra_filenames = filenames[1:] if not product["image_filename"] else filenames
            db.executemany(
                "INSERT INTO product_images (product_id, image_filename) VALUES (?, ?)",
                ((product_id, filename) for filename in extra_filenames),
            )
            difference = stock - product["stock_quantity"]
            if difference:
                movement_type = "IN" if difference > 0 else "OUT"
                db.execute(
                    """INSERT INTO stock_movements
                       (product_id, movement_type, quantity, reason)
                       VALUES (?, ?, ?, 'Ajustement manuel du stock')""",
                    (product_id, movement_type, abs(difference)),
                )
            db.commit()
            flash("Produit modifié.", "success")
            return redirect(url_for("admin.products"))
        except (ValueError, TypeError, OSError) as error:
            flash(str(error), "error")
    return render_template(
        "admin/product_form.html", product=product, categories=categories,
        extra_images=extra_images, image_limit=MAX_PRODUCT_IMAGES,
    )


@admin_bp.get("/categories")
@admin_required
def categories():
    rows = get_db().execute(
        """SELECT c.id, c.name, c.created_at, COUNT(p.id) AS product_count
           FROM categories c LEFT JOIN products p ON p.category_id = c.id
           GROUP BY c.id ORDER BY c.name COLLATE NOCASE"""
    ).fetchall()
    return render_template("admin/categories.html", categories=rows)


@admin_bp.post("/categories")
@admin_required
def add_category():
    name = request.form.get("name", "").strip()
    if not name or len(name) > 100:
        flash("Nom de catégorie obligatoire (100 caractères maximum).", "error")
    else:
        try:
            db = get_db()
            db.execute("INSERT INTO categories (name) VALUES (?)", (name,))
            db.commit()
            flash("Catégorie ajoutée.", "success")
        except sqlite3.IntegrityError:
            flash("Cette catégorie existe déjà.", "error")
    return redirect(url_for("admin.categories"))


@admin_bp.post("/produits/<int:product_id>/archiver")
@admin_required
def archive_product(product_id):
    db = get_db()
    db.execute("UPDATE products SET is_active = 0 WHERE id = ?", (product_id,))
    db.commit()
    flash("Produit archivé : il n'apparaît plus dans la boutique.", "success")
    return redirect(url_for("admin.products"))


@admin_bp.get("/commandes")
@admin_required
def orders():
    status = request.args.get("status", "")
    search = request.args.get("q", "").strip()[:100]
    query = """SELECT o.*, o.customer_name AS full_name, o.customer_phone AS phone,
                      o.delivery_city AS city FROM orders o WHERE 1=1"""
    params = []
    if status in ORDER_STATUSES:
        status_values = {
            "Nouvelle": ("Nouvelle", "À confirmer"),
            "Confirmée": ("Confirmée", "En préparation", "Expédiée"),
            "Livrée": ("Livrée",),
            "Colis refusé ou retour": ("Colis refusé ou retour", "Annulée"),
        }[status]
        query += f" AND o.status IN ({','.join('?' for _ in status_values)})"
        params.extend(status_values)
    if search:
        query += " AND (o.order_number LIKE ? OR o.customer_name LIKE ? OR o.customer_phone LIKE ?)"
        params.extend([f"%{search}%"] * 3)
    query += " ORDER BY o.created_at DESC LIMIT 200"
    rows = get_db().execute(query, params).fetchall()
    status_labels = {
        "À confirmer": "Nouvelle",
        "En préparation": "Confirmée",
        "Expédiée": "Confirmée",
        "Annulée": "Refus ou retour",
        "Colis refusé ou retour": "Refus ou retour",
    }
    return render_template(
        "admin/orders.html", orders=rows, statuses=ORDER_STATUSES,
        status_labels=status_labels, transitions=TRANSITIONS,
        selected_status=status, query=search,
    )


@admin_bp.post("/commandes/<int:order_id>/statut")
@admin_required
def update_order_status(order_id):
    try:
        change_order_status(order_id, request.form.get("status", ""))
        if request.form.get("status") == "Confirmée":
            flash("Commande confirmée. Le stock est réservé ; vous pouvez imprimer la fiche livreur.", "success")
        else:
            flash("Statut mis à jour. Le stock a été traité de façon transactionnelle.", "success")
    except ValueError as error:
        flash(str(error), "error")
    return redirect(url_for("admin.orders"))


@admin_bp.get("/commandes/<int:order_id>/bon-livraison")
@admin_required
def delivery_note(order_id):
    db = get_db()
    order = db.execute(
        """SELECT * FROM orders WHERE id = ?""",
        (order_id,),
    ).fetchone()
    if order is None:
        abort(404)
    if not order["stock_deducted"]:
        abort(409, description="La fiche livreur est disponible après confirmation de la commande.")
    lines = db.execute(
        "SELECT product_name, quantity, unit_price, line_total FROM order_items WHERE order_id = ? ORDER BY id",
        (order_id,),
    ).fetchall()
    return render_template("admin/delivery_note.html", order=order, lines=lines)


@admin_bp.get("/clients")
@admin_required
def customers():
    query = request.args.get("q", "").strip()[:100]
    sql = """SELECT c.*, COUNT(o.id) AS order_count,
                    COALESCE(SUM(CASE WHEN o.status='Livrée' THEN o.total_amount ELSE 0 END),0) AS delivered_total
             FROM customers c LEFT JOIN orders o ON o.customer_id=c.id"""
    params = []
    if query:
        sql += " WHERE c.full_name LIKE ? OR c.phone LIKE ?"
        params.extend((f"%{query}%", f"%{query}%"))
    sql += " GROUP BY c.id ORDER BY c.created_at DESC LIMIT 200"
    rows = get_db().execute(sql, params).fetchall()
    history = {
        customer["id"]: get_db().execute(
            """SELECT order_number, created_at, status, total_amount FROM orders
               WHERE customer_id=? ORDER BY created_at DESC""",
            (customer["id"],),
        ).fetchall()
        for customer in rows
    }
    return render_template("admin/customers.html", customers=rows, query=query, history=history)


@admin_bp.get("/stocks")
@admin_required
def stocks():
    db = get_db()
    products = db.execute(
        """SELECT id, name, stock_quantity, low_stock_threshold, is_active
           FROM products ORDER BY stock_quantity ASC, name COLLATE NOCASE"""
    ).fetchall()
    movements = db.execute(
        """SELECT sm.*, p.name AS product_name, o.order_number FROM stock_movements sm
           JOIN products p ON p.id=sm.product_id LEFT JOIN orders o ON o.id=sm.order_id
           ORDER BY sm.created_at DESC LIMIT 100"""
    ).fetchall()
    return render_template("admin/stocks.html", products=products, movements=movements)


@admin_bp.route("/parametres", methods=["GET", "POST"])
@admin_required
def settings():
    db = get_db()
    if request.method == "POST":
        try:
            fee = _money(request.form.get("delivery_fee"))
            return_fee_value = request.form.get("return_fee")
            return_fee = (
                _money(return_fee_value) if return_fee_value is not None else None
            )
            settings = [("delivery_fee", str(fee))]
            if return_fee is not None:
                settings.append(("return_fee", str(return_fee)))
            db.executemany(
                """INSERT INTO settings (key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                settings,
            )
            db.commit()
            flash("Paramètres de la boutique mis à jour.", "success")
        except ValueError as error:
            flash(str(error), "error")
        return redirect(url_for("admin.settings"))
    row = db.execute("SELECT value FROM settings WHERE key='delivery_fee'").fetchone()
    delivery_fee = int(row["value"]) if row else 8000
    return_row = db.execute("SELECT value FROM settings WHERE key='return_fee'").fetchone()
    return_fee = int(return_row["value"]) if return_row else 0
    return render_template(
        "admin/settings.html", delivery_fee=delivery_fee, return_fee=return_fee,
    )


@admin_bp.get("/statistiques")
@admin_required
def statistics():
    start = request.args.get("from", "")
    end = request.args.get("to", "")
    for value in (start, end):
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                abort(400, description="Date invalide.")
    where = " WHERE o.status = 'Livrée'"
    params = []
    if start:
        where += " AND date(o.created_at) >= date(?)"
        params.append(start)
    if end:
        where += " AND date(o.created_at) <= date(?)"
        params.append(end)
    db = get_db()
    summary = db.execute(
        """SELECT COUNT(DISTINCT o.id) AS delivered_orders,
                  COALESCE(SUM(oi.line_total),0) AS revenue,
                  COALESCE(SUM(oi.purchase_price * oi.quantity),0) AS purchase_cost,
                  COALESCE(SUM(oi.advertising_cost * oi.quantity),0) AS advertising_cost,
                  COALESCE(SUM(oi.packaging_cost * oi.quantity),0) AS packaging_cost,
                  COALESCE(SUM(
                      oi.line_total - (
                          oi.purchase_price + oi.advertising_cost + oi.packaging_cost
                      ) * oi.quantity
                  ),0) AS gross_margin,
                  COALESCE(SUM(oi.quantity),0) AS units
           FROM orders o JOIN order_items oi ON oi.order_id=o.id""" + where,
        params,
    ).fetchone()
    return_where = ["o.status = 'Colis refusé ou retour'"]
    return_params = []
    if start:
        return_where.append("date(o.created_at) >= date(?)")
        return_params.append(start)
    if end:
        return_where.append("date(o.created_at) <= date(?)")
        return_params.append(end)
    return_expenses = db.execute(
        """SELECT COALESCE(SUM(
                   o.return_fee + COALESCE((
                       SELECT SUM((oi.advertising_cost + oi.packaging_cost) * oi.quantity)
                       FROM order_items oi WHERE oi.order_id = o.id
                   ), 0)
               ), 0)
           FROM orders o WHERE """ + " AND ".join(return_where),
        return_params,
    ).fetchone()[0]
    all_where = []
    count_params = []
    if start:
        all_where.append("date(created_at) >= date(?)")
        count_params.append(start)
    if end:
        all_where.append("date(created_at) <= date(?)")
        count_params.append(end)
    count_filter = " WHERE " + " AND ".join(all_where) if all_where else ""
    all_count = db.execute("SELECT COUNT(*) FROM orders" + count_filter, count_params).fetchone()[0]
    cancelled_count = db.execute(
        "SELECT COUNT(*) FROM orders" + (count_filter + " AND " if count_filter else " WHERE ")
        + "status IN ('Annulée','Colis refusé ou retour')",
        count_params,
    ).fetchone()[0]
    statuses = db.execute("SELECT status, COUNT(*) AS count FROM orders GROUP BY status").fetchall()
    return render_template(
        "admin/statistics.html", summary=summary, statuses=statuses, start=start, end=end,
        cancellation_rate=round(cancelled_count * 100 / all_count, 1) if all_count else 0,
        return_expenses=return_expenses,
    )


@admin_bp.get("/statistiques/export.csv")
@admin_required
def export_statistics():
    rows = get_db().execute(
        """SELECT o.order_number, o.created_at, o.status, c.full_name, c.city,
                  oi.product_name, oi.quantity, oi.unit_price, oi.line_total,
                  oi.purchase_price, oi.advertising_cost, oi.packaging_cost,
                  CASE WHEN o.status='Colis refusé ou retour' THEN o.return_fee ELSE 0 END AS return_fee
           FROM orders o JOIN customers c ON c.id=o.customer_id
           JOIN order_items oi ON oi.order_id=o.id
           ORDER BY o.created_at DESC"""
    ).fetchall()
    output = StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "Commande", "Date", "Statut", "Client", "Ville", "Produit", "Quantité",
        "Prix DT", "Ligne DT", "Achat unitaire DT", "Publicité unitaire DT",
        "Emballage unitaire DT", "Frais de retour commande DT",
    ])
    writer.writerows([
        (
            *tuple(row)[:7], f"{row['unit_price']/1000:.3f}", f"{row['line_total']/1000:.3f}",
            f"{row['purchase_price']/1000:.3f}", f"{row['advertising_cost']/1000:.3f}",
            f"{row['packaging_cost']/1000:.3f}", f"{row['return_fee']/1000:.3f}",
        )
        for row in rows
    ])
    return Response(
        "\ufeff" + output.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=celina-store-statistiques.csv"},
    )
