#!/bin/bash
# running a bash script (instead of the sql directly) so that we can inject env variables

psql $POSTGRES_DB << EOF
CREATE EXTENSION vector;

CREATE TABLE IF NOT EXISTS arxiv_meta (
    id bigserial PRIMARY KEY,
    title text NOT NULL,
    abstract text NOT NULL,
    embedding vector($EMBEDDING_MODEL_DIM) NOT NULL,
    fts tsvector GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || abstract)) STORED
);

CREATE INDEX fts_idx ON arxiv_meta USING GIN (fts);
EOF
