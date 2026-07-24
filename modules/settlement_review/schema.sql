PRAGMA foreign_keys = ON;

-- One row represents one heading in the summary table at kan grain.
CREATE TABLE IF NOT EXISTS settlement_summary (
    id INTEGER PRIMARY KEY,
    fiscal_year INTEGER NOT NULL,
    account_name TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('revenue', 'expenditure')),
    kan_code TEXT NOT NULL,
    kan_name TEXT NOT NULL,
    budget_current_amount INTEGER NOT NULL,
    collected_amount INTEGER,
    uncollectible_amount INTEGER,
    outstanding_amount INTEGER,
    spent_amount INTEGER,
    carryover_amount INTEGER,
    unused_amount INTEGER,
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
        (
            side = 'revenue'
            AND collected_amount IS NOT NULL
            AND uncollectible_amount IS NOT NULL
            AND outstanding_amount IS NOT NULL
            AND spent_amount IS NULL
            AND carryover_amount IS NULL
            AND unused_amount IS NULL
        )
        OR
        (
            side = 'expenditure'
            AND collected_amount IS NULL
            AND uncollectible_amount IS NULL
            AND outstanding_amount IS NULL
            AND spent_amount IS NOT NULL
            AND carryover_amount IS NOT NULL
            AND unused_amount IS NOT NULL
        )
    ),
    UNIQUE (fiscal_year, account_name, side, kan_code)
);

-- Revenue detail has two accounting grains: kan and ko.
CREATE TABLE IF NOT EXISTS settlement_revenue (
    id INTEGER PRIMARY KEY,
    fiscal_year INTEGER NOT NULL,
    account_name TEXT NOT NULL,
    kan_code TEXT NOT NULL,
    kan_name TEXT NOT NULL,
    ko_code TEXT NOT NULL,
    ko_name TEXT NOT NULL,
    budget_current_amount INTEGER NOT NULL,
    collected_amount INTEGER NOT NULL,
    uncollectible_amount INTEGER NOT NULL,
    outstanding_amount INTEGER NOT NULL,
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
    UNIQUE (fiscal_year, account_name, kan_code, ko_code)
);

-- Expenditure detail is stored at setsu grain.
-- Item totals repeat on each section row and must be deduplicated before summing.
CREATE TABLE IF NOT EXISTS settlement_expenditure (
    id INTEGER PRIMARY KEY,
    fiscal_year INTEGER NOT NULL,
    account_name TEXT NOT NULL,
    kan_code TEXT NOT NULL,
    kan_name TEXT NOT NULL,
    ko_code TEXT NOT NULL,
    ko_name TEXT NOT NULL,
    moku_code TEXT NOT NULL,
    moku_name TEXT NOT NULL,
    setsu_code TEXT NOT NULL,
    setsu_name TEXT NOT NULL,
    block_no INTEGER NOT NULL DEFAULT 1 CHECK (block_no >= 1),
    item_budget_current_amount INTEGER NOT NULL,
    item_spent_amount INTEGER NOT NULL,
    item_carryover_amount INTEGER NOT NULL,
    item_unused_amount INTEGER NOT NULL,
    section_budget_current_amount INTEGER NOT NULL,
    section_spent_amount INTEGER NOT NULL,
    section_carryover_amount INTEGER NOT NULL,
    section_unused_amount INTEGER NOT NULL,
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
    UNIQUE (
        fiscal_year, account_name, kan_code, ko_code, moku_code,
        setsu_code, block_no
    )
);

CREATE INDEX IF NOT EXISTS settlement_summary_lookup
ON settlement_summary(fiscal_year, account_name, side, kan_code);

CREATE INDEX IF NOT EXISTS settlement_revenue_lookup
ON settlement_revenue(fiscal_year, account_name, kan_code, ko_code);

CREATE INDEX IF NOT EXISTS settlement_expenditure_lookup
ON settlement_expenditure(
    fiscal_year, account_name, kan_code, ko_code, moku_code, setsu_code
);
