-- Saving mode (provider batch APIs): usage rows record whether the 50% batch
-- price applied, and answers are kept until the paper finishes so a job that
-- restarts mid-batch replays them instead of paying for them again.
ALTER TABLE ai_usage ADD COLUMN batch INTEGER NOT NULL DEFAULT 0 CHECK(batch IN (0,1));
CREATE TABLE ai_results (
    doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL CHECK(json_valid(value)),
    PRIMARY KEY(doc_id, key)
);
INSERT INTO schema_migrations(version) VALUES(7);
PRAGMA user_version=7;
