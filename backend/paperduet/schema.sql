PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, title_ko TEXT NOT NULL DEFAULT '',
    arxiv_id TEXT, file_hash TEXT, source_path TEXT,
    status TEXT NOT NULL DEFAULT 'pending', last_block_id TEXT, last_offset REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, opened_at TEXT
);
CREATE TABLE IF NOT EXISTS blocks (
    id TEXT NOT NULL, doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    sort_order INTEGER NOT NULL CHECK(sort_order >= 0),
    type TEXT NOT NULL CHECK(type IN ('sec','sub','ssub','p','li','note','card','eq','fig','tab')),
    section_path TEXT NOT NULL CHECK(json_valid(section_path)),
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    PRIMARY KEY(doc_id,id), UNIQUE(doc_id,sort_order)
);
CREATE TABLE IF NOT EXISTS glossary (
    doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    term TEXT NOT NULL, ko TEXT NOT NULL, keep_english INTEGER NOT NULL DEFAULT 0 CHECK(keep_english IN (0,1)),
    definition_ko TEXT NOT NULL, PRIMARY KEY(doc_id,term)
);
CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, block_id TEXT NOT NULL,
    anchor TEXT NOT NULL CHECK(json_valid(anchor)), preset TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(doc_id,block_id) REFERENCES blocks(doc_id,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user','assistant')), content_md TEXT NOT NULL,
    provider TEXT NOT NULL CHECK(provider IN ('anthropic','openai','google')),
    mode TEXT NOT NULL CHECK(mode IN ('api_key','cli')), model TEXT NOT NULL,
    context_snapshot TEXT NOT NULL CHECK(json_valid(context_snapshot)),
    tokens_in INTEGER, tokens_out INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    stage TEXT NOT NULL, status TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
    checkpoint TEXT CHECK(checkpoint IS NULL OR json_valid(checkpoint)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
-- No API key/token/credential column. Provider secrets belong only in OS keyring (M3).
CREATE TABLE IF NOT EXISTS provider_settings (
    provider TEXT PRIMARY KEY CHECK(provider IN ('anthropic','openai','google')),
    mode TEXT NOT NULL CHECK(mode IN ('api_key','cli')), model TEXT, cli_path TEXT,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1))
);
CREATE TABLE IF NOT EXISTS reader_settings (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    payload TEXT NOT NULL CHECK(json_valid(payload))
);
INSERT OR IGNORE INTO provider_settings(provider,mode) VALUES ('anthropic','api_key');
INSERT OR IGNORE INTO schema_migrations(version) VALUES (1);
PRAGMA user_version = 1;
