"""Single-consumer durable evidence for processed live ledger entries."""
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4


@contextmanager
def open_store(bridge, scope, *, write=False):
    if not bridge._state_file or bridge._verdict_mode != "strict":
        yield None
        return
    path = Path(bridge._state_file + ".verdict-receipts.sqlite3")
    expected = bridge._verdict_store_id
    if not path.exists():
        if expected is not None:
            raise OSError("verdict receipt database missing")
        if not write:
            yield None
            return
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    mode = "rw" if write else "ro"
    with closing(sqlite3.connect(path.absolute().as_uri() + "?mode=" + mode, uri=True)) as db:
        if write:
            db.execute("PRAGMA synchronous=EXTRA")
        if write and expected is None:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                           "scope TEXT NOT NULL, store_id TEXT NOT NULL)")
                db.execute("INSERT OR IGNORE INTO identity VALUES (1, ?, ?)", (scope, str(uuid4())))
                db.execute("CREATE TABLE IF NOT EXISTS receipts (entry_id TEXT PRIMARY KEY, receipt TEXT NOT NULL)")
        binding = db.execute("SELECT scope, store_id FROM identity WHERE singleton=1").fetchone()
        if not binding or binding[0] != scope or (expected is not None and binding[1] != expected):
            raise ValueError("verdict receipt database binding mismatch")
        db.execute("SELECT entry_id FROM receipts LIMIT 0")
        if write:
            bridge._verdict_store_id = binding[1]
        yield db


def lookup(db, entry_id):
    if db is None:
        return None
    row = db.execute("SELECT receipt FROM receipts WHERE entry_id=?", (entry_id,)).fetchone()
    return json.loads(row[0]) if row else None


def append(db, records):
    with db:
        db.executemany("INSERT INTO receipts (entry_id, receipt) VALUES (?, ?)",
                       [(key, json.dumps(value, allow_nan=False)) for key, value in records.items()])
