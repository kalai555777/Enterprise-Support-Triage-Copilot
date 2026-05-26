-- infra/sql/seed.sql
INSERT INTO enterprise_customers (company_id, company_name, subscription_tier, account_status, technical_poc_email) VALUES
('c-01', 'Acme Corp', 'Enterprise', 'Active', 'tech-poc@acme.com'),
('c-02', 'Globex Corporation', 'Enterprise', 'Delinquent', 'admin@globex.com'),
('c-03', 'Initech LLC', 'Growth', 'Active', 'peter@initech.com'),
('c-04', 'Umbrella Corp', 'Enterprise', 'Locked', 'security@umbrella.com'),
('c-05', 'Hooli Inc', 'Enterprise', 'Active', 'richard@hooli.xyz'),
('c-06', 'Soylent Green Co', 'Free', 'Active', 'info@soylent.com'),
('c-07', 'Initech Software', 'Growth', 'Delinquent', 'samir@initech.com'),
('c-08', 'Wayne Enterprises', 'Enterprise', 'Active', 'bwayne@wayne.corp'),
('c-09', 'Stark Industries', 'Enterprise', 'Active', 'pepper@stark.com'),
('c-10', 'Cyberdyne Systems', 'Growth', 'Locked', 'miles@cyberdyne.com'),
('c-11', 'Tesco Labs', 'Free', 'Active', 'support@tesco.io'),
('c-12', 'Tyrell Nexus', 'Enterprise', 'Active', 'elden@tyrell.com'),
('c-13', 'Massive Dynamic', 'Enterprise', 'Delinquent', 'nina@massivedyn.com'),
('c-14', 'Veer Group', 'Growth', 'Active', 'contact@veer.co'),
('c-15', 'Bluth Company', 'Free', 'Locked', 'michael@bluthco.com'),
('c-16', 'Gekko Investments', 'Growth', 'Active', 'bud@gekko.com'),
('c-17', 'Prestige Worldwide', 'Free', 'Delinquent', 'dale@prestige.com'),
('c-18', 'Aperture Science', 'Enterprise', 'Active', 'cave@aperture.com'),
('c-19', 'Dunder Mifflin', 'Growth', 'Active', 'dwight@dundermifflin.com'),
('c-20', 'E Corp', 'Enterprise', 'Locked', 'elliot@ecorp.com')
ON CONFLICT (company_id) DO NOTHING;