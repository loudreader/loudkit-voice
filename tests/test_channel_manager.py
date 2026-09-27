"""Routing and crash-safety tests; speech and messenger traffic are explicit doubles."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest

from loudtalk.channels import manager as routing
from loudtalk.channels.base import ChannelError, Incoming
from loudtalk.channels.manager import ChannelManager
from loudtalk.store import Store


class SpeechDouble:
    def __init__(self, path):
        self.path = path
        self.transcribed = []
        self.synthesized = []
        self.fail_synthesis = False

    def _audio(self):
        audio_id = uuid4().hex
        (self.path / f"{audio_id}.wav").write_bytes(b"synthesized fixture")
        return {"audio_id": audio_id, "audio_url": f"/audio/{audio_id}.wav", "duration": 2.5}

    def transcribe(self, path):
        self.transcribed.append(path.read_bytes())
        return {"text": "Zrób raport.", **self._audio()}

    def synthesize(self, text, voice):
        self.synthesized.append((text, voice))
        if self.fail_synthesis:
            raise RuntimeError("synthesis failure contains a private-key")
        return self._audio()

    def resolve_audio(self, audio_id):
        assert len(audio_id) == 32 and audio_id.isalnum()
        path = self.path / f"{audio_id}.wav"
        assert path.exists()
        return path


class AdapterDouble:
    def __init__(self, config):
        self.config = config
        self.downloads = []
        self.sends = []
        self.checks = 0
        self.fail_send = False
        self.fail_check = None
        self.download_gate = None
        self.send_gate = None

    async def check_connection(self):
        self.checks += 1
        if self.fail_check:
            raise self.fail_check
        return {"ok": True, "identity": "fixture bot"}

    async def download_audio(self, event):
        self.downloads.append(event)
        if self.download_gate:
            await self.download_gate.wait()
        return b"voice fixture", "../unsafe/voice.ogg"

    async def send_voice(self, event, path, text, duration):
        self.sends.append((event, path.read_bytes(), text, duration))
        if self.send_gate:
            await self.send_gate.wait()
        if self.fail_send:
            raise RuntimeError("HTTP timeout with private-key")
        return {"message_id": "fixture-outgoing"}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.save_agent({"id": "agent", "name": "My agent", "kind": "command", "voice": "gosia"})
    speech = SpeechDouble(tmp_path)
    adapters = {}
    calls = []

    def factory(config):
        adapter = AdapterDouble(config)
        adapters[config["id"]] = adapter
        return adapter

    async def reply(agent, messages, conversation_id):
        calls.append((agent, messages, conversation_id))
        return "Raport gotowy."

    monkeypatch.setattr(routing, "generate_reply", reply)
    manager = ChannelManager(tmp_path, store, speech, factory)
    return manager, adapters, speech, calls, store


def channel(manager, **updates):
    return manager.create(
        {
            "platform": "telegram",
            "name": "Telegram",
            "agent_id": "agent",
            "secrets": {"bot_token": "private-key"},
            "allowed_chats": ["chat"],
            "allowed_senders": ["sender"],
            **updates,
        }
    )


def incoming(config, event_id="one", **updates):
    return Incoming(
        **{
            "channel_id": config["id"],
            "event_id": event_id,
            "chat_id": "chat",
            "sender_id": "sender",
            "thread_id": "thread",
            "audio_ref": {"url": "https://example.invalid/private-key"},
            "filename": "voice.ogg",
            **updates,
        }
    )


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.005)


def test_unpaired_audio_never_downloaded_and_approval_requires_a_new_event(setup):
    manager, adapters, speech, calls, _ = setup

    async def run():
        config = channel(manager, allowed_chats=[], allowed_senders=[])
        await manager.start()
        try:
            await manager.enable(config["id"])
            first = incoming(config)
            assert await manager.accept(first)
            assert len(manager.pairings()) == 1
            assert manager.events() == []
            assert adapters[config["id"]].downloads == []
            with manager._connect() as db:
                assert db.execute("SELECT payload FROM events").fetchone()[0] == "{}"
            paired = manager.approve(config["id"], manager.pairings()[0]["id"])
            assert paired["allowed_chats"] == ["chat"]
            assert paired["allowed_senders"] == ["sender"]
            assert manager.pairings() == []
            assert await manager.accept(first)
            assert manager.events() == []
            assert await manager.accept(incoming(config, "new-recording"))
            await until(lambda: manager.events()[0]["status"] == "sent")
            assert len(calls) == len(speech.transcribed) == 1
        finally:
            await manager.close()

    asyncio.run(run())


def test_both_sender_and_chat_must_be_allowed(setup):
    manager, adapters, _, calls, _ = setup

    async def run():
        config = channel(manager)
        await manager.start()
        try:
            await manager.enable(config["id"])
            await manager.accept(incoming(config, "wrong-sender", sender_id="stranger"))
            await manager.accept(incoming(config, "wrong-chat", chat_id="other-group"))
            assert len(manager.pairings()) == 2
            assert manager.events() == []
            assert adapters[config["id"]].downloads == calls == []
        finally:
            await manager.close()

    asyncio.run(run())


def test_duplicate_events_are_durable_and_receive_ack_before_speech(setup):
    manager, adapters, speech, calls, store = setup

    async def run():
        config = channel(manager)
        await manager.start()
        try:
            await manager.enable(config["id"])
            adapter = adapters[config["id"]]
            adapter.download_gate = asyncio.Event()
            assert await asyncio.wait_for(manager.accept(incoming(config)), timeout=0.2)
            assert all(await asyncio.gather(*[manager.accept(incoming(config)) for _ in range(8)]))
            with manager._connect() as db:
                assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 1
            assert calls == speech.transcribed == []
            adapter.download_gate.set()
            await until(lambda: manager.events()[0]["status"] == "sent")
            assert len(adapter.downloads) == len(adapter.sends) == len(calls) == 1
            assert adapter.sends[0][0].chat_id == "chat"
            assert adapter.sends[0][0].thread_id == "thread"
            assert adapter.sends[0][2:] == ("Raport gotowy.", 2.5)
            assert calls[0][1] == [{"role": "user", "content": "Zrób raport."}]
            assert [m["role"] for m in store.messages(manager.events()[0]["conversation_id"])] == [
                "user",
                "assistant",
            ]
            assert not list(manager.data_dir.glob("incoming-*"))
        finally:
            await manager.close()
        await manager.start()
        try:
            assert await manager.accept(incoming(config))
            assert len(calls) == 1
            assert manager.events()[0]["status"] == "sent"
        finally:
            await manager.close()

    asyncio.run(run())


def test_channel_sender_chat_and_thread_have_isolated_agent_history(setup):
    manager, _, _, calls, _ = setup

    async def run():
        first = channel(
            manager, allowed_chats=["chat", "chat-two"], allowed_senders=["sender", "sender-two"]
        )
        second = channel(manager)
        await manager.start()
        try:
            for config in (first, second):
                await manager.enable(config["id"])
            events = [
                incoming(first, "1"),
                incoming(first, "2"),
                incoming(first, "3", sender_id="sender-two"),
                incoming(first, "4", chat_id="chat-two"),
                incoming(first, "5", thread_id="another-thread"),
                incoming(second, "6"),
            ]
            for event in events:
                await manager.accept(event)
            await until(
                lambda: (
                    len(calls) == 6 and all(event["status"] == "sent" for event in manager.events())
                )
            )
            assert calls[0][2] == calls[1][2]
            assert len({call[2] for call in calls}) == 5
            assert [len(call[1]) for call in calls] == [1, 3, 1, 1, 1, 1]
        finally:
            await manager.close()

    asyncio.run(run())


def test_unknown_send_outcome_never_repeats_agent_or_delivery(setup):
    manager, adapters, _, calls, _ = setup

    async def run():
        config = channel(manager)
        await manager.start()
        try:
            await manager.enable(config["id"])
            adapter = adapters[config["id"]]
            adapter.fail_send = True
            await manager.accept(incoming(config))
            await until(lambda: manager.events()[0]["status"] == "uncertain")
            assert manager.events()[0]["reply"] == "Raport gotowy."
            assert "private-key" not in json.dumps(manager.events())
            assert len(adapter.sends) == len(calls) == 1
        finally:
            await manager.close()
        await manager.start()
        try:
            await manager.accept(incoming(config))
            assert manager.events()[0]["status"] == "uncertain"
            assert len(calls) == 1
            assert adapters[config["id"]].sends == []
        finally:
            await manager.close()

    asyncio.run(run())


def test_interrupted_processing_recovers_as_uncertain_but_queued_work_runs(setup):
    manager, _, _, calls, _ = setup

    async def run():
        config = channel(manager)
        await manager.enable(config["id"])
        await manager.accept(incoming(config, "interrupted"))
        await manager.accept(incoming(config, "queued"))
        with manager._connect() as db:
            db.execute(
                "UPDATE events SET status='processing',stage='agent' WHERE event_id='interrupted'"
            )
        await manager.start()
        try:
            await until(lambda: any(event["status"] == "sent" for event in manager.events()))
            assert {event["event_id"]: event["status"] for event in manager.events()} == {
                "queued": "sent",
                "interrupted": "uncertain",
            }
            assert len(calls) == 1
        finally:
            await manager.close()

    asyncio.run(run())


def test_synthesis_failure_preserves_agent_reply_without_reexecution(setup):
    manager, _, speech, calls, store = setup

    async def run():
        config = channel(manager)
        speech.fail_synthesis = True
        await manager.start()
        try:
            await manager.enable(config["id"])
            await manager.accept(incoming(config))
            await until(lambda: manager.events()[0]["status"] == "error")
            record = manager.events()[0]
            assert record["stage"] == "synthesis"
            assert record["reply"] == "Raport gotowy."
            reply = store.messages(record["conversation_id"])[-1]
            assert reply["text"] == record["reply"] and reply["status"] == "error"
            assert "private-key" not in json.dumps(record)
            await manager.accept(incoming(config))
            assert len(calls) == 1
        finally:
            await manager.close()

    asyncio.run(run())


def test_disable_cancels_active_work_and_requires_new_recordings(setup):
    manager, adapters, _, calls, _ = setup

    async def run():
        config = channel(manager)
        await manager.start()
        try:
            await manager.enable(config["id"])
            adapter = adapters[config["id"]]
            adapter.download_gate = asyncio.Event()
            await manager.accept(incoming(config, "active"))
            await until(lambda: bool(adapter.downloads))
            await manager.accept(incoming(config, "queued"))
            with pytest.raises(ValueError, match="Wyłącz"):
                manager.update(config["id"], {"name": "Changed"})
            await manager.disable(config["id"])
            assert {event["status"] for event in manager.events()} == {"uncertain", "error"}
            assert manager.list_channels()[0]["status"]["state"] == "disabled"
            manager.update(config["id"], {"name": "Changed"})
            await manager.enable(config["id"])
            await manager.accept(incoming(config, "active"))
            assert calls == []
            await manager.accept(incoming(config, "new"))
            await until(lambda: any(event["status"] == "sent" for event in manager.events()))
            assert len(calls) == 1
        finally:
            await manager.close()

    asyncio.run(run())


def test_secret_storage_permissions_public_redaction_and_partial_updates(setup):
    manager, adapters, _, _, _ = setup
    config = channel(manager)
    assert manager.path.stat().st_mode & 0o777 == 0o600
    assert manager.data_dir.stat().st_mode & 0o777 == 0o700
    assert config["secret_fields_set"] == ["bot_token"]
    assert "secrets" not in config
    changed = manager.update(config["id"], {"name": "Renamed", "secrets": {"bot_token": ""}})
    assert changed["name"] == "Renamed"
    assert manager.get_config(config["id"])["secrets"]["bot_token"] == "private-key"
    assert "private-key" not in json.dumps(manager.list_channels())

    async def run():
        await manager.check(config["id"])
        adapters[config["id"]].fail_check = ChannelError("Bad private-key")
        with pytest.raises(ChannelError, match=r"Bad \[ukryte\]"):
            await manager.check(config["id"])
        assert "private-key" not in json.dumps(manager.list_channels())
        adapters[config["id"]].fail_check = RuntimeError("URL includes private-key")
        with pytest.raises(ChannelError) as exc:
            await manager.enable(config["id"])
        assert "private-key" not in str(exc.value)
        assert manager.get_config(config["id"])["enabled"] is False

    asyncio.run(run())


def test_inbox_agent_reply_survives_restart_and_is_delivered_once_to_original_thread(setup):
    manager, adapters, speech, calls, store = setup

    async def run():
        config = channel(manager, agent_id="hermes")
        await manager.start()
        try:
            await manager.enable(config["id"])
            await manager.accept(incoming(config))
            await until(lambda: manager.events()[0]["status"] == "awaiting_agent")
            record = manager.events()[0]
            inbox = store.inbox("hermes")
            assert len(inbox) == 1
            assert inbox[0]["conversation_id"] == record["conversation_id"]
            assert inbox[0]["text"] == "Zrób raport."
            assert calls == speech.synthesized == adapters[config["id"]].sends == []
        finally:
            await manager.close()
        await manager.start()
        try:
            assert manager.events()[0]["status"] == "awaiting_agent"
            audio = speech.synthesize("MCP reply", "gosia")
            message = store.add_message(
                record["conversation_id"],
                "assistant",
                "MCP reply",
                audio["audio_url"],
                audio["duration"],
            )
            result = await manager.deliver_agent_message(message["id"], record["user_message_id"])
            assert result["status"] == "sent"
            assert (
                await manager.deliver_agent_message(message["id"], record["user_message_id"])
                == result
            )
            assert len(adapters[config["id"]].sends) == 1
            event, _, text, _ = adapters[config["id"]].sends[0]
            assert (event.chat_id, event.sender_id, event.thread_id) == ("chat", "sender", "thread")
            assert text == "MCP reply"
            proactive = store.add_message(
                record["conversation_id"],
                "assistant",
                "Proactive",
                audio["audio_url"],
                audio["duration"],
            )
            with pytest.raises(ValueError, match="reply_to_message_id"):
                await manager.deliver_agent_message(proactive["id"])
            assert len(adapters[config["id"]].sends) == 1
        finally:
            await manager.close()

    asyncio.run(run())


def test_concurrent_inbox_delivery_claim_and_unknown_outcome_never_send_twice(setup):
    manager, adapters, speech, _, store = setup

    async def run():
        config = channel(manager, agent_id="hermes")
        await manager.start()
        try:
            await manager.enable(config["id"])
            await manager.accept(incoming(config))
            await until(lambda: manager.events()[0]["status"] == "awaiting_agent")
            record = manager.events()[0]
            audio = speech.synthesize("MCP reply", "gosia")
            message = store.add_message(
                record["conversation_id"],
                "assistant",
                "MCP reply",
                audio["audio_url"],
                audio["duration"],
            )
            adapter = adapters[config["id"]]
            adapter.send_gate = asyncio.Event()
            adapter.fail_send = True
            delivery = asyncio.create_task(
                manager.deliver_agent_message(message["id"], record["user_message_id"])
            )
            await until(lambda: bool(adapter.sends))
            assert (await manager.deliver_agent_message(message["id"], record["user_message_id"]))[
                "status"
            ] == "processing"
            adapter.send_gate.set()
            assert (await delivery)["status"] == "uncertain"
            assert (await manager.deliver_agent_message(message["id"], record["user_message_id"]))[
                "status"
            ] == "uncertain"
            assert len(adapter.sends) == 1
        finally:
            await manager.close()

    asyncio.run(run())


def test_polling_only_starts_when_enabled_and_failure_is_safe(setup):
    manager, _, _, _, _ = setup
    poll_calls = []

    class PollingAdapter(AdapterDouble):
        async def run(self, accept):
            poll_calls.append(self.config["id"])
            raise RuntimeError("Remote error containing private-key")

    manager.adapter_factory = PollingAdapter

    async def run():
        config = channel(manager)
        await manager.start()
        try:
            assert poll_calls == []
            await manager.enable(config["id"])
            await until(lambda: bool(poll_calls))
            assert manager.list_channels()[0]["status"]["state"] == "error"
            assert "private-key" not in json.dumps(manager.list_channels())
            await manager.disable(config["id"])
        finally:
            await manager.close()

    asyncio.run(run())


def test_pairing_two_people_in_two_chats_never_grants_crossed_pairs(setup):
    manager, adapters, _, _, _ = setup

    async def run():
        config = channel(manager, allowed_chats=["manual-chat"], allowed_senders=["manual-sender"])
        await manager.start()
        try:
            await manager.enable(config["id"])
            for number, (chat, sender) in enumerate((("chat-a", "alice"), ("chat-b", "bob"))):
                await manager.accept(
                    incoming(config, f"pair-{number}", chat_id=chat, sender_id=sender)
                )
                pairing = manager.pairings()[0]
                manager.approve(config["id"], pairing["id"])
            assert "_manual_policy" not in manager.list_channels()[0]
            await manager.accept(incoming(config, "cross-one", chat_id="chat-a", sender_id="bob"))
            await manager.accept(incoming(config, "cross-two", chat_id="chat-b", sender_id="alice"))
            assert {(item["chat_id"], item["sender_id"]) for item in manager.pairings()} == {
                ("chat-a", "bob"),
                ("chat-b", "alice"),
            }
            assert adapters[config["id"]].downloads == []
            for number, (chat, sender) in enumerate(
                (("chat-a", "alice"), ("chat-b", "bob"), ("manual-chat", "manual-sender"))
            ):
                await manager.accept(
                    incoming(config, f"allowed-{number}", chat_id=chat, sender_id=sender)
                )
            await until(
                lambda: (
                    len(manager.events()) == 3
                    and all(event["status"] == "sent" for event in manager.events())
                )
            )
            assert len(adapters[config["id"]].downloads) == 3
        finally:
            await manager.close()

    asyncio.run(run())


async def ready_inbox_reply(manager, speech, store, config, event_id="one"):
    await manager.accept(incoming(config, event_id))
    await until(
        lambda: any(
            event["event_id"] == event_id and event["status"] == "awaiting_agent"
            for event in manager.events()
        )
    )
    record = next(event for event in manager.events() if event["event_id"] == event_id)
    audio = speech.synthesize(f"Reply {event_id}", "gosia")
    message = store.add_message(
        record["conversation_id"],
        "assistant",
        f"Reply {event_id}",
        audio["audio_url"],
        audio["duration"],
    )
    return record, message


def test_disabling_a_channel_cancels_api_delivery_but_not_other_channels(setup):
    manager, adapters, speech, _, store = setup

    async def run():
        first = channel(manager, agent_id="hermes")
        second = channel(manager, agent_id="hermes")
        await manager.start()
        try:
            for config in (first, second):
                await manager.enable(config["id"])
            first_record, first_reply = await ready_inbox_reply(
                manager, speech, store, first, "first"
            )
            second_record, second_reply = await ready_inbox_reply(
                manager, speech, store, second, "second"
            )
            entered = {name: asyncio.Event() for name in ("first", "second")}
            gates = {name: asyncio.Event() for name in ("first", "second")}
            completed = []

            async def send(event, path, text, duration):
                entered[event.event_id].set()
                await gates[event.event_id].wait()
                completed.append(event.event_id)

            for adapter in adapters.values():
                adapter.send_voice = send
            first_task = asyncio.create_task(
                manager.deliver_agent_message(first_reply["id"], first_record["user_message_id"])
            )
            second_task = asyncio.create_task(
                manager.deliver_agent_message(second_reply["id"], second_record["user_message_id"])
            )
            await asyncio.gather(*(event.wait() for event in entered.values()))
            await manager.disable(first["id"])
            assert first_task.cancelled()
            assert not second_task.done()
            gates["first"].set()
            gates["second"].set()
            assert (await second_task)["status"] == "sent"
            assert completed == ["second"]
            states = {record["event_id"]: record["status"] for record in manager.events()}
            assert states == {"first": "uncertain", "second": "sent"}
            assert (
                await manager.deliver_agent_message(
                    first_reply["id"], first_record["user_message_id"]
                )
            )["status"] == "uncertain"
            assert completed == ["second"]
        finally:
            await manager.close()

    asyncio.run(run())


@pytest.mark.parametrize("before_first_instruction", [False, True])
def test_shutdown_cancels_api_delivery_even_before_the_send_task_starts(
    setup, before_first_instruction
):
    manager, adapters, speech, _, store = setup

    async def run():
        config = channel(manager, agent_id="hermes")
        await manager.start()
        await manager.enable(config["id"])
        record, message = await ready_inbox_reply(manager, speech, store, config)
        entered = asyncio.Event()
        gate = asyncio.Event()
        completed = []

        async def send(event, path, text, duration):
            entered.set()
            await gate.wait()
            completed.append(event.event_id)

        adapters[config["id"]].send_voice = send
        task = asyncio.create_task(
            manager.deliver_agent_message(message["id"], record["user_message_id"])
        )
        if before_first_instruction:
            await asyncio.sleep(0)
            assert manager._deliveries
            assert not entered.is_set()
        else:
            await entered.wait()
        await manager.close()
        assert task.cancelled()
        gate.set()
        await asyncio.sleep(0)
        assert completed == []
        assert manager.events()[0]["status"] == "uncertain"
        assert manager._deliveries == {}
        assert await manager.deliver_agent_message(message["id"], record["user_message_id"]) is None

    asyncio.run(run())


def test_inbox_replies_require_exact_correlation_and_can_arrive_out_of_order(setup):
    manager, adapters, speech, _, store = setup

    async def run():
        config = channel(manager, agent_id="hermes")
        await manager.start()
        try:
            await manager.enable(config["id"])
            first, first_reply = await ready_inbox_reply(manager, speech, store, config, "first")
            second, second_reply = await ready_inbox_reply(manager, speech, store, config, "second")
            conversation_id = first["conversation_id"]
            assert second["conversation_id"] == conversation_id
            for wrong_id in (None, True, second_reply["id"], 987654321):
                with pytest.raises(ValueError, match="reply_to_message_id"):
                    manager.previous_agent_reply(conversation_id, wrong_id)
                with pytest.raises(ValueError, match="reply_to_message_id"):
                    await manager.deliver_agent_message(first_reply["id"], wrong_id)
            assert manager.previous_agent_reply(conversation_id, second["user_message_id"]) is None
            result = await manager.deliver_agent_message(
                second_reply["id"], second["user_message_id"]
            )
            previous = manager.previous_agent_reply(conversation_id, second["user_message_id"])
            assert previous == {"message": second_reply, "delivery": result}
            duplicate = store.add_message(
                conversation_id,
                "assistant",
                "Another reply",
                second_reply["audio_url"],
                second_reply["duration"],
            )
            assert (
                await manager.deliver_agent_message(duplicate["id"], second["user_message_id"])
                == result
            )
            assert manager.previous_agent_reply(conversation_id, first["user_message_id"]) is None
            await manager.deliver_agent_message(first_reply["id"], first["user_message_id"])
            assert [sent[0].event_id for sent in adapters[config["id"]].sends] == [
                "second",
                "first",
            ]
            plain = store.create_conversation("hermes")
            assert manager.previous_agent_reply(plain["id"], None) is None
        finally:
            await manager.close()

    asyncio.run(run())
