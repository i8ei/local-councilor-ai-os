PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS benchmark_municipality (
    area_code_5 TEXT PRIMARY KEY CHECK(length(area_code_5) = 5),
    local_government_code_6 TEXT UNIQUE CHECK(length(local_government_code_6) = 6),
    name TEXT NOT NULL,
    prefecture TEXT NOT NULL,
    municipality_kind TEXT,
    source_url TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS benchmark_indicator (
    id INTEGER PRIMARY KEY,
    area_code_5 TEXT NOT NULL REFERENCES benchmark_municipality(area_code_5),
    indicator_key TEXT NOT NULL,
    value REAL,
    raw_value TEXT NOT NULL,
    unit TEXT NOT NULL,
    as_of TEXT NOT NULL,
    definition TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_locator TEXT NOT NULL CHECK(json_valid(source_locator)),
    fetched_at TEXT NOT NULL,
    verification_state TEXT NOT NULL CHECK (
        verification_state IN (
            'draft', 'discovered', 'verified', 'reconciled', 'rejected',
            'verified_source_extraction', 'needs_review'
        )
    ),
    UNIQUE(area_code_5, indicator_key, as_of, source_url)
);

CREATE INDEX IF NOT EXISTS benchmark_indicator_lookup
ON benchmark_indicator(indicator_key, as_of, area_code_5);

CREATE VIEW IF NOT EXISTS benchmark_latest_indicator AS
SELECT bi.*
FROM benchmark_indicator AS bi
JOIN (
    SELECT area_code_5, indicator_key, MAX(as_of) AS as_of
    FROM benchmark_indicator
    GROUP BY area_code_5, indicator_key
) AS latest
  ON latest.area_code_5 = bi.area_code_5
 AND latest.indicator_key = bi.indicator_key
 AND latest.as_of = bi.as_of;
