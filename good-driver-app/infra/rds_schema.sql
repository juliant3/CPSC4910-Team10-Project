-- Good Driver Incentive Program
-- MySQL schema for AWS RDS
-- This file is the versioned source of truth for the DB structure.
-- In practice, prefer generating/updating this via Flask-Migrate
-- (Alembic) migrations in backend/migrations/, and treat this file as
-- a periodically-exported snapshot for reference/grading.

CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(80) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role ENUM('driver','sponsor','admin') NOT NULL,
    first_name VARCHAR(80) NOT NULL,
    last_name VARCHAR(80) NOT NULL,
    phone VARCHAR(20),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    alert_points_changed BOOLEAN DEFAULT TRUE,
    alert_order_placed BOOLEAN DEFAULT TRUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE sponsors (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_name VARCHAR(150) UNIQUE NOT NULL,
    point_value_usd DECIMAL(6,4) NOT NULL DEFAULT 0.01,
    catalog_api_source VARCHAR(100)
);

CREATE TABLE sponsor_users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT UNIQUE NOT NULL,
    sponsor_id INT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (sponsor_id) REFERENCES sponsors(id)
);

CREATE TABLE drivers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT UNIQUE NOT NULL,
    sponsor_id INT,
    points_balance INT NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (sponsor_id) REFERENCES sponsors(id)
);

CREATE TABLE admins (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT UNIQUE NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE driver_applications (
    id INT AUTO_INCREMENT PRIMARY KEY,
    driver_id INT NOT NULL,
    sponsor_id INT NOT NULL,
    status ENUM('pending','accepted','rejected') NOT NULL DEFAULT 'pending',
    reason VARCHAR(255),
    reviewed_by_user_id INT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    reviewed_at DATETIME,
    FOREIGN KEY (driver_id) REFERENCES drivers(id),
    FOREIGN KEY (sponsor_id) REFERENCES sponsors(id),
    FOREIGN KEY (reviewed_by_user_id) REFERENCES users(id)
);

CREATE TABLE products (
    id INT AUTO_INCREMENT PRIMARY KEY,
    sponsor_id INT NOT NULL,
    external_product_id VARCHAR(120) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    image_url VARCHAR(500),
    price_points INT NOT NULL,
    is_available BOOLEAN DEFAULT TRUE,
    last_synced_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (sponsor_id) REFERENCES sponsors(id),
    UNIQUE KEY uq_sponsor_product (sponsor_id, external_product_id)
);

CREATE TABLE purchases (
    id INT AUTO_INCREMENT PRIMARY KEY,
    driver_id INT NOT NULL,
    product_id INT NOT NULL,
    quantity INT NOT NULL DEFAULT 1,
    points_spent INT NOT NULL,
    status ENUM('pending','processing','shipped','cancelled') NOT NULL DEFAULT 'pending',
    placed_by_user_id INT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (driver_id) REFERENCES drivers(id),
    FOREIGN KEY (product_id) REFERENCES products(id),
    FOREIGN KEY (placed_by_user_id) REFERENCES users(id)
);

CREATE TABLE point_transactions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    driver_id INT NOT NULL,
    changed_by_user_id INT NOT NULL,
    points INT NOT NULL,
    reason VARCHAR(255) NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (driver_id) REFERENCES drivers(id),
    FOREIGN KEY (changed_by_user_id) REFERENCES users(id)
);

CREATE TABLE audit_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    category ENUM('driver_application','point_change','password_change','login_attempt') NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    sponsor_id INT,
    driver_id INT,
    user_id INT,
    username_attempted VARCHAR(80),
    success BOOLEAN,
    status VARCHAR(20),
    reason VARCHAR(255),
    points_changed INT,
    change_type VARCHAR(50),
    FOREIGN KEY (sponsor_id) REFERENCES sponsors(id),
    FOREIGN KEY (driver_id) REFERENCES drivers(id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    INDEX idx_category (category),
    INDEX idx_created_at (created_at)
);

CREATE TABLE about_info (
    id INT AUTO_INCREMENT PRIMARY KEY,
    team_number VARCHAR(20) NOT NULL,
    version_number VARCHAR(20) NOT NULL,
    release_date DATE NOT NULL,
    product_name VARCHAR(150) NOT NULL,
    product_description TEXT NOT NULL
);
