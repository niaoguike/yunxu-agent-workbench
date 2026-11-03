import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def uid() -> str:
    return uuid.uuid4().hex


def now() -> float:
    return time.time()


class Store:
    """One durable single-process transaction boundary; never holds a lock across await."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,password TEXT NOT NULL,instructions TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL REFERENCES users(id),name TEXT NOT NULL,instructions TEXT NOT NULL DEFAULT '',memory_enabled INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),title TEXT NOT NULL,mode TEXT NOT NULL,engine TEXT NOT NULL,model TEXT, status TEXT NOT NULL DEFAULT 'idle',stage INTEGER NOT NULL DEFAULT 0,version INTEGER NOT NULL DEFAULT 1,session_id TEXT,member_id TEXT,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),role TEXT NOT NULL,content TEXT NOT NULL,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS pending_inputs(id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),content TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'queued',created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),status TEXT NOT NULL,version INTEGER NOT NULL,stage INTEGER NOT NULL,error TEXT,session_id TEXT,snapshot TEXT NOT NULL DEFAULT '{}',created_at REAL NOT NULL,finished_at REAL);
        CREATE UNIQUE INDEX IF NOT EXISTS task_active_run ON runs(task_id) WHERE status IN ('queued','running','stopping');
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT NOT NULL REFERENCES tasks(id),run_id TEXT,kind TEXT NOT NULL,data TEXT NOT NULL,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS gates(id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),stage INTEGER NOT NULL,version INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending',comment TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),run_id TEXT NOT NULL REFERENCES runs(id),native_id TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',details TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),category TEXT NOT NULL,content TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS extensions(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),name TEXT NOT NULL,kind TEXT NOT NULL,content TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,revision INTEGER NOT NULL DEFAULT 1,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS members(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),name TEXT NOT NULL,role TEXT NOT NULL,engine TEXT NOT NULL,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS baselines(task_id TEXT NOT NULL REFERENCES tasks(id),path TEXT NOT NULL,digest TEXT,PRIMARY KEY(task_id,path));
        CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),run_id TEXT NOT NULL REFERENCES runs(id),path TEXT NOT NULL,digest TEXT NOT NULL,size INTEGER NOT NULL,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS checks(id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),run_id TEXT NOT NULL REFERENCES runs(id),name TEXT NOT NULL,status TEXT NOT NULL,evidence TEXT NOT NULL,source TEXT NOT NULL,created_at REAL NOT NULL);
        """)
        columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(tasks)")
        }
        if "require_acceptance" not in columns:
            self.connection.execute(
                "ALTER TABLE tasks ADD COLUMN require_acceptance INTEGER NOT NULL DEFAULT 0"
            )
        project_columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(projects)")
        }
        if "deleted_at" not in project_columns:
            self.connection.execute("ALTER TABLE projects ADD COLUMN deleted_at REAL")
        self.connection.commit()

    @contextmanager
    def transaction(self):
        with self.lock:
            try:
                yield self.connection
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise

    def execute(self, sql, parameters=()):
        with self.transaction() as db:
            return db.execute(sql, parameters).rowcount

    def one(self, sql, parameters=()):
        with self.lock:
            row = self.connection.execute(sql, parameters).fetchone()
            return dict(row) if row else None

    def all(self, sql, parameters=()):
        with self.lock:
            return [
                dict(row) for row in self.connection.execute(sql, parameters).fetchall()
            ]

    def event(self, task_id, kind, data, run_id=None):
        self.execute(
            "INSERT INTO events(task_id,run_id,kind,data,created_at) VALUES(?,?,?,?,?)",
            (task_id, run_id, kind, json.dumps(data, ensure_ascii=False), now()),
        )
