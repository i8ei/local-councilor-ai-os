PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS budget_line (
    id INTEGER PRIMARY KEY,
    fiscal_year INTEGER NOT NULL,
    account_name TEXT NOT NULL,
    budget_stage TEXT NOT NULL CHECK (
        budget_stage IN ('initial', 'supplemental', 'current')
    ),
    proposal_no TEXT,
    side TEXT NOT NULL CHECK (side IN ('revenue', 'expenditure')),
    grain TEXT NOT NULL CHECK (grain IN ('total', 'kan', 'ko', 'moku', 'setsu')),
    kan_code TEXT,
    kan_name TEXT,
    ko_code TEXT,
    ko_name TEXT,
    moku_code TEXT,
    moku_name TEXT,
    setsu_code TEXT,
    setsu_name TEXT,
    current_year_amount INTEGER,
    previous_year_amount INTEGER,
    comparison_amount INTEGER,
    pre_supplement_amount INTEGER,
    supplement_amount INTEGER,
    post_supplement_amount INTEGER,
    raw_value TEXT NOT NULL,
    unit TEXT NOT NULL CHECK (length(unit) > 0),
    as_of TEXT NOT NULL,
    definition TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_locator TEXT NOT NULL CHECK (json_valid(source_locator)),
    fetched_at TEXT NOT NULL,
    verification_state TEXT NOT NULL CHECK (
        verification_state IN (
            'draft', 'discovered', 'verified', 'reconciled', 'rejected'
        )
    ),
    fetch_cache_key TEXT,
    robots_decision TEXT,
    request_time TEXT,
    print_page TEXT NOT NULL,
    pdf_page INTEGER NOT NULL CHECK (pdf_page >= 1),
    CHECK (
        grain != 'total' OR (
            kan_code IS NULL AND ko_code IS NULL AND moku_code IS NULL
            AND setsu_code IS NULL
        )
    ),
    CHECK (
        grain != 'kan' OR (
            kan_code IS NOT NULL AND ko_code IS NULL AND moku_code IS NULL
            AND setsu_code IS NULL
        )
    ),
    CHECK (
        grain != 'ko' OR (
            kan_code IS NOT NULL AND ko_code IS NOT NULL
            AND moku_code IS NULL AND setsu_code IS NULL
        )
    ),
    CHECK (
        grain != 'moku' OR (
            kan_code IS NOT NULL AND ko_code IS NOT NULL
            AND moku_code IS NOT NULL AND setsu_code IS NULL
        )
    ),
    CHECK (
        grain != 'setsu' OR (
            kan_code IS NOT NULL AND ko_code IS NOT NULL
            AND moku_code IS NOT NULL AND setsu_code IS NOT NULL
        )
    ),
    CHECK (
        current_year_amount IS NOT NULL
        OR supplement_amount IS NOT NULL
        OR post_supplement_amount IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS budget_line_lookup
ON budget_line(
    fiscal_year, account_name, budget_stage, side, grain,
    kan_code, ko_code, moku_code, setsu_code
);

CREATE INDEX IF NOT EXISTS budget_line_source_idx
ON budget_line(source_url, pdf_page);


CREATE UNIQUE INDEX IF NOT EXISTS budget_line_unique
ON budget_line(
    fiscal_year, account_name, budget_stage, COALESCE(proposal_no, ''),
    side, grain, COALESCE(kan_code, ''), COALESCE(ko_code, ''),
    COALESCE(moku_code, ''), COALESCE(setsu_code, '')
);
