CREATE TABLE app_settings (key TEXT PRIMARY KEY, payload TEXT NOT NULL CHECK(json_valid(payload)));
ALTER TABLE messages ADD COLUMN status TEXT NOT NULL DEFAULT 'complete';
ALTER TABLE messages ADD COLUMN error_code TEXT;
CREATE INDEX threads_document ON threads(doc_id,block_id);
CREATE INDEX messages_thread ON messages(thread_id,created_at);
INSERT INTO schema_migrations(version) VALUES(4);
PRAGMA user_version=4;
