-- infra/sql/init.sql
CREATE TABLE IF NOT EXISTS enterprise_customers (
    company_id VARCHAR(50) PRIMARY KEY,
    company_name VARCHAR(100),
    subscription_tier VARCHAR(20), -- 'Enterprise', 'Growth', 'Free'
    account_status VARCHAR(20),    -- 'Active', 'Delinquent', 'Locked'
    technical_poc_email VARCHAR(100)
);