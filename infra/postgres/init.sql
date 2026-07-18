-- AetherGate PostgreSQL bootstrap
-- Same instance for Grand Admin; isolated stores per app

CREATE SCHEMA IF NOT EXISTS webui;
GRANT ALL ON SCHEMA webui TO CURRENT_USER;

-- LiteLLM uses Prisma (hardcoded public schema) → own database
SELECT 'CREATE DATABASE litellm'
WHERE NOT EXISTS (
    SELECT FROM pg_database WHERE datname = 'litellm'
)\gexec

-- Authentik SSO
SELECT 'CREATE DATABASE authentik'
WHERE NOT EXISTS (
    SELECT FROM pg_database WHERE datname = 'authentik'
)\gexec
