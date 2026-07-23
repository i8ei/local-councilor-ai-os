PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS regulation_documents (
    document_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT,
    source_url TEXT NOT NULL UNIQUE,
    source_name TEXT NOT NULL,
    promulgated_on TEXT,
    enforced_on TEXT,
    fetched_at TEXT NOT NULL,
    adapter TEXT NOT NULL,
    verification_state TEXT NOT NULL CHECK (
        verification_state IN (
            'draft', 'discovered', 'verified', 'reconciled', 'rejected'
        )
    )
);

CREATE TABLE IF NOT EXISTS regulation_articles (
    article_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES regulation_documents(document_id)
        ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    article_no TEXT,
    heading TEXT,
    text TEXT NOT NULL,
    locator TEXT NOT NULL,
    UNIQUE(document_id, seq)
);

CREATE TABLE IF NOT EXISTS regulation_provenance (
    provenance_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES regulation_documents(document_id)
        ON DELETE CASCADE,
    discovered_from TEXT,
    resolved_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    media_type TEXT,
    content_sha256 TEXT,
    adapter TEXT NOT NULL,
    transform TEXT NOT NULL,
    status TEXT NOT NULL,
    cache_path TEXT,
    issues_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE(document_id, resolved_url)
);

CREATE INDEX IF NOT EXISTS regulation_documents_title_idx
ON regulation_documents(title);

CREATE INDEX IF NOT EXISTS regulation_articles_document_seq_idx
ON regulation_articles(document_id, seq);

CREATE VIRTUAL TABLE IF NOT EXISTS regulation_articles_fts USING fts5(
    text,
    heading,
    article_no,
    title,
    document_id UNINDEXED,
    article_id UNINDEXED,
    tokenize='unicode61'
);
