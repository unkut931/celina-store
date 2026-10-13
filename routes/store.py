from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

from database import get_db
from image_storage import public_product_image_url
from orders import place_order

store_bp = Blueprint("store", __name__)


@store_bp.get("/uploads/<path:filename>")
def uploaded_image(filename):
    public_url = public_product_image_url(filename)
    if public_url:
        return redirect(public_url)
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], filename)


def active_products():
    return get_db().execute(
        """SELECT p.*, c.name AS category_name
           FROM products p LEFT JOIN categories c ON c.id = p.category_id
           WHERE p.is_active = 1 ORDER BY p.created_at DESC"""
    ).fetchall()


@store_bp.get("/")
def home():
    query = request.args.get("q", "").strip()[:100]
    category_id = request.args.get("category", "").strip()
    sql = """SELECT p.*, c.name AS category_name
             FROM products p LEFT JOIN categories c ON c.id = p.category_id
             WHERE p.is_active = 1"""
    params = []
    if query:
        sql += " AND (p.name LIKE ? OR p.description LIKE ?)"
        params.extend((f"%{query}%", f"%{query}%"))
    if category_id.isdigit():
        sql += " AND p.category_id = ?"
        params.append(int(category_id))
    sql += " ORDER BY p.created_at DESC"
    products = get_db().execute(sql, params).fetchall()
    popular = get_db().execute(
        """SELECT p.* FROM products p WHERE p.is_active=1 AND p.stock_quantity>0
           AND p.id IN (SELECT oi.product_id FROM order_items oi
                        JOIN orders o ON o.id=oi.order_id WHERE o.status='Livrée'
                        GROUP BY oi.product_id ORDER BY SUM(oi.quantity) DESC LIMIT 3)
           ORDER BY p.sale_price DESC"""
    ).fetchall()
    offers = get_db().execute(
        """SELECT p.* FROM products p WHERE p.is_active=1 AND p.stock_quantity>0
           AND p.compare_at_price > p.sale_price ORDER BY p.created_at DESC LIMIT 3"""
    ).fetchall()
    categories = get_db().execute("SELECT * FROM categories ORDER BY name").fetchall()
    return render_template(
        "store/home.html", products=products, categories=categories, query=query,
        selected_category=category_id, popular=popular, offers=offers,
    )


@store_bp.get("/produit/<int:product_id>")
def product(product_id):
    product_row = get_db().execute(
        """SELECT p.*, c.name AS category_name FROM products p
           LEFT JOIN categories c ON c.id = p.category_id
           WHERE p.id = ? AND p.is_active = 1""",
        (product_id,),
    ).fetchone()
    if product_row is None:
        abort(404)
    extra_images = get_db().execute(
        "SELECT image_filename FROM product_images WHERE product_id = ? ORDER BY id",
        (product_id,),
    ).fetchall()
    image_filenames = (
        ([product_row["image_filename"]] if product_row["image_filename"] else [])
        + [image["image_filename"] for image in extra_images]
    )
    return render_template(
        "store/product.html", product=product_row, image_filenames=image_filenames,
    )


@store_bp.post("/panier/ajouter/<int:product_id>")
def add_to_cart(product_id):
    product_row = get_db().execute(
        "SELECT id, stock_quantity FROM products WHERE id = ? AND is_active = 1",
        (product_id,),
    ).fetchone()
    if product_row is None:
        abort(404)
    try:
        quantity = int(request.form.get("quantity", "1"))
    except ValueError:
        quantity = 0
    cart = session.get("cart", {})
    current_quantity = int(cart.get(str(product_id), 0))
    if quantity < 1 or current_quantity + quantity > min(product_row["stock_quantity"], 20):
        flash("Quantité invalide ou stock insuffisant.", "error")
        return redirect(url_for("store.product", product_id=product_id))
    cart[str(product_id)] = current_quantity + quantity
    session["cart"] = cart
    flash("Produit ajouté au panier.", "success")
    return redirect(url_for("store.checkout"))


@store_bp.get("/commande")
def checkout():
    cart = session.get("cart", {})
    products = []
    subtotal = 0
    for product_id, quantity in cart.items():
        if not str(product_id).isdigit():
            continue
        product_row = get_db().execute(
            "SELECT * FROM products WHERE id = ? AND is_active = 1", (int(product_id),)
        ).fetchone()
        if product_row is not None:
            line_total = product_row["sale_price"] * int(quantity)
            subtotal += line_total
            products.append({"product": product_row, "quantity": int(quantity), "line_total": line_total})
    fee_row = get_db().execute("SELECT value FROM settings WHERE key = 'delivery_fee'").fetchone()
    delivery_fee = int(fee_row["value"]) if fee_row else 8000
    return render_template(
        "store/checkout.html", products=products, subtotal=subtotal,
        delivery_fee=delivery_fee, total=subtotal + delivery_fee,
    )


@store_bp.post("/commande")
def submit_order():
    full_name = request.form.get("full_name", "").strip()
    phone = request.form.get("phone", "").strip()
    address = request.form.get("address", "").strip()
    city = request.form.get("city", "").strip()
    cart = session.get("cart", {})
    if not all((full_name, phone, address, city)) or len(full_name) > 120 or len(address) > 300 or len(city) > 100:
        flash("Veuillez vérifier vos coordonnées.", "error")
        return redirect(url_for("store.checkout"))
    if not phone.replace("+", "").replace(" ", "").replace("-", "").isdigit() or len(phone) < 8 or len(phone) > 20:
        flash("Numéro de téléphone invalide.", "error")
        return redirect(url_for("store.checkout"))
    if not cart:
        flash("Votre panier est vide.", "error")
        return redirect(url_for("store.home"))
    fee_row = get_db().execute("SELECT value FROM settings WHERE key = 'delivery_fee'").fetchone()
    delivery_fee = int(fee_row["value"]) if fee_row else 8000
    try:
        order_number = place_order(
            (full_name, phone, address, city),
            {int(product_id): int(quantity) for product_id, quantity in cart.items()},
            delivery_fee,
        )
    except (ValueError, TypeError) as error:
        flash(str(error), "error")
        return redirect(url_for("store.checkout"))
    session.pop("cart", None)
    return render_template("store/confirmation.html", order_number=order_number)
