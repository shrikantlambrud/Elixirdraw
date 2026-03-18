-- Users Table
CREATE TABLE users (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100),
    business_name VARCHAR(200),
    email VARCHAR(100),
    phone VARCHAR(20),
    address TEXT,
    password TEXT,
    role VARCHAR(20),
    udyam_no VARCHAR(50),
    document VARCHAR(200),
    status VARCHAR(20),
    wallet_balance INT DEFAULT 0
);

-- Properties Table
CREATE TABLE properties (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id INT,
    title VARCHAR(200),
    price VARCHAR(100),
    deposit INT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    location VARCHAR(200),
    property_type VARCHAR(50),
    description TEXT,
    flat_type VARCHAR(50),
    plot_size VARCHAR(50),
    map_link TEXT,
    image1 VARCHAR(200),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE wallet_requests (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id INT,
    amount INT,
    utr_number VARCHAR(100),
    payment_screenshot VARCHAR(200),
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE materials (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id INT,
    title VARCHAR(200),
    price VARCHAR(100),
    location VARCHAR(200),
    description TEXT,
    map_link TEXT,
    quantity VARCHAR(100),
    image1 VARCHAR(200),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);