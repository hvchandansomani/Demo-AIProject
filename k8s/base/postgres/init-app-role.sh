#!/bin/sh
# Runs ONCE, the first time PostgreSQL starts on an empty data volume
# (the official image runs every script in /docker-entrypoint-initdb.d).
#
# Least privilege:
#   postgres  - superuser and owner of the database and the doubts table
#   doubtwall - the role the app logs in as: may read and write rows,
#               may NOT create, alter or drop anything
#
# Keep the table definition in step with SCHEMA in doubt-wall/app/db.py.
set -eu
: "${APP_DB_USER:=doubtwall}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v app_user="$APP_DB_USER" -v app_password="$APP_DB_PASSWORD" <<'EOSQL'
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_password' CONNECTION LIMIT 60;

-- Guardrails applied to every session of the app role
ALTER ROLE :"app_user" SET statement_timeout = '5s';
ALTER ROLE :"app_user" SET idle_in_transaction_session_timeout = '30s';

-- Nobody but the owner can create objects in the public schema
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO :"app_user";

CREATE TABLE doubts (
    id         SERIAL PRIMARY KEY,
    nickname   TEXT        NOT NULL CHECK (char_length(nickname) BETWEEN 1 AND 100),
    question   TEXT        NOT NULL CHECK (char_length(question) BETWEEN 1 AND 2000),
    upvotes    INT         NOT NULL DEFAULT 0,
    answered   BOOLEAN     NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX doubts_created_at_idx ON doubts (created_at DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON doubts TO :"app_user";
GRANT USAGE, SELECT ON SEQUENCE doubts_id_seq TO :"app_user";
EOSQL
echo "init-app-role: created role $APP_DB_USER and table doubts"
