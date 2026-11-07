"""Local Storage metadata index, not the business database or capacity authority.

Normal listing uses a key index. Legacy discovery visits a bounded number of
filesystem entries per call; completed directories survive restarts. One live
scandir per instance resumes partial directories without rescanning every page.
"""

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

CURSOR = "local-index-v1:"
DISCOVERY_ENTRIES = 1000
DISCOVERY_SECONDS = 0.1
DISCOVERY_LEASE = 180


def wal(db, seconds=5):
    """Switch the index file to WAL once; the mode persists in the file.

    The switch needs an exclusive lock. When several first opens race, SQLite
    answers BUSY at once to avoid a deadlock instead of honouring busy_timeout,
    so only that switch is retried, within a deadline.
    """
    deadline = time.monotonic() + seconds
    while db.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
        try:
            if db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal":
                return
        except sqlite3.OperationalError as error:
            if "locked" not in str(error):
                raise
        if time.monotonic() >= deadline:
            raise sqlite3.OperationalError("local object index could not switch to WAL")
        time.sleep(0.01)


class LocalObjectIndex:
    def __init__(self, root):
        self.root = Path(root)
        metadata = self.root / ".metadata"
        metadata.mkdir(mode=0o700, exist_ok=True)
        self.path = metadata / "objects.sqlite3"
        self.owner = uuid4().hex
        self.guard = threading.RLock()
        self.directory = None
        self.entries = None
        self.anchor = None
        with self.connection() as db:
            wal(db)
            db.executescript("""
                CREATE TABLE IF NOT EXISTS objects (
                    key TEXT PRIMARY KEY COLLATE BINARY,
                    size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                    etag TEXT, content_type TEXT
                );
                CREATE TABLE IF NOT EXISTS pending (
                    token TEXT PRIMARY KEY, key TEXT NOT NULL, lease_until REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS pending_expiry ON pending (lease_until, token);
                CREATE INDEX IF NOT EXISTS pending_key ON pending (key);
                CREATE TABLE IF NOT EXISTS discovery (
                    path TEXT PRIMARY KEY, done INTEGER NOT NULL DEFAULT 0,
                    owner TEXT, lease_until REAL
                );
                CREATE INDEX IF NOT EXISTS discovery_pending ON discovery (done, lease_until, path);
                CREATE TABLE IF NOT EXISTS control (id INTEGER PRIMARY KEY, ready INTEGER NOT NULL DEFAULT 0);
                INSERT OR IGNORE INTO control (id) VALUES (1);
                INSERT OR IGNORE INTO discovery (path) VALUES ('');
            """)
        self.path.chmod(0o600)
        # One bounded idle connection keeps WAL from checkpointing/recreating on
        # every tiny object write. Per-operation connections remain thread-local.
        self.anchor = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        self.anchor.execute("SELECT ready FROM control WHERE id=1").fetchone()

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA synchronous=NORMAL")
            with db:
                yield db
        finally:
            db.close()

    def register(self, key):
        # Register before rename: a process crash between rename and metadata
        # publication must not make the object's key invisible to collection.
        token = uuid4().hex
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO objects (key,size,mtime_ns) VALUES (?,0,0)", (key,))
            db.execute("INSERT INTO pending VALUES (?,?,?)", (token, key, time.time() + 180))
        return token

    def end_write(self, key, token):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM pending WHERE token=? AND key=?", (token, key))
            if not (self.root / key).is_file():
                db.execute("DELETE FROM objects WHERE key=? AND NOT EXISTS (SELECT 1 FROM pending WHERE key=?)", (key, key))

    def maintenance(self, *, now=None, limit=100):
        # Normal writes clear immediately. A crashed writer's private .tmp has
        # its own durable lease, independent of the later business publication.
        now = time.time() if now is None else now
        with self.connection() as db:
            rows = db.execute(
                "SELECT token,key FROM pending WHERE lease_until<=? ORDER BY lease_until,token LIMIT ?", (now - 900, limit)
            ).fetchall()
        removed = 0
        for token, key in rows:
            (self.root / ".metadata" / (token + ".tmp")).unlink(missing_ok=True)
            self.end_write(key, token)
            removed += 1
        return removed

    def record(self, key, size, mtime_ns, etag, content_type):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                current = (self.root / key).stat()
            except FileNotFoundError:
                return
            if current.st_size != size or current.st_mtime_ns != mtime_ns:
                return  # a replacement won while this writer was publishing metadata
            db.execute(
                "INSERT INTO objects VALUES (?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                "size=excluded.size,mtime_ns=excluded.mtime_ns,etag=excluded.etag,content_type=excluded.content_type",
                (key, size, mtime_ns, etag, content_type),
            )

    def publish(self, key, temporary, target, stat, etag, content_type):
        # Serialize rename/catalog publication with deletion across processes.
        # The already committed pending key survives a crash before this commit.
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            os.replace(temporary, target)
            db.execute(
                "INSERT INTO objects VALUES (?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                "size=excluded.size,mtime_ns=excluded.mtime_ns,etag=excluded.etag,content_type=excluded.content_type",
                (key, stat.st_size, stat.st_mtime_ns, etag, content_type),
            )

    def delete(self, key, target, legacy_metadata):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            target.unlink(missing_ok=True)
            legacy_metadata.unlink(missing_ok=True)
            db.execute("DELETE FROM objects WHERE key=?", (key,))

    def get(self, key):
        with self.connection() as db:
            row = db.execute("SELECT size,mtime_ns,etag,content_type FROM objects WHERE key=?", (key,)).fetchone()
        return dict(zip(("size", "mtime", "etag", "content_type"), row, strict=True)) if row else {}

    def remove(self, key):
        with self.connection() as db:
            db.execute("DELETE FROM objects WHERE key=?", (key,))

    def _claim(self):
        now = time.time()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT ready FROM control WHERE id=1").fetchone()[0]:
                return False
            row = db.execute(
                "SELECT path FROM discovery WHERE done=0 AND (lease_until IS NULL OR lease_until<=?) ORDER BY path LIMIT 1", (now,)
            ).fetchone()
            if not row:
                if db.execute("SELECT 1 FROM discovery WHERE done=0 LIMIT 1").fetchone() is None:
                    db.execute("UPDATE control SET ready=1 WHERE id=1")
                return False
            self.directory = row[0]
            db.execute("UPDATE discovery SET owner=?,lease_until=? WHERE path=?", (self.owner, now + DISCOVERY_LEASE, self.directory))
        try:
            candidate = self.root / self.directory
            self.entries = (
                iter(()) if candidate.is_symlink() or not candidate.resolve().is_relative_to(self.root) else os.scandir(candidate)
            )
        except FileNotFoundError:
            self.entries = iter(())
        return True

    def _flush(self, objects, directories, done):
        with self.connection() as db:
            # Do not publish an old scanner's observations after lease takeover.
            row = db.execute("SELECT owner FROM discovery WHERE path=?", (self.directory,)).fetchone()
            if not row or row[0] != self.owner:
                return False
            db.executemany("INSERT OR IGNORE INTO objects (key,size,mtime_ns) VALUES (?,?,?)", objects)
            db.executemany("INSERT OR IGNORE INTO discovery (path) VALUES (?)", [(path,) for path in directories])
            db.execute(
                "UPDATE discovery SET done=?,lease_until=? WHERE path=? AND owner=?",
                (int(done), time.time() + DISCOVERY_LEASE, self.directory, self.owner),
            )
        return True

    def _close_entries(self):
        if self.entries is not None and hasattr(self.entries, "close"):
            self.entries.close()
        self.entries = self.directory = None

    def discover(self, limit=DISCOVERY_ENTRIES):
        with self.guard:
            with self.connection() as db:
                if db.execute("SELECT ready FROM control WHERE id=1").fetchone()[0]:
                    return True
            deadline = time.monotonic() + DISCOVERY_SECONDS
            examined = 0
            while examined < limit and time.monotonic() < deadline:
                if self.entries is None and not self._claim():
                    break
                objects, directories, done = [], [], False
                try:
                    while examined < limit and time.monotonic() < deadline:
                        try:
                            entry = next(self.entries)
                        except StopIteration:
                            done = True
                            break
                        examined += 1
                        if entry.is_symlink() or (not self.directory and entry.name == ".metadata"):
                            continue
                        relative = (Path(self.directory) / entry.name).as_posix()
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                directories.append(relative)
                            elif entry.is_file(follow_symlinks=False):
                                stat = entry.stat(follow_symlinks=False)
                                objects.append((relative, stat.st_size, stat.st_mtime_ns))
                        except FileNotFoundError:
                            continue
                    won = self._flush(objects, directories, done)
                except BaseException:
                    self.close()
                    raise
                if done or not won:
                    self._close_entries()
            with self.connection() as db:
                return bool(db.execute("SELECT ready FROM control WHERE id=1").fetchone()[0])

    def page(self, prefix, cursor, limit):
        after = cursor
        if cursor and cursor.startswith(CURSOR):
            try:
                body = json.loads(cursor[len(CURSOR) :])
                if set(body) - {"prefix", "after"} or body.get("prefix") != prefix:
                    raise ValueError
                after = body.get("after")
                if after is not None and (not isinstance(after, str) or len(after) > 1024):
                    raise ValueError
            except (ValueError, TypeError):
                raise ValueError("invalid local storage cursor") from None
        if not self.discover():
            return [], CURSOR + json.dumps({"prefix": prefix, "after": after})
        where, args = ["key>=?"], [prefix]
        # A true prefix range uses the primary-key index; LIKE would depend on
        # collation and can turn escaped user filenames into a table scan.
        for i in range(len(prefix) - 1, -1, -1):
            if ord(prefix[i]) < 0x10FFFF:
                where.append("key<?")
                args.append(prefix[:i] + chr(ord(prefix[i]) + 1))
                break
        if after:
            where.append("key>?")
            args.append(after)
        with self.connection() as db:
            rows = db.execute(
                "SELECT key FROM objects WHERE " + " AND ".join(where) + " ORDER BY key LIMIT ?", (*args, limit + 1)
            ).fetchall()
        keys = [row[0] for row in rows]
        continuation = CURSOR + json.dumps({"prefix": prefix, "after": keys[limit - 1]}) if len(keys) > limit else None
        return keys[:limit], continuation

    def close(self):
        with self.guard:
            directory = self.directory
            self._close_entries()
            if self.anchor is not None:
                self.anchor.close()
                self.anchor = None
            if directory is not None:
                try:
                    with self.connection() as db:
                        db.execute("UPDATE discovery SET owner=NULL,lease_until=NULL WHERE path=? AND owner=?", (directory, self.owner))
                except (OSError, sqlite3.Error):
                    pass  # a removed test root or failed disk is recovered by the durable lease

    def __del__(self):
        if hasattr(self, "guard"):
            self.close()
