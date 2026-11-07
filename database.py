"""SQLite transactions keep imports, provenance and partial session results durable."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from models import from_dict, now_iso, canonical_url

class Database:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS imports (
                id TEXT PRIMARY KEY, canonical TEXT, text_key TEXT, payload TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS imports_text ON imports(text_key);
            CREATE TABLE IF NOT EXISTS import_urls (canonical TEXT PRIMARY KEY, item_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, status TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT,
                deadline_seconds REAL NOT NULL, cancel INTEGER NOT NULL DEFAULT 0, summary TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS results (
                session_id TEXT NOT NULL, item_id TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(session_id, item_id));
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, session_id TEXT, created_at TEXT NOT NULL, message TEXT NOT NULL);
            ''')
            columns = {r[1] for r in db.execute("PRAGMA table_info(sessions)")}
            if "pause" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN pause INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=8)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback(); raise
        finally: db.close()

    def add_import(self, item):
        incoming = item.to_dict()
        receipt = {"source": item.source, "url": item.url, "imported_at": now_iso()}
        incoming["provenance"] = incoming.get("provenance", []) or [receipt]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            canonical = canonical_url(item.url)
            old = db.execute("SELECT * FROM imports WHERE id=? OR (canonical<>'' AND canonical=?) OR id IN (SELECT item_id FROM import_urls WHERE canonical=? AND canonical<>'') LIMIT 1",
                (item.key, canonical, canonical)).fetchone()
            if not old and item.text_key:
                from models import normalized
                for possible in db.execute("SELECT * FROM imports WHERE text_key=?", (item.text_key,)):
                    previous = json.loads(possible["payload"])
                    if previous.get("company") and item.company and normalized(previous["company"]) != normalized(item.company): continue
                    if previous.get("title") and item.title and normalized(previous["title"]) != normalized(item.title): continue
                    old = possible; break
            if old:
                previous = json.loads(old["payload"])
                # Same URL can be enriched; URL-only reimports must not erase the text.
                merged = dict(previous)
                for key, value in incoming.items():
                    if value and key not in ("provenance", "metadata"):
                        if key in ("url", "source") and previous.get("url"): continue
                        merged[key] = value
                merged["metadata"] = {**previous.get("metadata", {}), **incoming.get("metadata", {})}
                receipts = previous.get("provenance", [])
                if not any(p.get("source") == receipt["source"] and p.get("url") == receipt["url"] for p in receipts): receipts.append(receipt)
                merged["provenance"] = receipts
                obj = from_dict(merged)
                db.execute("UPDATE imports SET canonical=?,text_key=?,payload=?,updated_at=? WHERE id=?",
                    (canonical_url(obj.url), obj.text_key, json.dumps(merged), now_iso(), old["id"]))
                if canonical: db.execute("INSERT OR REPLACE INTO import_urls VALUES (?,?)", (canonical, old["id"]))
                return old["id"], False
            db.execute("INSERT INTO imports VALUES (?,?,?,?,?)", (item.key, canonical_url(item.url), item.text_key, json.dumps(incoming), now_iso()))
            if canonical: db.execute("INSERT OR REPLACE INTO import_urls VALUES (?,?)", (canonical, item.key))
            return item.key, True

    def imports(self):
        with self.connect() as db:
            return [(r["id"], from_dict(json.loads(r["payload"]))) for r in db.execute("SELECT * FROM imports ORDER BY updated_at DESC")]

    def create_session(self, seconds):
        identifier = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM sessions WHERE status IN ('running','paused','exporting')").fetchone():
                raise ValueError("A session is already running. Wait or cancel it before starting another.")
            db.execute("INSERT INTO sessions(id,status,started_at,deadline_seconds) VALUES (?,?,?,?)", (identifier, "running", now_iso(), seconds))
        return identifier

    def get_session(self, identifier=None):
        with self.connect() as db:
            r = db.execute("SELECT * FROM sessions WHERE id=?", (identifier,)).fetchone() if identifier else db.execute("SELECT * FROM sessions ORDER BY started_at DESC LIMIT 1").fetchone()
            if not r: return None
            d = dict(r); d["summary"] = json.loads(d["summary"]); return d

    def update_session(self, identifier, status, summary=None):
        with self.connect() as db:
            db.execute("UPDATE sessions SET status=?,summary=?,ended_at=? WHERE id=?", (status, json.dumps(summary or {}),
                now_iso() if status not in ("running", "paused", "exporting") else None, identifier))

    def request_pause(self, identifier, paused=True):
        with self.connect() as db:
            db.execute("UPDATE sessions SET pause=? WHERE id=? AND status IN ('running','paused')", (int(paused), identifier))

    def set_activity(self, identifier, status):
        if status not in ("running", "paused"): raise ValueError("Invalid activity state")
        with self.connect() as db:
            db.execute("UPDATE sessions SET status=? WHERE id=? AND status IN ('running','paused')", (status, identifier))

    def cancel(self, identifier):
        with self.connect() as db: db.execute("UPDATE sessions SET cancel=1 WHERE id=?", (identifier,))

    def save_result(self, session_id, item_id, result):
        with self.connect() as db:
            previous = db.execute("SELECT payload FROM results WHERE session_id=? AND item_id=?", (session_id,item_id)).fetchone()
            result["found_at"] = json.loads(previous[0]).get("found_at", now_iso()) if previous else now_iso()
            db.execute("INSERT OR REPLACE INTO results VALUES (?,?,?)", (session_id, item_id, json.dumps(result)))

    def results(self, session_id):
        with self.connect() as db:
            return sorted([json.loads(r[0]) for r in db.execute("SELECT payload FROM results WHERE session_id=?", (session_id,))], key=lambda r:r.get("score",0), reverse=True)

    def event(self, session_id, message):
        with self.connect() as db: db.execute("INSERT INTO events(session_id,created_at,message) VALUES (?,?,?)", (session_id, now_iso(), message))

    def events(self, session_id):
        with self.connect() as db: return [dict(r) for r in db.execute("SELECT created_at,message FROM events WHERE session_id=? ORDER BY id", (session_id,))]
