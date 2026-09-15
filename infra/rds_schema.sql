-- Drop tables if they already exist to start fresh
DROP TABLE IF EXISTS Point_Transactions;
DROP TABLE IF EXISTS Audit_Logs;
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Drivers 
CREATE TABLE Drivers (
    driver_id INT PRIMARY KEY,
    points_balance INT DEFAULT 0,
    FOREIGN KEY (driver_id) REFERENCES Users(user_id) ON DELETE CASCADE
);

-- Sponsors 
CREATE TABLE Sponsors (
    sponsor_id INT PRIMARY KEY,
    company_name VARCHAR(255) NOT NULL,
    FOREIGN KEY (sponsor_id) REFERENCES Users(user_id) ON DELETE CASCADE
);

-- Point Transactions 
CREATE TABLE Point_Transactions (
    transaction_id INT AUTO_INCREMENT PRIMARY KEY,
    driver_id INT NOT NULL,
    sponsor_id INT NOT NULL,
    point_change INT NOT NULL,
    reason VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (driver_id) REFERENCES Drivers(driver_id),
    FOREIGN KEY (sponsor_id) REFERENCES Sponsors(sponsor_id)
);

CREATE TABLE Audit_Logs (
    log_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT,
    action VARCHAR(255) NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES Users(user_id) ON DELETE SET NULL
);

CREATE TABLE Password (
    password_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES Users(user_id) ON DELETE CASCADE
);
