-- Initial PostgreSQL setup for Plant Wiki
-- Tables are created by Alembic migrations, this file is for any additional setup

-- Enable UUID extension if needed
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "timescaledb";
