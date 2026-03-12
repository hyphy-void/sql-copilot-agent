PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    price REAL NOT NULL,
    order_date TEXT NOT NULL,
    status TEXT DEFAULT 'paid',
    FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_order_date ON orders(order_date);

INSERT OR IGNORE INTO users (id, name, email) VALUES
    (1, 'Alice', 'alice@example.com'),
    (2, 'Bob', 'bob@example.com'),
    (3, 'Carol', 'carol@example.com');

INSERT OR IGNORE INTO orders (id, user_id, price, order_date, status) VALUES
    (1, 1, 120.50, '2026-03-01', 'paid'),
    (2, 1, 89.99, '2026-03-05', 'paid'),
    (3, 2, 45.00, '2026-03-09', 'pending'),
    (4, 3, 300.00, '2026-03-11', 'paid');
