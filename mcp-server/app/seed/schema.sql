-- Schema for the MCP server's mock data.
--
-- Idempotent: dropped and recreated on every seed run (see build_seed.py), so a
-- re-seed always yields a clean, deterministic database. Order of DROP respects
-- the FK graph (documents is independent; shipments -> orders -> customers).

DROP TABLE IF EXISTS documents CASCADE;
DROP TABLE IF EXISTS shipments CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS customers CASCADE;

-- Customers are organizations (not people) — no PII in the main seed.
CREATE TABLE customers (
    id         integer PRIMARY KEY,
    name       text NOT NULL,
    region     text NOT NULL,
    created_at timestamptz NOT NULL
);

CREATE TABLE orders (
    id          integer PRIMARY KEY,
    customer_id integer NOT NULL REFERENCES customers (id),
    status      text NOT NULL,          -- failed | processing | shipped | delivered
    total_cents integer NOT NULL,
    created_at  timestamptz NOT NULL
);

CREATE TABLE shipments (
    id              integer PRIMARY KEY,
    order_id        integer NOT NULL REFERENCES orders (id),
    carrier         text NOT NULL,
    tracking_number text NOT NULL,
    status          text NOT NULL,      -- exception | lost | delayed | in_transit | delivered
    last_update     timestamptz NOT NULL
);

-- Documents are pre-chunked. chunk_id is the stable, opaque retrieval ID
-- (e.g. doc_007#chunk_1) that search_documents returns as retrieval_ids. The
-- tsv column is generated from title + text and GIN-indexed for full-text
-- search (websearch_to_tsquery + ts_rank_cd).
CREATE TABLE documents (
    chunk_id text PRIMARY KEY,
    doc_id   text NOT NULL,
    title    text NOT NULL,
    text     text NOT NULL,
    tsv      tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(title, '') || ' ' || coalesce(text, ''))
    ) STORED
);

CREATE INDEX documents_tsv_gin ON documents USING GIN (tsv);
CREATE INDEX orders_status_created_idx ON orders (status, created_at);
CREATE INDEX shipments_order_idx ON shipments (order_id);
