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
        CREATE TABLE IF NOT EXISTS plugins(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),name TEXT NOT NULL,version TEXT NOT NULL,source TEXT NOT NULL,digest TEXT NOT NULL,manifest TEXT NOT NULL,created_at REAL NOT NULL,UNIQUE(project_id,name,version));
        CREATE TABLE IF NOT EXISTS plugin_extensions(plugin_id TEXT NOT NULL REFERENCES plugins(id),extension_id TEXT NOT NULL REFERENCES extensions(id) ON DELETE CASCADE,PRIMARY KEY(plugin_id,extension_id));
        CREATE TABLE IF NOT EXISTS routines(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),config TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0,next_due REAL NOT NULL,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS routine_runs(id TEXT PRIMARY KEY,routine_id TEXT NOT NULL REFERENCES routines(id),slot TEXT NOT NULL,task_id TEXT REFERENCES tasks(id),status TEXT NOT NULL,error TEXT NOT NULL,created_at REAL NOT NULL,UNIQUE(routine_id,slot));
        CREATE TABLE IF NOT EXISTS channel_connections(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),name TEXT NOT NULL,provider TEXT NOT NULL,chat_id TEXT NOT NULL,user_id TEXT NOT NULL,engine TEXT NOT NULL,permission_mode TEXT NOT NULL,receive INTEGER NOT NULL,send INTEGER NOT NULL,enabled INTEGER NOT NULL,cursor INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'disabled',error TEXT NOT NULL DEFAULT '',created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS channel_threads(connection_id TEXT NOT NULL REFERENCES channel_connections(id),thread_key TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(id),external_reply TEXT NOT NULL,PRIMARY KEY(connection_id,thread_key));
        CREATE TABLE IF NOT EXISTS channel_events(connection_id TEXT NOT NULL REFERENCES channel_connections(id),event_id TEXT NOT NULL,task_id TEXT REFERENCES tasks(id),status TEXT NOT NULL,error TEXT NOT NULL,content TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(connection_id,event_id));
        CREATE TABLE IF NOT EXISTS channel_outbox(id TEXT PRIMARY KEY,connection_id TEXT NOT NULL REFERENCES channel_connections(id),task_id TEXT NOT NULL REFERENCES tasks(id),delivery_key TEXT NOT NULL,status TEXT NOT NULL,error TEXT NOT NULL,external_id TEXT NOT NULL,created_at REAL NOT NULL,UNIQUE(connection_id,task_id,delivery_key));
        CREATE TABLE IF NOT EXISTS resume_positions(user_id TEXT PRIMARY KEY REFERENCES users(id),task_id TEXT NOT NULL REFERENCES tasks(id));
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL REFERENCES users(id),name TEXT NOT NULL,instructions TEXT NOT NULL DEFAULT '',memory_enabled INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),title TEXT NOT NULL,mode TEXT NOT NULL,engine TEXT NOT NULL,model TEXT, status TEXT NOT NULL DEFAULT 'idle',stage INTEGER NOT NULL DEFAULT 0,version INTEGER NOT NULL DEFAULT 1,session_id TEXT,member_id TEXT,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS charts(id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),source TEXT NOT NULL,data TEXT NOT NULL,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS chart_versions(chart_id TEXT NOT NULL REFERENCES charts(id),version INTEGER NOT NULL,config TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(chart_id,version));
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
        CREATE TABLE IF NOT EXISTS assignments(id TEXT PRIMARY KEY,scope TEXT NOT NULL,assignment_key TEXT NOT NULL,payload_hash TEXT NOT NULL,parent_task_id TEXT REFERENCES tasks(id),member_id TEXT NOT NULL REFERENCES members(id),task_id TEXT NOT NULL REFERENCES tasks(id),created_at REAL NOT NULL,UNIQUE(scope,assignment_key));
        CREATE TABLE IF NOT EXISTS member_reports(id TEXT PRIMARY KEY,task_id TEXT UNIQUE NOT NULL REFERENCES tasks(id),parent_task_id TEXT REFERENCES tasks(id),status TEXT NOT NULL,content TEXT NOT NULL,created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS memory_context_state(project_id TEXT NOT NULL REFERENCES projects(id),engine TEXT NOT NULL,session_id TEXT NOT NULL,epoch INTEGER NOT NULL DEFAULT 0,fired TEXT NOT NULL DEFAULT '[]',PRIMARY KEY(project_id,engine,session_id));
        """)
        columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(tasks)")
        }
        if "require_acceptance" not in columns:
            self.connection.execute(
                "ALTER TABLE tasks ADD COLUMN require_acceptance INTEGER NOT NULL DEFAULT 0"
            )
        if "permission_mode" not in columns:
            self.connection.execute(
                "ALTER TABLE tasks ADD COLUMN permission_mode TEXT NOT NULL DEFAULT 'auto'"
            )
        if "parent_task_id" not in columns:
            self.connection.execute(
                "ALTER TABLE tasks ADD COLUMN parent_task_id TEXT REFERENCES tasks(id)"
            )
        if "allow_collaboration" not in columns:
            self.connection.execute(
                "ALTER TABLE tasks ADD COLUMN allow_collaboration INTEGER NOT NULL DEFAULT 0"
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
