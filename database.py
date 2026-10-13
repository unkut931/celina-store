import sqlite3

from flask import current_app, g


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    image_filename TEXT,
    purchase_price INTEGER NOT NULL CHECK (purchase_price >= 0),
    advertising_cost INTEGER NOT NULL DEFAULT 0 CHECK (advertising_cost >= 0),
    packaging_cost INTEGER NOT NULL DEFAULT 0 CHECK (packaging_cost >= 0),
    sale_price INTEGER NOT NULL CHECK (sale_price >= 0),
    compare_at_price INTEGER CHECK (compare_at_price IS NULL OR compare_at_price >= sale_price),
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    stock_quantity INTEGER NOT NULL DEFAULT 0 CHECK (stock_quantity >= 0),
    low_stock_threshold INTEGER NOT NULL DEFAULT 3 CHECK (low_stock_threshold >= 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    is_demo INTEGER NOT NULL DEFAULT 0 CHECK (is_demo IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS product_images (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    image_filename TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_product_images_product_id ON product_images(product_id);
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL,
    phone TEXT NOT NULL,
    address TEXT NOT NULL,
    city TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);
CREATE UNIQUE INDEX IF NOT EXISTS uq_customers_phone ON customers(phone);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    order_number TEXT NOT NULL UNIQUE,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    customer_name TEXT NOT NULL,
    customer_phone TEXT NOT NULL,
    delivery_address TEXT NOT NULL,
    delivery_city TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN
      ('Nouvelle','À confirmer','Confirmée','En préparation','Expédiée','Livrée','Annulée','Colis refusé ou retour')),
    delivery_fee INTEGER NOT NULL DEFAULT 0 CHECK (delivery_fee >= 0),
    total_amount INTEGER NOT NULL CHECK (total_amount >= 0),
    return_fee INTEGER NOT NULL DEFAULT 0 CHECK (return_fee >= 0),
    stock_deducted INTEGER NOT NULL DEFAULT 0 CHECK (stock_deducted IN (0, 1)),
    stock_restored INTEGER NOT NULL DEFAULT 0 CHECK (stock_restored IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at);
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    product_name TEXT NOT NULL,
    unit_price INTEGER NOT NULL CHECK (unit_price >= 0),
    purchase_price INTEGER NOT NULL CHECK (purchase_price >= 0),
    advertising_cost INTEGER NOT NULL DEFAULT 0 CHECK (advertising_cost >= 0),
    packaging_cost INTEGER NOT NULL DEFAULT 0 CHECK (packaging_cost >= 0),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    line_total INTEGER NOT NULL CHECK (line_total >= 0)
);
CREATE TABLE IF NOT EXISTS stock_movements (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id),
    order_id INTEGER REFERENCES orders(id),
    movement_type TEXT NOT NULL CHECK (movement_type IN ('OUT','IN','ADJUSTMENT')),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admin_users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"], timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA busy_timeout = 10000")
    return g.db


def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(SCHEMA)

    migrations = {
        "products": {
            "advertising_cost": "INTEGER NOT NULL DEFAULT 0 CHECK (advertising_cost >= 0)",
            "packaging_cost": "INTEGER NOT NULL DEFAULT 0 CHECK (packaging_cost >= 0)",
        },
        "order_items": {
            "advertising_cost": "INTEGER NOT NULL DEFAULT 0 CHECK (advertising_cost >= 0)",
            "packaging_cost": "INTEGER NOT NULL DEFAULT 0 CHECK (packaging_cost >= 0)",
        },
        "orders": {
            "return_fee": "INTEGER NOT NULL DEFAULT 0 CHECK (return_fee >= 0)",
        },
    }
    for table, columns in migrations.items():
        existing = {
            row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for column, definition in columns.items():
            if column not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    db.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('delivery_fee', '8000')"
    )
    db.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('return_fee', '0')"
    )
    db.execute("DELETE FROM settings WHERE key='notification_emails'")
    db.commit()
