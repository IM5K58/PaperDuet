import json
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .adapter import adapt_fixture
from .models import Block, Document, PipelineOptions, ReaderSettings, ReadingPosition


class Store:
    def __init__(self, data_dir: Path, fixture: Path):
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "paperduet.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 8:
                raise RuntimeError("Database version is newer than this app")
            if version == 0:
                db.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
            if version < 2:
                db.executescript("BEGIN IMMEDIATE;\n" + Path(__file__).with_name("migration_2.sql").read_text(encoding="utf-8") + "\nCOMMIT;")
            if version < 3:
                db.executescript("BEGIN IMMEDIATE;\n" + Path(__file__).with_name("migration_3.sql").read_text(encoding="utf-8") + "\nCOMMIT;")
            if version < 4:
                db.executescript("BEGIN IMMEDIATE;\n" + Path(__file__).with_name("migration_4.sql").read_text(encoding="utf-8") + "\nCOMMIT;")
            if version < 5:
                db.executescript("BEGIN IMMEDIATE;\n" + Path(__file__).with_name("migration_5.sql").read_text(encoding="utf-8") + "\nCOMMIT;")
            if version < 6:
                db.executescript("BEGIN IMMEDIATE;\n" + Path(__file__).with_name("migration_6.sql").read_text(encoding="utf-8") + "\nCOMMIT;")
            if version < 7:
                db.executescript("BEGIN IMMEDIATE;\n" + Path(__file__).with_name("migration_7.sql").read_text(encoding="utf-8") + "\nCOMMIT;")
            if version < 8:
                db.executescript("BEGIN IMMEDIATE;\n" + Path(__file__).with_name("migration_8.sql").read_text(encoding="utf-8") + "\nCOMMIT;")
            # The sample paper is optional: it is not published with the source,
            # and a user who deleted it does not get it back on the next start.
            if fixture.is_file() and not db.execute("SELECT 1 FROM documents WHERE id='rex-omni'").fetchone() \
                    and not db.execute("SELECT 1 FROM app_settings WHERE key='sample_removed'").fetchone():
                doc = adapt_fixture(fixture)
                db.execute("INSERT INTO documents(id,title,title_ko,arxiv_id,status) VALUES(?,?,?,?,?)",
                           (doc.id, doc.title, doc.title_ko, doc.arxiv_id, doc.status))
                db.executemany("INSERT INTO blocks(id,doc_id,sort_order,type,section_path,payload) VALUES(?,?,?,?,?,?)",
                               [(b.id, b.doc_id, b.order, b.type, json.dumps(b.section_path),
                                 b.model_dump_json(exclude_none=True)) for b in doc.blocks])

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def asset_path(self, doc_id: str, stored_path: str) -> Path | None:
        relative = Path(stored_path)
        document = Path('documents') / doc_id
        if relative.is_absolute() or '..' in relative.parts or not relative.is_relative_to(document):
            return None
        # Windows packaged-process AppData virtualization can redirect one
        # document while leaving its parent directory at the logical path.
        # Compare canonical paths within that document, and reject leaf links
        # escaping it. The lexical check above also rejects cross-document paths.
        root = (self.data_dir / document).resolve()
        path = (self.data_dir / relative).resolve()
        return path if path.is_relative_to(root) and path.is_file() else None

    def document(self, doc_id: str) -> Document | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
            if row is None:
                return None
            blocks = [Block.model_validate_json(b[0]) for b in db.execute(
                "SELECT payload FROM blocks WHERE doc_id=? ORDER BY sort_order", (doc_id,))]
            if row['status']!='fixture':
                from .table_style import decorate_tables
                decorate_tables(blocks)  # Existing M1 documents also work offline.
            return Document(id=row["id"], title=row["title"], title_ko=row["title_ko"],
                            source_kind=row['source_kind'],source_url=row['source_url'],
                            arxiv_id=row["arxiv_id"] or "", status=row["status"], blocks=blocks,
                            page_count=row["page_count"], authors=row["authors"],
                            glossary=[dict(g) for g in db.execute("SELECT term,ko,keep_english,definition_ko FROM glossary WHERE doc_id=? ORDER BY term", (doc_id,))],
                            reading_position=ReadingPosition(block_id=row["last_block_id"], offset=row["last_offset"]))

    def position(self, doc_id: str, position: ReadingPosition):
        with self.connect() as db:
            if position.block_id is not None and not db.execute(
                "SELECT 1 FROM blocks WHERE doc_id=? AND id=?", (doc_id, position.block_id)
            ).fetchone():
                raise ValueError("Unknown block")
            if not db.execute("UPDATE documents SET last_block_id=?,last_offset=?,opened_at=CURRENT_TIMESTAMP WHERE id=?",
                              (position.block_id, position.offset, doc_id)).rowcount:
                raise ValueError("Unknown document")

    def settings(self) -> ReaderSettings:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM reader_settings WHERE singleton=1").fetchone()
            return ReaderSettings.model_validate_json(row[0]) if row else ReaderSettings()

    def save_settings(self, settings: ReaderSettings):
        with self.connect() as db:
            db.execute("INSERT INTO reader_settings VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET payload=excluded.payload",
                       (settings.model_dump_json(),))

    def library(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("""SELECT d.id,d.title,d.status,d.page_count,d.created_at,d.opened_at,
                (SELECT COUNT(*) FROM blocks b WHERE b.doc_id=d.id) AS block_count
                FROM documents d ORDER BY COALESCE(opened_at,created_at) DESC,id""")]

    def delete_document(self, doc_id: str) -> bool:
        """Blocks, sources, glossary, job, usage, conversations, notes and
        presentation notes all cascade from the document row; files go after."""
        with self.connect() as db:
            if not db.execute("DELETE FROM documents WHERE id=?", (doc_id,)).rowcount:
                return False
            if doc_id == 'rex-omni':
                db.execute("INSERT INTO app_settings VALUES('sample_removed','true') ON CONFLICT(key) DO UPDATE SET payload=excluded.payload")
        directory = self.data_dir / 'documents' / doc_id
        if directory.parent == self.data_dir / 'documents' and doc_id not in {'', '.', '..'}:
            # A PDF page still open elsewhere may hold a file; leftovers are harmless.
            shutil.rmtree(directory, ignore_errors=True)
        return True

    def summary(self, doc_id: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM paper_summaries WHERE doc_id=?", (doc_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["payload"] = json.loads(row["payload"]) if row["payload"] else None
            result["edited"] = bool(row["edited"])
            return result

    def save_summary(self, doc_id: str, **fields):
        """Create or update a paper's summary row; `payload` is stored as JSON."""
        if "payload" in fields and fields["payload"] is not None:
            fields["payload"] = json.dumps(fields["payload"], ensure_ascii=False)
        if "edited" in fields:
            fields["edited"] = int(bool(fields["edited"]))
        names = list(fields)
        with self.connect() as db:
            db.execute("INSERT INTO paper_summaries(doc_id,status) VALUES(?, 'running') ON CONFLICT(doc_id) DO NOTHING", (doc_id,))
            if names:
                db.execute(f"UPDATE paper_summaries SET {','.join(n + '=?' for n in names)},updated_at=CURRENT_TIMESTAMP WHERE doc_id=?",
                           [fields[n] for n in names] + [doc_id])

    def ai_result(self, doc_id: str, key: str):
        with self.connect() as db:
            row = db.execute("SELECT value FROM ai_results WHERE doc_id=? AND key=?", (doc_id, key)).fetchone()
            return json.loads(row[0]) if row else None

    def save_ai_result(self, doc_id: str, key: str, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO ai_results VALUES(?,?,?)", (doc_id, key, json.dumps(value, ensure_ascii=False)))

    def clear_ai_results(self, doc_id: str):
        with self.connect() as db:
            db.execute("DELETE FROM ai_results WHERE doc_id=?", (doc_id,))

    def job(self, doc_id: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE doc_id=? ORDER BY rowid DESC LIMIT 1", (doc_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["checkpoint"] = json.loads(row["checkpoint"] or "{}")
            result["usage"] = [dict(r) for r in db.execute("SELECT stage,model,SUM(tokens_in) AS tokens_in,SUM(tokens_out) AS tokens_out,SUM(cache_read) AS cache_read,SUM(cache_write) AS cache_write,SUM(requests) AS requests,COUNT(*) AS calls,batch FROM ai_usage WHERE doc_id=? GROUP BY stage,model,batch", (doc_id,))]
            return result

    def update_job(self, doc_id: str, stage: str, status: str, progress: float, **checkpoint):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT checkpoint,status FROM jobs WHERE doc_id=?", (doc_id,)).fetchone()
            saved = json.loads(row[0] or "{}") if row else {}
            if row and row[1] == 'paused' and status != 'queued':
                status = 'paused'
            saved.update(checkpoint)
            db.execute("UPDATE jobs SET stage=?,status=?,progress=?,checkpoint=?,updated_at=CURRENT_TIMESTAMP WHERE doc_id=?",
                       (stage, status, progress, json.dumps(saved, ensure_ascii=False), doc_id))
            db.execute("UPDATE documents SET status=? WHERE id=?", (status, doc_id))

    def save_blocks(self, doc_id: str, blocks: list[Block], sources: list[dict] | None = None):
        with self.connect() as db:
            for b in blocks:
                db.execute("""INSERT INTO blocks(id,doc_id,sort_order,type,section_path,payload) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(doc_id,id) DO UPDATE SET payload=excluded.payload""",
                    (b.id, doc_id, b.order, b.type, json.dumps(b.section_path), b.model_dump_json(exclude_none=True)))
            if sources is not None:
                db.executemany("INSERT OR IGNORE INTO source_blocks VALUES(?,?,?,?)",
                    [(doc_id, b.id, b.model_dump_json(exclude_none=True), json.dumps(s, ensure_ascii=False)) for b, s in zip(blocks, sources)])

    def sources(self, doc_id: str):
        with self.connect() as db:
            return {r["block_id"]: {"block": Block.model_validate_json(r["payload"]), **json.loads(r["provenance"])}
                    for r in db.execute("SELECT * FROM source_blocks WHERE doc_id=?", (doc_id,))}

    def replace_extraction(self,doc_id,blocks,sources):
        with self.connect() as db:
            db.execute('DELETE FROM source_blocks WHERE doc_id=?',(doc_id,))
            db.execute('DELETE FROM blocks WHERE doc_id=?',(doc_id,))
            for b,s in zip(blocks,sources):
                db.execute('INSERT INTO blocks VALUES(?,?,?,?,?,?)',(b.id,doc_id,b.order,b.type,json.dumps(b.section_path),b.model_dump_json(exclude_none=True)))
                db.execute('INSERT INTO source_blocks VALUES(?,?,?,?)',(doc_id,b.id,b.model_dump_json(exclude_none=True),json.dumps(s,ensure_ascii=False)))
            db.execute('UPDATE documents SET last_block_id=NULL,last_offset=0 WHERE id=?',(doc_id,))

    def pipeline_options(self):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM pipeline_settings WHERE singleton=1").fetchone()
            return PipelineOptions.model_validate_json(row[0]) if row else PipelineOptions()

    def annotated_sections(self, doc_id):
        with self.connect() as db:
            return {r[0] for r in db.execute('SELECT section_key FROM annotation_sections WHERE doc_id=?',(doc_id,))}

    def note_anchor(self, doc_id, note_id):
        with self.connect() as db:
            row=db.execute('SELECT after_block_id FROM note_anchors WHERE doc_id=? AND note_id=?',(doc_id,note_id)).fetchone()
            return row[0] if row else None

    def save_repaired_note(self, doc_id, block, anchor):
        with self.connect() as db:
            key=db.execute('SELECT section_key FROM note_anchors WHERE doc_id=? AND note_id=?',(doc_id,block.id)).fetchone()[0]
            rows=db.execute('''SELECT a.after_block_id,b.payload FROM note_anchors a JOIN blocks b ON a.doc_id=b.doc_id AND a.note_id=b.id
                WHERE a.doc_id=? AND a.section_key=?''',(doc_id,key)).fetchall()
        notes=[]
        for row in rows:
            saved=Block.model_validate_json(row['payload'])
            notes.append((anchor,block) if saved.id==block.id else (row['after_block_id'],saved))
        self.save_annotations(doc_id,key,notes,[],preserve_user=False)

    def save_annotations(self, doc_id, section_key, notes, anchors, preserve_user=True):
        """Commit a section and its completion marker together; retain source IDs.

        Sort positions can change, but source_blocks and reading anchors do not.
        A crash never leaves half a section or duplicates on the next run.
        """
        with self.connect() as db:
            existing={r[0] for r in db.execute('SELECT note_id FROM note_anchors WHERE doc_id=? AND section_key=?',(doc_id,section_key))}
            if preserve_user:
                protected={}
                for row in db.execute('''SELECT a.after_block_id,b.payload FROM note_anchors a JOIN blocks b ON a.doc_id=b.doc_id AND a.note_id=b.id
                    WHERE a.doc_id=? AND a.section_key=?''',(doc_id,section_key)):
                    saved=Block.model_validate_json(row['payload'])
                    if saved.note.origin=='user':protected[saved.id]=(row['after_block_id'],saved)
                notes=[pair for pair in notes if pair[1].id not in protected]+list(protected.values())
            for obsolete in existing-{b.id for _,b in notes}:
                db.execute('DELETE FROM blocks WHERE doc_id=? AND id=?',(doc_id,obsolete))
            next_order=db.execute('SELECT COALESCE(MAX(sort_order),0)+1 FROM blocks WHERE doc_id=?',(doc_id,)).fetchone()[0]
            for anchor,b in notes:
                b.order=next_order; next_order+=1
                db.execute('''INSERT INTO blocks VALUES(?,?,?,?,?,?) ON CONFLICT(doc_id,id) DO UPDATE SET payload=excluded.payload''',
                    (b.id,doc_id,b.order,b.type,json.dumps(b.section_path),b.model_dump_json(exclude_none=True)))
                db.execute('INSERT OR REPLACE INTO note_anchors VALUES(?,?,?,?)',(doc_id,b.id,anchor,section_key))
            for b in anchors:
                db.execute('UPDATE blocks SET payload=? WHERE doc_id=? AND id=?',(b.model_dump_json(exclude_none=True),doc_id,b.id))
            all_blocks=[Block.model_validate_json(r[0]) for r in db.execute('SELECT payload FROM blocks WHERE doc_id=? ORDER BY sort_order',(doc_id,))]
            by_anchor={}
            note_ids=set()
            by_id={b.id:b for b in all_blocks}
            for r in db.execute('SELECT note_id,after_block_id FROM note_anchors WHERE doc_id=? ORDER BY note_id',(doc_id,)):
                by_anchor.setdefault(r['after_block_id'],[]).append(by_id[r['note_id']]);note_ids.add(r['note_id'])
            ordered=[];visited=set()
            def append_tree(block):
                if block.id in visited:return
                visited.add(block.id);ordered.append(block)
                for child in by_anchor.get(block.id,[]):append_tree(child)
            for b in all_blocks:
                if b.id not in note_ids:
                    append_tree(b)
            offset=db.execute('SELECT COALESCE(MAX(sort_order),0)+1 FROM blocks WHERE doc_id=?',(doc_id,)).fetchone()[0]
            db.execute('UPDATE blocks SET sort_order=sort_order+? WHERE doc_id=?',(offset,doc_id))
            for i,b in enumerate(ordered):
                b.order=i
                db.execute('UPDATE blocks SET sort_order=?,payload=? WHERE doc_id=? AND id=?',(i,b.model_dump_json(exclude_none=True),doc_id,b.id))
            db.execute('INSERT OR IGNORE INTO annotation_sections VALUES(?,?)',(doc_id,section_key))

    def save_pipeline_options(self, options: PipelineOptions):
        with self.connect() as db:
            db.execute("INSERT INTO pipeline_settings VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET payload=excluded.payload", (options.model_dump_json(),))

    def preference(self,key,default=None):
        with self.connect() as db:
            row=db.execute('SELECT payload FROM app_settings WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else default

    def save_preference(self,key,value):
        with self.connect() as db:
            db.execute('INSERT INTO app_settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload',(key,json.dumps(value,ensure_ascii=False)))

    def provider_config(self,provider):
        with self.connect() as db:
            row=db.execute('SELECT * FROM provider_settings WHERE provider=?',(provider,)).fetchone()
            return dict(row) if row else {'provider':provider,'mode':'api_key','model':None,'cli_path':None,'enabled':False}
