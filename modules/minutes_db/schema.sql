PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meetings (
    meeting_id TEXT PRIMARY KEY,
    council_name TEXT NOT NULL,
    meeting_name TEXT NOT NULL,
    session TEXT,
    date TEXT,
    source_url TEXT NOT NULL UNIQUE,
    adapter TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS speeches (
    speech_id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(meeting_id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    speaker TEXT,
    speaker_role TEXT,
    text TEXT NOT NULL,
    locator TEXT NOT NULL,
    UNIQUE (meeting_id, seq)
);

CREATE TABLE IF NOT EXISTS provenance (
    provenance_id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(meeting_id) ON DELETE CASCADE,
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
    UNIQUE (meeting_id, resolved_url)
);

CREATE INDEX IF NOT EXISTS speeches_meeting_seq_idx
    ON speeches(meeting_id, seq);
CREATE INDEX IF NOT EXISTS meetings_date_idx
    ON meetings(date);
CREATE INDEX IF NOT EXISTS provenance_fetched_at_idx
    ON provenance(fetched_at);

-- This portable declaration is replaced with tokenize='trigram' by ingest.py
-- when the linked SQLite build supports it. unicode61 remains the fallback.
CREATE VIRTUAL TABLE IF NOT EXISTS speeches_fts USING fts5(
    text,
    speaker,
    meeting_id UNINDEXED,
    speech_id UNINDEXED,
    content='speeches',
    content_rowid='rowid',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS speeches_ai AFTER INSERT ON speeches BEGIN
    INSERT INTO speeches_fts(rowid, text, speaker, meeting_id, speech_id)
    VALUES (new.rowid, new.text, new.speaker, new.meeting_id, new.speech_id);
END;

CREATE TRIGGER IF NOT EXISTS speeches_ad AFTER DELETE ON speeches BEGIN
    INSERT INTO speeches_fts(
        speeches_fts, rowid, text, speaker, meeting_id, speech_id
    )
    VALUES (
        'delete', old.rowid, old.text, old.speaker, old.meeting_id, old.speech_id
    );
END;

CREATE TRIGGER IF NOT EXISTS speeches_au AFTER UPDATE ON speeches BEGIN
    INSERT INTO speeches_fts(
        speeches_fts, rowid, text, speaker, meeting_id, speech_id
    )
    VALUES (
        'delete', old.rowid, old.text, old.speaker, old.meeting_id, old.speech_id
    );
    INSERT INTO speeches_fts(rowid, text, speaker, meeting_id, speech_id)
    VALUES (new.rowid, new.text, new.speaker, new.meeting_id, new.speech_id);
END;
