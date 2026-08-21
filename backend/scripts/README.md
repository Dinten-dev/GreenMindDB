# Backend maintenance scripts

This directory is not part of application startup, migrations, or developer onboarding.

- `cleanup_demo_gateways.py` is a manual data-maintenance utility for removing gateways whose
  names or hardware identifiers are explicitly marked as demo/test data. Review its query and
  take a database backup before running it.

Database structure is owned by Alembic under `backend/alembic/`. Do not add seed or import
scripts that create rows against historical table names; use the current API or a reviewed,
versioned migration instead.
