-- Idempotent role + grants for the MCP read-only connector (Phase 3.1.5).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'estc_reader') THEN
        CREATE ROLE estc_reader LOGIN PASSWORD 'estc_reader_dev_pw';
    END IF;
END $$;

REVOKE ALL ON enterprise_customers FROM estc_reader;
GRANT SELECT ON enterprise_customers TO estc_reader;
GRANT USAGE ON SCHEMA public TO estc_reader;
