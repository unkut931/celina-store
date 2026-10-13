import secrets
from database import DATABASE_ERRORS, get_db, is_postgres

ORDER_ERRORS = (ValueError,) + DATABASE_ERRORS

ORDER_STATUSES = (
    "Nouvelle",
    "Confirmée",
    "Livrée",
    "Colis refusé ou retour",
)
TRANSITIONS = {
    "Nouvelle": ("Confirmée", "Colis refusé ou retour"),
    "Confirmée": ("Livrée", "Colis refusé ou retour"),
    "Livrée": ("Colis refusé ou retour",),
    "À confirmer": ("Confirmée", "Colis refusé ou retour"),
    "En préparation": ("Livrée", "Colis refusé ou retour"),
    "Expédiée": ("Livrée", "Colis refusé ou retour"),
    "Annulée": (),
    "Colis refusé ou retour": (),
}


def place_order(customer, items, delivery_fee):
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        product_rows = {}
        row_lock = " FOR UPDATE" if is_postgres() else ""
        for product_id, quantity in sorted(items.items()):
            product = db.execute(
                "SELECT * FROM products WHERE id = ? AND is_active = 1" + row_lock,
                (product_id,),
            ).fetchone()
            if product is None:
                raise ValueError("Un produit sélectionné n'est plus disponible.")
            if quantity < 1 or quantity > 20:
                raise ValueError("La quantité doit être comprise entre 1 et 20.")
            if product["stock_quantity"] < quantity:
                raise ValueError(f"Stock insuffisant pour « {product['name']} ».")
            product_rows[product_id] = (product, quantity)

        customer_id = db.execute(
            """INSERT INTO customers (full_name, phone, address, city)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(phone) DO UPDATE SET
                 full_name=excluded.full_name,
                 address=excluded.address,
                 city=excluded.city
               RETURNING id""",
            customer,
        ).fetchone()["id"]
        subtotal = sum(p["sale_price"] * qty for p, qty in product_rows.values())
        order_number = f"CS-{secrets.token_hex(4).upper()}"
        order_id = db.execute(
            """INSERT INTO orders
               (order_number, customer_id, customer_name, customer_phone, delivery_address,
                delivery_city, status, delivery_fee, total_amount)
               VALUES (?, ?, ?, ?, ?, ?, 'Nouvelle', ?, ?) RETURNING id""",
            (order_number, customer_id, *customer, delivery_fee, subtotal + delivery_fee),
        ).fetchone()["id"]
        for product, quantity in product_rows.values():
            db.execute(
                """INSERT INTO order_items
                   (order_id, product_id, product_name, unit_price, purchase_price,
                    advertising_cost, packaging_cost, quantity, line_total)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order_id, product["id"], product["name"], product["sale_price"],
                    product["purchase_price"], product["advertising_cost"],
                    product["packaging_cost"], quantity, product["sale_price"] * quantity,
                ),
            )
        db.commit()
        return order_number
    except ORDER_ERRORS:
        db.rollback()
        raise


def change_order_status(order_id, new_status):
    if new_status not in ORDER_STATUSES:
        raise ValueError("Statut de commande invalide.")
    db = get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        row_lock = " FOR UPDATE" if is_postgres() else ""
        order = db.execute(
            "SELECT * FROM orders WHERE id = ?" + row_lock, (order_id,)
        ).fetchone()
        if order is None:
            raise ValueError("Commande introuvable.")
        if new_status == order["status"]:
            db.commit()
            return
        if new_status not in TRANSITIONS.get(order["status"], ()):
            raise ValueError("Cette transition de statut n'est pas autorisée.")
        lines = db.execute(
            "SELECT * FROM order_items WHERE order_id = ? ORDER BY product_id",
            (order_id,),
        ).fetchall()
        if new_status == "Confirmée" and not order["stock_deducted"]:
            for line in lines:
                product = db.execute(
                    "SELECT stock_quantity, name FROM products WHERE id = ?" + row_lock,
                    (line["product_id"],),
                ).fetchone()
                if product is None or product["stock_quantity"] < line["quantity"]:
                    raise ValueError(f"Stock insuffisant pour « {line['product_name']} ».")
            for line in lines:
                db.execute(
                    "UPDATE products SET stock_quantity = stock_quantity - ? WHERE id = ?",
                    (line["quantity"], line["product_id"]),
                )
                db.execute(
                    """INSERT INTO stock_movements
                       (product_id, order_id, movement_type, quantity, reason)
                       VALUES (?, ?, 'OUT', ?, 'Confirmation de commande')""",
                    (line["product_id"], order_id, line["quantity"]),
                )
            db.execute("UPDATE orders SET stock_deducted = 1 WHERE id = ?", (order_id,))
        if new_status == "Colis refusé ou retour" and order["stock_deducted"] and not order["stock_restored"]:
            for line in lines:
                if line["product_id"] is not None:
                    db.execute(
                        "UPDATE products SET stock_quantity = stock_quantity + ? WHERE id = ?",
                        (line["quantity"], line["product_id"]),
                    )
                    db.execute(
                        """INSERT INTO stock_movements
                           (product_id, order_id, movement_type, quantity, reason)
                           VALUES (?, ?, 'IN', ?, ?)""",
                        (line["product_id"], order_id, line["quantity"], f"Retour: {new_status}"),
                    )
            db.execute("UPDATE orders SET stock_restored = 1 WHERE id = ?", (order_id,))
        if new_status == "Colis refusé ou retour":
            setting = db.execute(
                "SELECT value FROM settings WHERE key='return_fee'"
            ).fetchone()
            return_fee = int(setting["value"]) if setting else 0
            db.execute(
                "UPDATE orders SET status = ?, return_fee = ? WHERE id = ?",
                (new_status, return_fee, order_id),
            )
        else:
            db.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
        db.commit()
    except ORDER_ERRORS:
        db.rollback()
        raise
