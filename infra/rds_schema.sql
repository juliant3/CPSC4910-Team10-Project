-- Good Driver Incentive Program
-- Team 10 
-- 4910 Fall 2026

-- Drop tables if they already exist to start fresh
DROP TABLE IF EXISTS Notifications; 
DROP TABLE IF EXISTS Order_Items; 
DROP TABLE IF EXISTS Orders; 
DROP TABLE IF EXISTS Products; 
DROP TABLE IF EXISTS Driver_Applications; 
DROP TABLE IF EXISTS Point_Transactions;
DROP TABLE IF EXISTS Audit_Logs;
DROP TABLE IF EXISTS Sponsor_Users;
DROP TABLE IF EXISTS Password; 
DROP TABLE IF EXISTS Drivers;
DROP TABLE IF EXISTS Sponsors;
DROP TABLE IF EXISTS Users;

-- Users Table
CREATE TABLE Users (
    user_id INT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    role ENUM('Admin', 'Driver', 'Sponsor') NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP
);

-- Sponsors 
CREATE TABLE Sponsors (
    sponsor_id INT PRIMARY KEY,
    company_name VARCHAR(255) NOT NULL,
    email VARCHAR(255), 
    phone VARCHAR(25),
    invite_code VARCHAR(5) UNIQUE,
    point_value DECIMAL(10,4) NOT NULL DEFAULT 0.0100,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    FOREIGN KEY(sponsor_id)
        REFERENCES Users(user_id)
        ON DELETE CASCADE, 
    CHECK (point_value > 0) 
);


-- Drivers 
CREATE TABLE Drivers (
    driver_id INT PRIMARY KEY,
    points_balance INT  NOT NULL DEFAULT 0,
    sponsor_id INT NULL,
    status ENUM(
        'Applicant', 
        'Active', 
        'Rejected', 
        'Dropped', 
        'Inactive'
    ) NOT NULL DEFAULT 'Applicant',
    FOREIGN KEY (driver_id)
        REFERENCES Users(user_id) 
        ON DELETE CASCADE,
    FOREIGN KEY (sponsor_id)
        REFERENCES Sponsors(sponsor_id) 
        ON DELETE SET NULL,
    CHECK (points_balance >= 0)
);


-- Sponsor Users
CREATE TABLE Sponsor_Users (
    sponsor_user_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL UNIQUE, 
    sponsor_id INT NOT NULL, 
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
    FOREIGN KEY (user_id)
        REFERENCES Users(user_id)
        ON DELETE CASCADE, 
    FOREIGN KEY (sponsor_id)
        REFERENCES Sponsors(sponsor_id)
        ON DELETE CASCADE
); 

-- Driver Applications
CREATE TABLE Driver_Applications(
    application_id INT AUTO_INCREMENT PRIMARY KEY,
    driver_id INT NOT NULL, 
    sponsor_id INT NOT NULL, 
    application_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
    status ENUM(
        'Pending', 
        'Accepted', 
        'Rejected'
    )NOT NULL DEFAULT 'Pending', 
    decision_date TIMESTAMP NULL, 
    decision_by_user_id INT NULL, 
    reason VARCHAR(1000), 
    FOREIGN KEY (driver_id)
        REFERENCES Drivers(driver_id), 
    FOREIGN KEY (sponsor_id)
        REFERENCES Sponsors(sponsor_id), 
    FOREIGN KEY (decision_by_user_id)
        REFERENCES Users(user_id)
);

-- Point Transactions 
CREATE TABLE Point_Transactions (
    transaction_id INT AUTO_INCREMENT PRIMARY KEY,
    driver_id INT NOT NULL,
    sponsor_id INT NOT NULL,
    changed_by_user_id INT NOT NULL,
    point_change INT NOT NULL,
    reason VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (driver_id) REFERENCES Drivers(driver_id),
    FOREIGN KEY (sponsor_id) REFERENCES Sponsors(sponsor_id), 
    FOREIGN KEY (changed_by_user_id) REFERENCES Users(user_id)
);

-- Products 
CREATE TABLE Products (
    product_id INT AUTO_INCREMENT PRIMARY KEY,
    sponsor_id INT NOT NULL,
    external_product_id VARCHAR(255) NOT NULL,
    product_name VARCHAR(500) NOT NULL,
    description TEXT,
    image_url VARCHAR(1000),
    product_url VARCHAR(1000),
    price_dollars DECIMAL(10,2) NOT NULL,
    price_points INT NOT NULL,
    availability BOOLEAN NOT NULL DEFAULT TRUE,
    content_rating ENUM('G', 'PG') NOT NULL,
    api_source VARCHAR(255) NOT NULL,
    last_api_update TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (sponsor_id)
        REFERENCES Sponsors(sponsor_id) 
        ON DELETE CASCADE, 
    CHECK (price_dollars >= 0), 
    CHECK (price_points >= 0), 
    UNIQUE (sponsor_id, external_product_id)
);

-- Orders
CREATE TABLE Orders (
    order_id INT AUTO_INCREMENT PRIMARY KEY,
    order_number VARCHAR(50) NOT NULL UNIQUE,
    driver_id INT NOT NULL,
    sponsor_id INT NOT NULL,
    placed_by_user_id INT NOT NULL,
    order_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status ENUM(
        'Placed',
        'Processing',
        'Shipped',
        'Delivered',
        'Cancelled',
        'Updated'
    ) NOT NULL DEFAULT 'Placed',
    total_points INT NOT NULL DEFAULT 0,
    total_dollars DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
     FOREIGN KEY (driver_id)
        REFERENCES Drivers(driver_id),
    FOREIGN KEY (sponsor_id)
        REFERENCES Sponsors(sponsor_id),
    FOREIGN KEY (placed_by_user_id)
        REFERENCES Users(user_id),
    CHECK (total_points >= 0),
    CHECK (total_dollars >= 0)
);

-- Order Items 
CREATE TABLE Order_Items (
    order_item_id INT AUTO_INCREMENT PRIMARY KEY,
    order_id INT NOT NULL,
    product_id INT NOT NULL,
    quantity INT NOT NULL,
    product_name VARCHAR(500) NOT NULL,
    unit_price_points INT NOT NULL,
    unit_price_dollars DECIMAL(10,2) NOT NULL,
    FOREIGN KEY (order_id)
        REFERENCES Orders(order_id)
        ON DELETE CASCADE,
    FOREIGN KEY (product_id)
        REFERENCES Products(product_id),
    CHECK (quantity > 0),
    CHECK (unit_price_points >= 0),
    CHECK (unit_price_dollars >= 0)
);

-- Notifications 
CREATE TABLE Notifications (
    notification_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    notification_type ENUM(
        'Dropped',
        'Point Change',
        'Order Placed'
    ) NOT NULL,
    subject VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id)
        REFERENCES Users(user_id)
        ON DELETE CASCADE
);

-- Audit Logs
CREATE TABLE Audit_Logs (
    log_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT,
    sponsor_id INT NULL, 
    driver_id INT NULL,
    category ENUM(
        'Driver Application', 
        'Point Change', 
        'Password Change', 
        'Login Attempt'
    )NOT NULL,
    action VARCHAR(255) NOT NULL,
    status VARCHAR(100),
    reason VARCHAR(1000),
    username VARCHAR(255),
    success BOOLEAN NULL,
    ip_address VARCHAR(45),
    details TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES Users(user_id) ON DELETE SET NULL,
    FOREIGN KEY (sponsor_id)
        REFERENCES Sponsors(sponsor_id)
        ON DELETE SET NULL,
    FOREIGN KEY (driver_id)
        REFERENCES Drivers(driver_id)
        ON DELETE SET NULL
);

-- Password 
CREATE TABLE Password (
    password_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) 
        REFERENCES Users(user_id) 
        ON DELETE CASCADE
);

-- Indexes
CREATE INDEX idx_drivers_sponsor
    ON Drivers(sponsor_id);

CREATE INDEX idx_drivers_status
    ON Drivers(status);

CREATE INDEX idx_point_driver
    ON Point_Transactions(driver_id);

CREATE INDEX idx_point_sponsor
    ON Point_Transactions(sponsor_id);

CREATE INDEX idx_point_date
    ON Point_Transactions(created_at);

CREATE INDEX idx_applications_sponsor
    ON Driver_Applications(sponsor_id);

CREATE INDEX idx_applications_driver
    ON Driver_Applications(driver_id);

CREATE INDEX idx_applications_status
    ON Driver_Applications(status);

CREATE INDEX idx_products_sponsor
    ON Products(sponsor_id);

CREATE INDEX idx_orders_driver
    ON Orders(driver_id);

CREATE INDEX idx_orders_sponsor
    ON Orders(sponsor_id);

CREATE INDEX idx_orders_date
    ON Orders(order_date);

CREATE INDEX idx_audit_category
    ON Audit_Logs(category);

CREATE INDEX idx_audit_date
    ON Audit_Logs(timestamp);

CREATE INDEX idx_audit_sponsor
    ON Audit_Logs(sponsor_id);

CREATE INDEX idx_audit_driver
    ON Audit_Logs(driver_id);


-- Recurring points: target all drivers or only the ones listed below
ALTER TABLE Recurring_Points
    ADD COLUMN all_drivers TINYINT(1) NOT NULL DEFAULT 1;

CREATE TABLE Recurring_Point_Drivers (
    recurring_id INT NOT NULL,
    driver_id INT NOT NULL,
    PRIMARY KEY (recurring_id, driver_id),
    FOREIGN KEY (recurring_id)
        REFERENCES Recurring_Points(recurring_id)
        ON DELETE CASCADE,
    FOREIGN KEY (driver_id)
        REFERENCES Drivers(driver_id)
        ON DELETE CASCADE
);


-- Sponsor access requests (2026-10-20)
-- A prospective sponsor requests access from the login page, gets an emailed
-- link, applies for a new organization, and an admin approves or rejects it.
-- request_type 'Join' / target_sponsor_id are reserved for joining an
-- existing organization later.
CREATE TABLE Sponsor_Requests (
    request_id INT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    first_name VARCHAR(100) NOT NULL,
    last_name VARCHAR(100) NOT NULL,
    token VARCHAR(64) NOT NULL UNIQUE,
    request_type ENUM('New', 'Join') NOT NULL DEFAULT 'New',
    company_name VARCHAR(255) NULL,
    phone VARCHAR(25) NULL,
    message VARCHAR(1000) NULL,
    target_sponsor_id INT NULL,
    status ENUM(
        'Started',
        'Submitted',
        'Approved',
        'Rejected'
    ) NOT NULL DEFAULT 'Started',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    submitted_at TIMESTAMP NULL,
    decision_date TIMESTAMP NULL,
    decision_by_user_id INT NULL,
    reason VARCHAR(1000) NULL,
    user_id INT NULL,
    FOREIGN KEY (target_sponsor_id)
        REFERENCES Sponsors(sponsor_id)
        ON DELETE SET NULL,
    FOREIGN KEY (decision_by_user_id)
        REFERENCES Users(user_id)
        ON DELETE SET NULL,
    FOREIGN KEY (user_id)
        REFERENCES Users(user_id)
        ON DELETE SET NULL
);

CREATE INDEX idx_sponsor_requests_status
    ON Sponsor_Requests(status);

CREATE INDEX idx_sponsor_requests_email
    ON Sponsor_Requests(email);


-- Multiple sponsors per driver (Requirements Change #2, 2026-10-20)
-- Each row is one driver's membership in one sponsor's program, with its own
-- status and its own point balance (points never mix between sponsors).
-- Drivers.sponsor_id and Drivers.points_balance are no longer used by the app;
-- they are left in place so nothing that still reads them breaks.
CREATE TABLE Driver_Sponsors (
    driver_id INT NOT NULL,
    sponsor_id INT NOT NULL,
    status ENUM(
        'Applicant',
        'Active',
        'Rejected',
        'Dropped',
        'Inactive'
    ) NOT NULL DEFAULT 'Active',
    points_balance INT NOT NULL DEFAULT 0,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (driver_id, sponsor_id),
    FOREIGN KEY (driver_id)
        REFERENCES Drivers(driver_id)
        ON DELETE CASCADE,
    FOREIGN KEY (sponsor_id)
        REFERENCES Sponsors(sponsor_id)
        ON DELETE CASCADE,
    CHECK (points_balance >= 0)
);

CREATE INDEX idx_driver_sponsors_sponsor
    ON Driver_Sponsors(sponsor_id, status);

-- Copy every existing driver/sponsor link and balance into the new table.
-- Safe to re-run: it refreshes rows that already exist, so run it again right
-- before deploying if points changed after the first run.
INSERT INTO Driver_Sponsors (driver_id, sponsor_id, status, points_balance)
SELECT * FROM (
    SELECT driver_id, sponsor_id, status, COALESCE(points_balance, 0) AS points_balance
    FROM Drivers
    WHERE sponsor_id IS NOT NULL
) AS src
ON DUPLICATE KEY UPDATE
    status = src.status,
    points_balance = src.points_balance;


-- Catalog item types (2026-10-25)
-- Each sponsor picks the types of items in its catalog (e.g. Hats, Cars);
-- drivers filter between them. Replaces the single search term in
-- Sponsor_Catalog_Settings.
CREATE TABLE IF NOT EXISTS Sponsor_Catalog_Types (
    sponsor_id INT NOT NULL,
    item_type VARCHAR(60) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (sponsor_id, item_type),
    FOREIGN KEY (sponsor_id)
        REFERENCES Sponsors(sponsor_id)
        ON DELETE CASCADE
);

-- Keep each sponsor's old saved search as its first item type.
-- Skip this if your database never had Sponsor_Catalog_Settings.
INSERT IGNORE INTO Sponsor_Catalog_Types (sponsor_id, item_type)
SELECT sponsor_id, LEFT(TRIM(search_term), 60)
FROM Sponsor_Catalog_Settings
WHERE TRIM(COALESCE(search_term, '')) <> '';

-- Catalog purchases use the existing Products, Orders and Order_Items tables:
-- the driver pays in points (Driver_Sponsors.points_balance) and nothing is
-- ordered from eBay.
