#!/bin/bash
# running a bash script (instead of the sql directly) so that we can inject env variables

psql $POSTGRES_DB << EOF
CREATE EXTENSION vector;

CREATE TABLE IF NOT EXISTS $POSTGRES_ARXIV_TABLE (
    id text PRIMARY KEY,
    title text NOT NULL,
    abstract text NOT NULL,
    embedding vector($EMBEDDING_MODEL_DIM) NOT NULL,
    fts tsvector GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || abstract)) STORED
);

CREATE INDEX fts_idx ON arxiv_meta USING GIN (fts);

CREATE TABLE IF NOT EXISTS $POSTGRES_RESUMABLES_TABLE (
    id text PRIMARY KEY,
    value text NOT NULL,
    expire_date TIMESTAMPTZ
);
EOF
