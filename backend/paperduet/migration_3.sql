CREATE TABLE annotation_sections (
    doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    section_key TEXT NOT NULL,
    PRIMARY KEY(doc_id, section_key)
);
CREATE TABLE note_anchors (
    doc_id TEXT NOT NULL, note_id TEXT NOT NULL, after_block_id TEXT NOT NULL,
    section_key TEXT NOT NULL,
    PRIMARY KEY(doc_id, note_id),
    FOREIGN KEY(doc_id, note_id) REFERENCES blocks(doc_id,id) ON DELETE CASCADE,
    FOREIGN KEY(doc_id, after_block_id) REFERENCES blocks(doc_id,id) ON DELETE CASCADE
);
INSERT INTO schema_migrations(version) VALUES(3);
PRAGMA user_version=3;
