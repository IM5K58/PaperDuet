ALTER TABLE documents ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'pdf';
ALTER TABLE documents ADD COLUMN source_url TEXT NOT NULL DEFAULT '';
DROP INDEX document_hash;
CREATE UNIQUE INDEX document_hash ON documents(file_hash) WHERE file_hash IS NOT NULL AND source_kind='pdf';
CREATE TABLE presentation_notes (
 id TEXT PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 block_id TEXT, message_id TEXT, title TEXT NOT NULL, body_md TEXT NOT NULL,
 sort_order INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(doc_id,message_id)
);
CREATE INDEX presentation_document ON presentation_notes(doc_id,sort_order);
INSERT INTO schema_migrations(version) VALUES(5);
PRAGMA user_version=5;
