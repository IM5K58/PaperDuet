ALTER TABLE threads ADD COLUMN title TEXT NOT NULL DEFAULT '';
INSERT INTO schema_migrations(version) VALUES(9);
PRAGMA user_version=9;
