PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    phone TEXT,
    city TEXT,
    segment TEXT DEFAULT 'standard',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    price REAL NOT NULL,
    order_date TEXT NOT NULL,
    status TEXT DEFAULT 'paid',
    channel TEXT DEFAULT 'web',
    payment_status TEXT DEFAULT 'paid',
    FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    list_price REAL NOT NULL,
    stock_qty INTEGER NOT NULL DEFAULT 0,
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS orders_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL,
    discount_amount REAL DEFAULT 0,
    FOREIGN KEY (order_id) REFERENCES orders (id),
    FOREIGN KEY (product_id) REFERENCES products (id)
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL,
    amount REAL NOT NULL,
    method TEXT NOT NULL,
    status TEXT DEFAULT 'paid',
    transaction_id TEXT UNIQUE,
    paid_at TEXT,
    FOREIGN KEY (order_id) REFERENCES orders (id)
);

CREATE TABLE IF NOT EXISTS shipments (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL,
    carrier TEXT NOT NULL,
    tracking_no TEXT UNIQUE,
    shipping_fee REAL DEFAULT 0,
    shipped_at TEXT,
    delivered_at TEXT,
    status TEXT DEFAULT 'pending',
    FOREIGN KEY (order_id) REFERENCES orders (id)
);

CREATE TABLE IF NOT EXISTS support_tickets (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    order_id INTEGER,
    priority TEXT DEFAULT 'normal',
    issue_type TEXT NOT NULL,
    status TEXT DEFAULT 'open',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    resolved_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users (id),
    FOREIGN KEY (order_id) REFERENCES orders (id)
);

CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_order_date ON orders(order_date);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_items_order_id ON orders_items(order_id);
CREATE INDEX IF NOT EXISTS idx_orders_items_product_id ON orders_items(product_id);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
CREATE INDEX IF NOT EXISTS idx_payments_order_id ON payments(order_id);
CREATE INDEX IF NOT EXISTS idx_shipments_order_id ON shipments(order_id);
CREATE INDEX IF NOT EXISTS idx_support_tickets_user_id ON support_tickets(user_id);
CREATE INDEX IF NOT EXISTS idx_support_tickets_status ON support_tickets(status);

INSERT OR IGNORE INTO users (id, name, email, phone, city, segment) VALUES
    (1, 'Alice', 'alice@example.com', '13800010001', 'Shanghai', 'vip'),
    (2, 'Bob', 'bob@example.com', '13800010002', 'Hangzhou', 'standard'),
    (3, 'Carol', 'carol@example.com', '13800010003', 'Shenzhen', 'vip'),
    (4, 'David', 'david@example.com', '13800010004', 'Beijing', 'new'),
    (5, 'Eva', 'eva@example.com', '13800010005', 'Guangzhou', 'standard'),
    (6, 'Frank', 'frank@example.com', '13800010006', 'Nanjing', 'churn_risk');

INSERT OR IGNORE INTO products (id, sku, name, category, list_price, stock_qty, status) VALUES
    (1, 'SKU-IPH-001', 'iPhone Case', 'accessories', 89.00, 120, 'active'),
    (2, 'SKU-KBD-002', 'Mechanical Keyboard', 'electronics', 399.00, 42, 'active'),
    (3, 'SKU-MSE-003', 'Wireless Mouse', 'electronics', 199.00, 65, 'active'),
    (4, 'SKU-DSK-004', 'Standing Desk', 'furniture', 1599.00, 18, 'active'),
    (5, 'SKU-CHR-005', 'Ergonomic Chair', 'furniture', 1299.00, 24, 'active'),
    (6, 'SKU-HDP-006', 'USB-C Hub', 'electronics', 249.00, 55, 'active'),
    (7, 'SKU-CBL-007', 'Type-C Cable', 'accessories', 39.00, 320, 'active'),
    (8, 'SKU-LMP-008', 'Desk Lamp', 'furniture', 179.00, 80, 'inactive');

INSERT OR IGNORE INTO orders (id, user_id, price, order_date, status, channel, payment_status) VALUES
    (1, 1, 120.50, '2026-03-01', 'paid', 'web', 'paid'),
    (2, 1, 89.99, '2026-03-05', 'paid', 'app', 'paid'),
    (3, 2, 45.00, '2026-03-09', 'pending', 'web', 'unpaid'),
    (4, 3, 300.00, '2026-03-11', 'paid', 'app', 'paid'),
    (5, 4, 498.00, '2026-03-12', 'shipped', 'web', 'paid'),
    (6, 5, 1599.00, '2026-03-13', 'shipped', 'partner', 'paid'),
    (7, 2, 249.00, '2026-03-14', 'cancelled', 'app', 'refunded'),
    (8, 6, 177.00, '2026-03-14', 'paid', 'web', 'paid'),
    (9, 3, 1299.00, '2026-03-15', 'pending', 'app', 'authorized'),
    (10, 1, 438.00, '2026-03-15', 'paid', 'web', 'paid');

INSERT OR IGNORE INTO orders_items (id, order_id, product_id, quantity, unit_price, discount_amount) VALUES
    (1, 1, 1, 1, 89.00, 0),
    (2, 1, 7, 1, 39.00, 7.50),
    (3, 2, 3, 1, 199.00, 109.01),
    (4, 3, 7, 2, 39.00, 33.00),
    (5, 4, 2, 1, 399.00, 99.00),
    (6, 5, 2, 1, 399.00, 0),
    (7, 5, 6, 1, 249.00, 150.00),
    (8, 6, 4, 1, 1599.00, 0),
    (9, 7, 6, 1, 249.00, 0),
    (10, 8, 1, 1, 89.00, 0),
    (11, 8, 7, 3, 39.00, 29.00),
    (12, 9, 5, 1, 1299.00, 0),
    (13, 10, 2, 1, 399.00, 0),
    (14, 10, 3, 1, 199.00, 160.00);

INSERT OR IGNORE INTO payments (id, order_id, amount, method, status, transaction_id, paid_at) VALUES
    (1, 1, 120.50, 'alipay', 'paid', 'TXN-20260301-0001', '2026-03-01 10:12:00'),
    (2, 2, 89.99, 'wechat_pay', 'paid', 'TXN-20260305-0002', '2026-03-05 09:35:00'),
    (3, 3, 45.00, 'alipay', 'pending', 'TXN-20260309-0003', NULL),
    (4, 4, 300.00, 'credit_card', 'paid', 'TXN-20260311-0004', '2026-03-11 14:20:00'),
    (5, 5, 498.00, 'credit_card', 'paid', 'TXN-20260312-0005', '2026-03-12 16:05:00'),
    (6, 6, 1599.00, 'bank_transfer', 'paid', 'TXN-20260313-0006', '2026-03-13 11:42:00'),
    (7, 7, 249.00, 'wechat_pay', 'refunded', 'TXN-20260314-0007', '2026-03-14 08:51:00'),
    (8, 8, 177.00, 'alipay', 'paid', 'TXN-20260314-0008', '2026-03-14 12:10:00'),
    (9, 9, 1299.00, 'credit_card', 'authorized', 'TXN-20260315-0009', '2026-03-15 10:03:00'),
    (10, 10, 438.00, 'alipay', 'paid', 'TXN-20260315-0010', '2026-03-15 20:47:00');

INSERT OR IGNORE INTO shipments (id, order_id, carrier, tracking_no, shipping_fee, shipped_at, delivered_at, status) VALUES
    (1, 1, 'SF Express', 'SF100000001', 12.00, '2026-03-01 18:00:00', '2026-03-03 11:30:00', 'delivered'),
    (2, 2, 'YTO', 'YT100000002', 10.00, '2026-03-05 18:30:00', '2026-03-07 16:20:00', 'delivered'),
    (3, 4, 'JD Logistics', 'JD100000004', 15.00, '2026-03-11 20:12:00', '2026-03-13 15:00:00', 'delivered'),
    (4, 5, 'SF Express', 'SF100000005', 12.00, '2026-03-12 21:00:00', NULL, 'in_transit'),
    (5, 6, 'ZTO', 'ZT100000006', 20.00, '2026-03-13 17:25:00', NULL, 'in_transit'),
    (6, 8, 'YTO', 'YT100000008', 10.00, '2026-03-14 18:10:00', NULL, 'shipped'),
    (7, 10, 'SF Express', 'SF100000010', 12.00, '2026-03-15 22:10:00', NULL, 'shipped');

INSERT OR IGNORE INTO support_tickets (id, user_id, order_id, priority, issue_type, status, created_at, resolved_at) VALUES
    (1, 2, 3, 'high', 'payment_timeout', 'open', '2026-03-09 10:22:00', NULL),
    (2, 1, 2, 'normal', 'invoice_request', 'resolved', '2026-03-06 09:05:00', '2026-03-06 15:40:00'),
    (3, 4, 5, 'high', 'delivery_delay', 'open', '2026-03-14 13:18:00', NULL),
    (4, 6, NULL, 'normal', 'account_issue', 'closed', '2026-03-10 18:42:00', '2026-03-11 12:00:00'),
    (5, 3, 9, 'urgent', 'address_change', 'open', '2026-03-15 11:29:00', NULL);
