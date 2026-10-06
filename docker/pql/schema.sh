#!/bin/bash
# running a bash script (instead of the sql directly) so that we can inject env variables

psql $POSTGRES_DB << EOF
CREATE EXTENSION vector;

CREATE TABLE IF NOT EXISTS $POSTGRES_ARXIV_TABLE (
    id text PRIMARY KEY,
    datestamp TEXT NOT NULL,
    title text NOT NULL,
    abstract text NOT NULL,
    embedding vector($ETL__EMBEDDING_MODEL_DIM) NOT NULL,
    fts tsvector GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || abstract)) STORED
);

CREATE INDEX fts_idx ON arxiv_meta USING GIN (fts);
CREATE INDEX ON arxiv_meta USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS $POSTGRES_RESUMABLES_TABLE (
    id SERIAL PRIMARY KEY,
    set text NOT NULL,
    resume_token text,
    token_expire_date TIMESTAMPTZ,
    resume_datestamp text,
    inserted_at TIMESTAMPTZ default current_timestamp NOT NULL
);
EOF
