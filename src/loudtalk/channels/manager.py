"""Durable, explicitly paired messenger-to-agent voice routing.

Receiving a webhook acknowledges persistence, never an agent's completion.
An interrupted operation is intentionally not replayed: agent actions and remote
message delivery cannot be rolled back or reliably inferred after a crash.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from ..dispatch import generate_reply
from ..store import Store, now
from .base import MAX_AUDIO_BYTES, ChannelError, Incoming


class ChannelManager:
    def __init__(self, data_dir: Path, store: Store, speech, adapter_factory):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data_dir.chmod(0o700)
        self.path = self.data_dir / "channels.sqlite3"
        self.store = store
        self.speech = speech
        self.adapter_factory = adapter_factory
        self.lock = threading.RLock()
        self._wake = asyncio.Event()
        self._worker = None
        self._active = None
        self._active_channel = None
        self._pollers = {}
        self._deliveries = {}
        self._adapters = {}
        self._states = {}
        self._started = False
        self._closing = False
        with self._connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS channels (
                    id TEXT PRIMARY KEY, config TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, channel_id TEXT NOT NULL,
                    event_id TEXT NOT NULL, payload TEXT NOT NULL,
                    status TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'received',
                    conversation_id TEXT, user_message_id INTEGER, transcript TEXT, reply TEXT,
                    error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(channel_id,event_id)
                );
                CREATE INDEX IF NOT EXISTS events_queue ON events(status,created_at);
                CREATE TABLE IF NOT EXISTS agent_deliveries (
                    message_id INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS pairings (
                    id TEXT PRIMARY KEY, channel_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL, sender_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, approved INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(channel_id,chat_id,sender_id)
                );
                CREATE TABLE IF NOT EXISTS routes (
                    channel_id TEXT NOT NULL, chat_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL, thread_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
                    PRIMARY KEY(channel_id,chat_id,sender_id,thread_id,agent_id)
                );
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(events)")}
            if "user_message_id" not in columns:
                db.execute("ALTER TABLE events ADD COLUMN user_message_id INTEGER")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS events_user_message ON events(user_message_id)"
            )
        self.path.chmod(0o600)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get_config(self, channel_id):
        with self._connect() as db:
            row = db.execute("SELECT config FROM channels WHERE id=?", (channel_id,)).fetchone()
        if row is None:
            raise KeyError("Nie ma takiego połączenia.")
        config = json.loads(row[0])
        if "_manual_policy" not in config:
            # Older versions mixed approved pairs into public display lists.
            # Never infer broad permissions from those already-paired members.
            with self._connect() as db:
                paired = db.execute(
                    "SELECT chat_id,sender_id FROM pairings WHERE channel_id=? AND approved=1",
                    (channel_id,),
                ).fetchall()
            config["_manual_policy"] = {
                "allowed_chats": [
                    item
                    for item in config["allowed_chats"]
                    if item not in {r["chat_id"] for r in paired}
                ],
                "allowed_senders": [
                    item
                    for item in config["allowed_senders"]
                    if item not in {r["sender_id"] for r in paired}
                ],
            }
        return config

    def _configs(self):
        with self._connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT config FROM channels")]

    @staticmethod
    def _redact(value, config):
        secrets = [str(v) for v in config.get("secrets", {}).values() if v]
        if isinstance(value, str):
            for secret in sorted(secrets, key=len, reverse=True):
                value = value.replace(secret, "[ukryte]")
            return value
        if isinstance(value, dict):
            return {k: ChannelManager._redact(v, config) for k, v in value.items()}
        if isinstance(value, list):
            return [ChannelManager._redact(v, config) for v in value]
        return value

    def _public(self, config):
        result = {
            key: value
            for key, value in config.items()
            if key != "secrets" and not key.startswith("_")
        }
        result["secret_fields_set"] = [k for k, v in config["secrets"].items() if v]
        result["status"] = self._states.get(
            config["id"],
            {"state": "configured" if config["enabled"] else "disabled", "error": None},
        )
        return self._redact(result, config)

    def list_channels(self):
        return [self._public(config) for config in self._configs()]

    def _validate(self, config):
        if config.get("platform") not in {"telegram", "discord", "slack", "whatsapp", "imessage"}:
            raise ValueError("Nieobsługiwany komunikator.")
        if not isinstance(config.get("name"), str) or not 1 <= len(config["name"].strip()) <= 100:
            raise ValueError("Podaj nazwę połączenia (maksymalnie 100 znaków).")
        agent = self.store.agent(config.get("agent_id"))
        if agent.get("kind") not in {"command", "openai", "webhook", "inbox"}:
            raise ValueError("Wybierz agenta z poleceniem, API, webhookiem lub połączeniem MCP.")
        for field in ("settings", "secrets"):
            if not isinstance(config.get(field), dict):
                raise ValueError("Nieprawidłowa konfiguracja połączenia.")
        if any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in config["secrets"].items()
        ):
            raise ValueError("Klucze połączenia muszą być tekstem.")
        for field in ("allowed_chats", "allowed_senders"):
            values = config.get(field)
            if (
                not isinstance(values, list)
                or len(values) > 500
                or any(
                    not isinstance(value, str) or not value or len(value) > 1024 for value in values
                )
            ):
                raise ValueError("Nieprawidłowa lista zatwierdzonych rozmówców.")
        if len(json.dumps(config)) > 100_000:
            raise ValueError("Konfiguracja połączenia jest zbyt duża.")

    def _save(self, config):
        with self.lock, self._connect() as db:
            db.execute(
                "INSERT INTO channels VALUES(?,?) ON CONFLICT(id) DO UPDATE SET config=excluded.config",
                (config["id"], json.dumps(config)),
            )

    def create(self, config):
        if config.get("enabled"):
            raise ValueError("Najpierw zapisz i sprawdź połączenie, potem je włącz.")
        result = {
            "id": uuid4().hex,
            "platform": config.get("platform"),
            "name": config.get("name", ""),
            "agent_id": config.get("agent_id"),
            "enabled": False,
            "settings": config.get("settings", {}),
            "secrets": config.get("secrets", {}),
            "allowed_senders": config.get("allowed_senders", []),
            "allowed_chats": config.get("allowed_chats", []),
        }
        self._validate(result)
        result["_manual_policy"] = {
            field: list(result[field]) for field in ("allowed_chats", "allowed_senders")
        }
        self._save(result)
        return self._public(result)

    def update(self, channel_id, updates):
        config = self.get_config(channel_id)
        if config["enabled"]:
            raise ValueError("Wyłącz połączenie przed zmianą konfiguracji.")
        if set(updates) - {
            "name",
            "agent_id",
            "settings",
            "secrets",
            "allowed_senders",
            "allowed_chats",
            "enabled",
        }:
            raise ValueError("Nieprawidłowa aktualizacja połączenia.")
        if updates.get("enabled"):
            raise ValueError("Włącz połączenie osobnym przyciskiem.")
        result = {**config, **updates}
        for key in ("settings", "secrets"):
            if key in updates:
                if not isinstance(updates[key], dict):
                    raise ValueError("Nieprawidłowa konfiguracja połączenia.")
                # Empty secret form inputs mean unchanged, not accidental deletion.
                values = {k: v for k, v in updates[key].items() if key != "secrets" or v}
                result[key] = {**config[key], **values}
        self._validate(result)
        for field in ("allowed_chats", "allowed_senders"):
            if field in updates:
                result["_manual_policy"][field] = list(updates[field])
        self._save(result)
        self._adapters.pop(channel_id, None)
        self._states.pop(channel_id, None)
        return self._public(result)

    def _adapter(self, channel_id):
        if channel_id not in self._adapters:
            self._adapters[channel_id] = self.adapter_factory(self.get_config(channel_id))
        return self._adapters[channel_id]

    def _safe_error(self, error, config, fallback):
        return self._redact(str(error), config) if isinstance(error, ChannelError) else fallback

    async def check(self, channel_id):
        config = self.get_config(channel_id)
        try:
            result = await self._adapter(channel_id).check_connection()
            if result.get("ok") is False:
                raise ChannelError("Komunikator nie potwierdził połączenia. Sprawdź konfigurację.")
            return self._redact(result, config)
        except Exception as error:
            message = self._safe_error(
                error, config, "Nie udało się sprawdzić połączenia. Sprawdź klucze i sieć."
            )
            self._states[channel_id] = {"state": "error", "error": message}
            raise ChannelError(message) from None

    async def enable(self, channel_id):
        config = self.get_config(channel_id)
        self._validate(config)
        await self.check(channel_id)
        config["enabled"] = True
        self._save(config)
        if self._started:
            self._launch(channel_id)
        else:
            self._states[channel_id] = {"state": "configured", "error": None}
        self._wake.set()
        return self._public(config)

    async def disable(self, channel_id):
        config = self.get_config(channel_id)
        config["enabled"] = False
        self._save(config)
        current = asyncio.current_task()
        tasks = [self._pollers.pop(channel_id, None)]
        if self._active_channel == channel_id:
            tasks.append(self._active)
        tasks = [task for task in tasks if task is not None and task is not current]
        for task in tasks:
            task.cancel()
        await self._cancel_deliveries(channel_id)
        await asyncio.gather(*tasks, return_exceptions=True)
        with self.lock, self._connect() as db:
            db.execute(
                "UPDATE events SET status='error',error=?,updated_at=? WHERE channel_id=? AND status IN ('queued','awaiting_agent')",
                (
                    "Połączenie wyłączono przed przetworzeniem głosówki. Wyślij nową po włączeniu.",
                    now(),
                    channel_id,
                ),
            )
        self._adapters.pop(channel_id, None)
        self._states[channel_id] = {"state": "disabled", "error": None}
        return self._public(config)

    def pairings(self):
        with self._connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT id,channel_id,chat_id,sender_id,created_at FROM pairings WHERE approved=0 ORDER BY created_at DESC LIMIT 100"
                )
            ]

    def approve(self, channel_id, pairing_id):
        config = self.get_config(channel_id)
        with self.lock, self._connect() as db:
            row = db.execute(
                "SELECT * FROM pairings WHERE id=? AND channel_id=?", (pairing_id, channel_id)
            ).fetchone()
            if row is None:
                raise KeyError("Nie ma takiej prośby o połączenie.")
            for field, item in (
                ("allowed_chats", row["chat_id"]),
                ("allowed_senders", row["sender_id"]),
            ):
                if item not in config[field]:
                    config[field].append(item)
            self._validate(config)
            db.execute("UPDATE channels SET config=? WHERE id=?", (json.dumps(config), channel_id))
            db.execute("UPDATE pairings SET approved=1 WHERE id=?", (pairing_id,))
        return self._public(config)

    def events(self):
        with self._connect() as db:
            rows = db.execute(
                "SELECT id,channel_id,event_id,status,stage,conversation_id,user_message_id,transcript,reply,error,created_at,updated_at "
                "FROM events WHERE status!='unpaired' ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        configs = {config["id"]: config for config in self._configs()}
        return [self._redact(dict(row), configs[row["channel_id"]]) for row in rows]

    def _reply_event(self, conversation_id, reply_to_message_id):
        with self._connect() as db:
            routed = db.execute(
                "SELECT 1 FROM routes WHERE conversation_id=?", (conversation_id,)
            ).fetchone()
            if not routed:
                return None
            if not isinstance(reply_to_message_id, int) or isinstance(reply_to_message_id, bool):
                raise ValueError("Podaj reply_to_message_id głosówki, na którą odpowiadasz.")
            row = db.execute(
                "SELECT * FROM events WHERE conversation_id=? AND user_message_id=?",
                (conversation_id, reply_to_message_id),
            ).fetchone()
        if row is None:
            raise ValueError("reply_to_message_id nie wskazuje głosówki w tej rozmowie.")
        return dict(row)

    def previous_agent_reply(self, conversation_id, reply_to_message_id):
        record = self._reply_event(conversation_id, reply_to_message_id)
        if record is None:
            return None
        with self._connect() as db:
            delivered = db.execute(
                "SELECT message_id FROM agent_deliveries WHERE event_id=?", (record["id"],)
            ).fetchone()
        if delivered:
            return {
                "message": self.store.message(delivered["message_id"]),
                "delivery": {
                    "event_id": record["id"],
                    "status": record["status"],
                    "error": record["error"],
                },
            }
        if record["status"] != "awaiting_agent":
            raise ValueError("Ta głosówka nie oczekuje na odpowiedź agenta.")
        return None

    async def deliver_agent_message(self, message_id, reply_to_message_id=None):
        """Deliver one completed MCP/CLI reply to its pending messenger recording.

        The claim and assistant-message association commit before any remote send.
        Repeating this call can observe the outcome but cannot resend the audio.
        """
        if self._closing:
            return None
        message = self.store.message(message_id)
        if (
            message["role"] != "assistant"
            or message["status"] != "ready"
            or not message["audio_url"]
        ):
            raise ValueError("Odpowiedź głosowa nie jest jeszcze gotowa.")
        record = self._reply_event(message["conversation_id"], reply_to_message_id)
        if record is None:
            return None
        with self.lock, self._connect() as db:
            previous = db.execute(
                "SELECT e.id,e.status,e.error FROM agent_deliveries d JOIN events e ON e.id=d.event_id WHERE d.message_id=? OR d.event_id=?",
                (message_id, record["id"]),
            ).fetchone()
            if previous:
                if previous["id"] != record["id"]:
                    raise ValueError("Ta odpowiedź została już przypisana do innej głosówki.")
                return {
                    "event_id": previous["id"],
                    "status": previous["status"],
                    "error": previous["error"],
                }
            if record["status"] != "awaiting_agent":
                raise ValueError("Ta głosówka nie oczekuje na odpowiedź agenta.")
            config = self.get_config(record["channel_id"])
            event = Incoming(**json.loads(record["payload"]))
            if not config["enabled"] or not self._allowed(config, event):
                return None
            db.execute("INSERT INTO agent_deliveries VALUES(?,?)", (message_id, record["id"]))
            db.execute(
                "UPDATE events SET status='processing',stage='sending',reply=?,updated_at=? WHERE id=?",
                (message["text"], now(), record["id"]),
            )
        # Register before yielding, so disabling cannot miss a claimed delivery.
        # A child task contains only delivery work, not the caller's HTTP request.
        task = asyncio.create_task(self._deliver_claimed(record, config, event, message))
        self._deliveries.setdefault(event.channel_id, {})[task] = record["id"]
        try:
            return await task
        finally:
            deliveries = self._deliveries.get(event.channel_id)
            if deliveries is not None:
                deliveries.pop(task, None)
                if not deliveries:
                    self._deliveries.pop(event.channel_id, None)

    async def _deliver_claimed(self, record, config, event, message):
        try:
            url = message["audio_url"]
            if (
                not isinstance(url, str)
                or not url.startswith("/audio/")
                or not url.endswith(".wav")
            ):
                raise ValueError("Nieprawidłowy plik odpowiedzi.")
            path = self.speech.resolve_audio(url.removeprefix("/audio/").removesuffix(".wav"))
            await self._adapter(event.channel_id).send_voice(
                event, path, message["text"], message["duration"] or 0
            )
            self._update_event(record["id"], status="sent", stage="sent", error=None)
            return {"event_id": record["id"], "status": "sent", "error": None}
        except asyncio.CancelledError:
            self._update_event(
                record["id"],
                status="uncertain",
                error="Przerwano wysyłanie odpowiedzi. Sprawdź czat przed ponowieniem.",
            )
            raise
        except Exception as error:
            detail = self._safe_error(
                error,
                config,
                "Nie potwierdzono wysłania odpowiedzi. Mogła dotrzeć; sprawdź czat przed ponowieniem.",
            )
            self._update_event(record["id"], status="uncertain", error=detail)
            return {"event_id": record["id"], "status": "uncertain", "error": detail}

    async def _cancel_deliveries(self, channel_id=None):
        groups = (
            [self._deliveries.get(channel_id, {})]
            if channel_id is not None
            else list(self._deliveries.values())
        )
        current = asyncio.current_task()
        pending = [
            (task, event_id)
            for group in groups
            for task, event_id in tuple(group.items())
            if task is not current
        ]
        for task, _ in pending:
            task.cancel()
        await asyncio.gather(*(task for task, _ in pending), return_exceptions=True)
        # A task cancelled before its first instruction never enters its handler.
        # Persist that outcome here too, without changing a completed send.
        with self.lock, self._connect() as db:
            db.executemany(
                "UPDATE events SET status='uncertain',error=?,updated_at=? WHERE id=? AND status='processing' AND stage='sending'",
                [
                    (
                        "Przerwano wysyłanie odpowiedzi. Sprawdź czat przed ponowieniem.",
                        now(),
                        event_id,
                    )
                    for _, event_id in pending
                ],
            )

    def _allowed(self, config, event):
        if (
            event.chat_id not in config["allowed_chats"]
            or event.sender_id not in config["allowed_senders"]
        ):
            return False
        policy = config["_manual_policy"]
        if (
            event.chat_id in policy["allowed_chats"]
            and event.sender_id in policy["allowed_senders"]
        ):
            return True
        with self._connect() as db:
            return (
                db.execute(
                    "SELECT 1 FROM pairings WHERE channel_id=? AND chat_id=? AND sender_id=? AND approved=1",
                    (event.channel_id, event.chat_id, event.sender_id),
                ).fetchone()
                is not None
            )

    async def accept(self, event: Incoming):
        config = self.get_config(event.channel_id)
        if not config["enabled"]:
            return True
        for value in (event.event_id, event.chat_id, event.sender_id):
            if not isinstance(value, str) or not value or len(value) > 1024:
                raise ChannelError("Komunikator przesłał nieprawidłowy identyfikator wiadomości.")
        if event.thread_id is not None and (
            not isinstance(event.thread_id, str) or len(event.thread_id) > 1024
        ):
            raise ChannelError("Komunikator przesłał nieprawidłowy identyfikator wątku.")
        allowed = self._allowed(config, event)
        # Unpaired metadata cannot contain a download URL, file token or audio body.
        payload = json.dumps(event.to_dict()) if allowed else "{}"
        if len(payload) > 100_000:
            raise ChannelError("Metadane głosówki są zbyt duże.")
        with self.lock, self._connect() as db:
            if db.execute(
                "SELECT 1 FROM events WHERE channel_id=? AND event_id=?",
                (event.channel_id, event.event_id),
            ).fetchone():
                return True
            if (
                allowed
                and db.execute("SELECT count(*) FROM events WHERE status='queued'").fetchone()[0]
                >= 500
            ):
                return False  # Pollers must retain their cursor; webhooks should request retry.
            timestamp = now()
            db.execute(
                "INSERT INTO events(id,channel_id,event_id,payload,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (
                    uuid4().hex,
                    event.channel_id,
                    event.event_id,
                    payload,
                    "queued" if allowed else "unpaired",
                    timestamp,
                    timestamp,
                ),
            )
            if not allowed:
                db.execute(
                    "INSERT OR IGNORE INTO pairings(id,channel_id,chat_id,sender_id,created_at) VALUES(?,?,?,?,?)",
                    (uuid4().hex, event.channel_id, event.chat_id, event.sender_id, timestamp),
                )
        if allowed:
            self._wake.set()
        return True

    def _launch(self, channel_id):
        previous = self._pollers.get(channel_id)
        if previous is not None and not previous.done():
            return
        adapter = self._adapter(channel_id)
        self._states[channel_id] = {"state": "running", "error": None}
        if callable(getattr(adapter, "run", None)):
            self._pollers[channel_id] = asyncio.create_task(self._poll(channel_id, adapter))

    async def _poll(self, channel_id, adapter):
        try:
            await adapter.run(self.accept)
            if self.get_config(channel_id)["enabled"] and self._started:
                self._states[channel_id] = {
                    "state": "error",
                    "error": "Odbieranie głosówek zostało zatrzymane. Włącz połączenie ponownie.",
                }
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._states[channel_id] = {
                "state": "error",
                "error": self._safe_error(
                    error,
                    self.get_config(channel_id),
                    "Nie udało się odbierać głosówek. Sprawdź połączenie i włącz je ponownie.",
                ),
            }

    async def start(self):
        if self._started:
            return
        self._closing = False
        self._started = True
        with self.lock, self._connect() as db:
            db.execute(
                "UPDATE events SET status='uncertain',error=?,updated_at=? WHERE status='processing'",
                (
                    "Przetwarzanie przerwano. Agent mógł wykonać polecenie lub wysłać odpowiedź; sprawdź czat przed ponowieniem.",
                    now(),
                ),
            )
        self._worker = asyncio.create_task(self._work())
        for config in self._configs():
            if config["enabled"]:
                try:
                    self._launch(config["id"])
                except Exception as error:
                    self._states[config["id"]] = {
                        "state": "error",
                        "error": self._safe_error(
                            error,
                            config,
                            "Nie można uruchomić połączenia. Sprawdź jego konfigurację.",
                        ),
                    }
        self._wake.set()

    async def close(self):
        self._closing = True
        self._started = False
        tasks = [task for task in [*self._pollers.values(), self._worker, self._active] if task]
        for task in tasks:
            task.cancel()
        await self._cancel_deliveries()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._pollers.clear()
        self._worker = None
        self._active = None
        self._active_channel = None
        self._adapters.clear()
        self._states.clear()

    def _update_event(self, event_id, **fields):
        if not set(fields) <= {
            "status",
            "stage",
            "conversation_id",
            "user_message_id",
            "transcript",
            "reply",
            "error",
        }:
            raise ValueError("Nieprawidłowa aktualizacja głosówki.")
        fields["updated_at"] = now()
        with self.lock, self._connect() as db:
            db.execute(
                f"UPDATE events SET {','.join(key + '=?' for key in fields)} WHERE id=?",
                (*fields.values(), event_id),
            )

    async def _work(self):
        while True:
            self._wake.clear()
            with self._connect() as db:
                row = db.execute(
                    "SELECT * FROM events WHERE status='queued' ORDER BY created_at LIMIT 1"
                ).fetchone()
            if row is None:
                await self._wake.wait()
                continue
            self._active_channel = row["channel_id"]
            self._active = asyncio.create_task(self._process(dict(row)))
            try:
                await self._active
            except asyncio.CancelledError:
                if not self._started:
                    raise
            finally:
                self._active = None
                self._active_channel = None

    def _conversation(self, config, event):
        key = (
            event.channel_id,
            event.chat_id,
            event.sender_id,
            event.thread_id or "",
            config["agent_id"],
        )
        with self.lock, self._connect() as db:
            row = db.execute(
                "SELECT conversation_id FROM routes WHERE channel_id=? AND chat_id=? AND sender_id=? AND thread_id=? AND agent_id=?",
                key,
            ).fetchone()
            if row:
                try:
                    return self.store.conversation(row[0])["id"]
                except KeyError:
                    db.execute("DELETE FROM routes WHERE conversation_id=?", (row[0],))
            conversation = self.store.create_conversation(
                config["agent_id"], f"{config['name']} · {event.chat_id}"[:100]
            )
            db.execute("INSERT INTO routes VALUES(?,?,?,?,?,?)", (*key, conversation["id"]))
            return conversation["id"]

    async def _process(self, record):
        config = self.get_config(record["channel_id"])
        event = Incoming(**json.loads(record["payload"]))
        if not config["enabled"] or not self._allowed(config, event):
            self._update_event(
                record["id"],
                status="error",
                error="Połączenie lub dostęp rozmówcy zostały wyłączone.",
            )
            return
        self._update_event(record["id"], status="processing", stage="transcription")
        stage = "transcription"
        temporary = None
        reply_message = None
        try:
            adapter = self._adapter(event.channel_id)
            blob, filename = await adapter.download_audio(event)
            if not isinstance(blob, bytes) or not blob or len(blob) > MAX_AUDIO_BYTES:
                raise ChannelError("Głosówka jest pusta lub przekracza 25 MB.")
            suffix = Path(str(filename)).suffix.lower()
            if not suffix or len(suffix) > 10 or not suffix[1:].isalnum():
                suffix = ".audio"
            with tempfile.NamedTemporaryFile(
                prefix="incoming-", suffix=suffix, dir=self.data_dir, delete=False
            ) as audio:
                temporary = Path(audio.name)
                audio.write(blob)
            transcript = await asyncio.to_thread(self.speech.transcribe, temporary)
            text = transcript["text"].strip()
            if not text:
                raise ChannelError("W nagraniu nie rozpoznano mowy. Wyślij nową głosówkę.")
            conversation_id = self._conversation(config, event)
            user_message = self.store.add_message(
                conversation_id,
                "user",
                text,
                transcript.get("audio_url"),
                transcript.get("duration"),
            )
            self._update_event(
                record["id"],
                conversation_id=conversation_id,
                user_message_id=user_message["id"],
                transcript=text,
                stage="agent",
            )
            stage = "agent"
            agent = self.store.agent(config["agent_id"])
            if agent["kind"] == "inbox":
                self._update_event(record["id"], status="awaiting_agent", stage="agent")
                return
            history = [
                {"role": message["role"], "content": message["text"]}
                for message in self.store.messages(conversation_id)
                if message["status"] == "ready"
            ]
            reply = await generate_reply(agent, history, conversation_id)
            if not isinstance(reply, str) or not reply.strip():
                raise ChannelError("Agent nie zwrócił tekstu odpowiedzi.")
            reply_message = self.store.add_message(
                conversation_id, "assistant", reply.strip(), status="pending"
            )
            self._update_event(record["id"], reply=reply.strip(), stage="synthesis")
            stage = "synthesis"
            audio = await asyncio.to_thread(
                self.speech.synthesize, reply.strip(), agent.get("voice", "sophie")
            )
            self.store.update_message(
                reply_message["id"],
                status="ready",
                audio_url=audio["audio_url"],
                duration=audio.get("duration"),
            )
            self._update_event(record["id"], stage="sending")
            stage = "sending"
            await adapter.send_voice(
                event,
                self.speech.resolve_audio(audio["audio_id"]),
                reply.strip(),
                audio.get("duration", 0),
            )
            self._update_event(record["id"], status="sent", stage="sent", error=None)
        except asyncio.CancelledError:
            self._update_event(
                record["id"],
                status="uncertain",
                error="Przetwarzanie przerwano. Sprawdź czat i działanie agenta przed ponowieniem.",
            )
            if reply_message and stage == "synthesis":
                self.store.update_message(
                    reply_message["id"], status="error", error="Przerwano przygotowanie głosu."
                )
            raise
        except Exception as error:
            messages = {
                "transcription": "Nie udało się odczytać głosówki. Sprawdź silnik rozpoznawania mowy i połączenie.",
                "agent": "Agent nie zakończył odpowiedzi. Mógł wykonać część polecenia; sprawdź go przed ponowieniem.",
                "synthesis": "Odpowiedź agenta została zapisana, ale nie udało się przygotować głosu.",
                "sending": "Nie potwierdzono wysłania odpowiedzi. Mogła dotrzeć; sprawdź czat przed ponowieniem.",
            }
            message = self._safe_error(error, config, messages[stage])
            self._update_event(
                record["id"],
                status="uncertain" if stage in {"agent", "sending"} else "error",
                error=message,
            )
            if reply_message and stage == "synthesis":
                self.store.update_message(reply_message["id"], status="error", error=message)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
