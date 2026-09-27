"""SQLite persistence. No transcripts, audio or keys belong in the source repository."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        data_dir.chmod(0o700)
        self.path = data_dir / "loudtalk.sqlite3"
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY, config TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agents(id),
                    title TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL, text TEXT NOT NULL, audio_url TEXT,
                    duration REAL, created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ready', error TEXT
                );
                CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id,id);
            """)
        self.path.chmod(0o600)
        if not self.agents():
            self.save_agent(
                {
                    "id": "hermes",
                    "name": "Hermes",
                    "kind": "inbox",
                    "voice": "sophie",
                    "color": "#eb7256",
                    "endpoint": "",
                    "model": "",
                    "api_key": "",
                    "command_id": "",
                    "working_directory": str(Path.home()),
                }
            )

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

    def agents(self):
        with self.connect() as db:
            return [
                json.loads(r[0]) for r in db.execute("SELECT config FROM agents ORDER BY rowid")
            ]

    def agent(self, agent_id):
        with self.connect() as db:
            row = db.execute("SELECT config FROM agents WHERE id=?", (agent_id,)).fetchone()
        if not row:
            raise KeyError("Nie ma takiego agenta.")
        return json.loads(row[0])

    def save_agent(self, config):
        config = dict(config)
        config.setdefault("id", uuid.uuid4().hex)
        with self.lock, self.connect() as db:
            db.execute(
                "INSERT INTO agents(id, config) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET config=excluded.config",
                (config["id"], json.dumps(config)),
            )
        return config

    @staticmethod
    def public_agent(agent):
        return {
            **{k: v for k, v in agent.items() if k != "api_key"},
            "has_api_key": bool(agent.get("api_key")),
        }

    def conversations(self):
        with self.connect() as db:
            return [
                dict(r) for r in db.execute("SELECT * FROM conversations ORDER BY created_at DESC")
            ]

    def conversation(self, conversation_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
        if not row:
            raise KeyError("Nie ma takiej rozmowy.")
        return dict(row)

    def create_conversation(self, agent_id, title="Nowa rozmowa"):
        self.agent(agent_id)
        item = {"id": uuid.uuid4().hex, "agent_id": agent_id, "title": title, "created_at": now()}
        with self.lock, self.connect() as db:
            db.execute("INSERT INTO conversations VALUES(:id,:agent_id,:title,:created_at)", item)
        return item

    def delete_conversation(self, conversation_id):
        self.conversation(conversation_id)
        with self.lock, self.connect() as db:
            db.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))

    def messages(self, conversation_id):
        self.conversation(conversation_id)
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,)
                )
            ]

    def message(self, message_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if not row:
            raise KeyError("Nie ma takiej wiadomości.")
        return dict(row)

    def add_message(
        self, conversation_id, role, text, audio_url=None, duration=None, status="ready"
    ):
        self.conversation(conversation_id)
        with self.lock, self.connect() as db:
            cur = db.execute(
                "INSERT INTO messages(conversation_id,role,text,audio_url,duration,created_at,status) VALUES(?,?,?,?,?,?,?)",
                (conversation_id, role, text, audio_url, duration, now(), status),
            )
            if role == "user" and text:
                db.execute(
                    "UPDATE conversations SET title=? WHERE id=? AND title='Nowa rozmowa'",
                    (text[:60], conversation_id),
                )
            message_id = cur.lastrowid
        return self.message(message_id)

    def update_message(self, message_id, **fields):
        if not fields or not set(fields) <= {"text", "audio_url", "duration", "status", "error"}:
            raise ValueError("Nieprawidłowa aktualizacja wiadomości.")
        with self.lock, self.connect() as db:
            db.execute(
                f"UPDATE messages SET {','.join(k + '=?' for k in fields)} WHERE id=?",
                (*fields.values(), message_id),
            )
        return self.message(message_id)

    def recover_interrupted(self):
        with self.lock, self.connect() as db:
            db.execute(
                "UPDATE messages SET status='error',error='Aplikacja została zamknięta. Spróbuj ponownie.' WHERE status='pending'"
            )

    def inbox(self, agent_id, after_id=0):
        self.agent(agent_id)
        with self.connect() as db:
            rows = db.execute(
                """SELECT m.* FROM messages m JOIN conversations c ON c.id=m.conversation_id
                WHERE c.agent_id=? AND m.role='user' AND m.id>? ORDER BY m.id LIMIT 100""",
                (agent_id, after_id),
            )
            return [dict(r) for r in rows]
