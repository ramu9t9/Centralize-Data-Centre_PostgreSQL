-- Run as superuser (postgres). Creates the app database and assigns owner.
-- Usage: psql -U postgres -h localhost -p 5432 -f scripts/create_db.sql
CREATE DATABASE "Centralized_Index_Option_Data" OWNER nifty_app;
