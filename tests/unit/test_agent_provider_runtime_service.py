from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

import pytest

import app.modules.agent_provider_runtime.service as runtime_service_module
from app.db.models import AgentProviderAccount
from app.modules.agent_provider_routing.settlement import AgentProviderUsageSettlementData
from app.modules.agent_provider_runtime.antigravity import (
    AntigravityHarnessExecutionError,
    AntigravityHarnessRequest,
    AntigravityProcessResult,
    antigravity_harness_env,
    build_antigravity_command,
    command_preview,
    inspect_antigravity_cli,
)
from app.modules.agent_provider_runtime.service import (
    AntigravityHarnessService,
    AntigravityManagedAgentService,
    AntigravityRuntimeRequestContext,
    GeminiRuntimeRequestContext,
    GeminiRuntimeService,
    GeminiRuntimeValidationError,
    parse_chat_completion_request,
)
from app.modules.api_keys.service import ApiKeyData, ApiKeyUsageReservationData


@dataclass(slots=True)
class _Selected:
    account: AgentProviderAccount


class _RoutingService:
    def __init__(self, account: AgentProviderAccount) -> None:
        self.account = account
        self.provider_ids: list[str] = []
        self.auth_modes: list[str | None] = []
        self.settlements: list[tuple[str, str, AgentProviderUsageSettlementData]] = []

    async def select_account(self, provider_id: str, *, auth_mode: str | None = None) -> _Selected:
        self.provider_ids.append(provider_id)
        self.auth_modes.append(auth_mode)
        return _Selected(account=self.account)

    async def settle_usage(
        self,
        provider_id: str,
        account_id: str,
        usage: AgentProviderUsageSettlementData,
    ) -> None:
        self.settlements.append((provider_id, account_id, usage))


class _FailingSettlementRoutingService(_RoutingService):
    async def settle_usage(
        self,
        provider_id: str,
        account_id: str,
        usage: AgentProviderUsageSettlementData,
    ) -> None:
        del provider_id, account_id, usage
        raise RuntimeError("database unavailable")


class _Decryptor:
    def decrypt(self, encrypted: bytes) -> str:
        assert encrypted == b"encrypted-key"
        return "AIza-test-key"


class _Response:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._payload = payload or {}
        self.content = _Content(chunks or [])

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def json(self) -> dict[str, Any]:
        return self._payload

    async def text(self) -> str:
        return "upstream-error"


class _Content:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_any(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class _CancelledContent:
    async def iter_any(self) -> AsyncIterator[bytes]:
        raise asyncio.CancelledError
        yield b""


class _FailingAfterChunkContent:
    async def iter_any(self) -> AsyncIterator[bytes]:
        yield (
            b'data: {"responseId":"resp_partial","candidates":[{"content":{"parts":[{"text":"Hi"}]}}],'
            b'"usageMetadata":{"promptTokenCount":2,"candidatesTokenCount":1,"totalTokenCount":3}}\n\n'
        )
        raise RuntimeError("stream failed")


class _CancelledResponse(_Response):
    def __init__(self) -> None:
        super().__init__(chunks=[])
        self.content = _CancelledContent()


class _FailingAfterChunkResponse(_Response):
    def __init__(self) -> None:
        super().__init__(chunks=[])
        self.content = _FailingAfterChunkContent()


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"url": url, **kwargs})
        return self.response


class _ApiKeyUsageService:
    def __init__(self) -> None:
        self.released: list[str] = []
        self.finalized: list[tuple[str, int, int]] = []

    async def enforce_limits_for_request(
        self,
        key_id: str,
        *,
        request_model: str | None,
        request_service_tier: str | None = None,
        request_usage_budget: object | None = None,
    ) -> ApiKeyUsageReservationData:
        del request_service_tier, request_usage_budget
        return ApiKeyUsageReservationData(
            reservation_id=f"reservation-{request_model}",
            key_id=key_id,
            model=request_model or "",
        )

    async def finalize_usage_reservation(
        self,
        reservation_id: str,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        service_tier: str | None = None,
    ) -> None:
        del model, cached_input_tokens, service_tier
        self.finalized.append((reservation_id, input_tokens, output_tokens))

    async def release_usage_reservation(self, reservation_id: str) -> None:
        self.released.append(reservation_id)


def _account(
    *,
    account_id: str = "gemini-account",
    provider_id: str = "gemini",
    display_name: str = "Gemini",
    auth_mode: str = "api_key",
    api_key_encrypted: bytes | None = b"encrypted-key",
    external_account_id: str | None = None,
) -> AgentProviderAccount:
    return AgentProviderAccount(
        id=account_id,
        provider_id=provider_id,
        external_account_id=external_account_id,
        display_name=display_name,
        auth_mode=auth_mode,
        api_key_encrypted=api_key_encrypted,
        status="active",
    )


class _AntigravityRunner:
    def __init__(self, result: AntigravityProcessResult | None = None, on_run: Any | None = None) -> None:
        self.result = result or AntigravityProcessResult(
            exit_code=0,
            stdout="agy-result",
            stderr="",
            duration_ms=12,
        )
        self.on_run = on_run
        self.commands = []
        self.envs: list[dict[str, str]] = []

    async def run(self, command, *, env):
        self.commands.append(command)
        self.envs.append(dict(env))
        if self.on_run is not None:
            self.on_run(command)
        return self.result


def _write_fake_antigravity_conversation(
    app_data_dir,
    workspace,
    *,
    prompt: str,
    response: str,
    extra_response_fields: tuple[str, ...] = (),
) -> None:
    conversation_id = "11111111-1111-4111-8111-111111111111"
    cache_dir = app_data_dir / "cache"
    conversations_dir = app_data_dir / "conversations"
    cache_dir.mkdir(parents=True)
    conversations_dir.mkdir(parents=True)
    (cache_dir / "last_conversations.json").write_text(
        json.dumps({str(workspace): conversation_id}),
        encoding="utf-8",
    )
    db_path = conversations_dir / f"{conversation_id}.db"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("create table steps (idx integer primary key, step_type integer, step_payload blob)")
        connection.execute(
            "insert into steps (idx, step_type, step_payload) values (?, ?, ?)",
            (0, 14, _pb_string(prompt)),
        )
        connection.execute(
            "insert into steps (idx, step_type, step_payload) values (?, ?, ?)",
            (1, 15, b"".join(_pb_string(value) for value in (response, *extra_response_fields))),
        )
        connection.execute(
            "insert into steps (idx, step_type, step_payload) values (?, ?, ?)",
            (2, 15, _pb_string("\n$ca617789-7a86-42b2-b0f4-636102a34590\x10\x05\x18\x01")),
        )
        connection.commit()
    finally:
        connection.close()


def _pb_string(value: str) -> bytes:
    payload = value.encode()
    return _pb_varint(10) + _pb_varint(len(payload)) + payload


def _pb_varint(value: int) -> bytes:
    output = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            output.append(byte | 0x80)
        else:
            output.append(byte)
            return bytes(output)


def test_build_antigravity_command_redacts_prompt_and_disables_dangerous_flags(tmp_path) -> None:
    extra_dir = tmp_path / "extra"
    extra_dir.mkdir()

    command = build_antigravity_command(
        AntigravityHarnessRequest(
            prompt="Inspect this",
            workspace_path=str(tmp_path),
            timeout_seconds=30,
            model="gemini-3-flash-preview",
            add_dirs=("extra",),
            sandbox="read-only",
        )
    )

    assert command.cwd == tmp_path.resolve()
    assert command.args[:4] == ("--print", "Inspect this", "--print-timeout", "30s")
    assert ("--model", "gemini-3-flash-preview") == command.args[4:6]
    assert "--dangerously-skip-permissions" not in command.args
    assert command_preview(command)[:4] == ("agy", "--print", "<redacted>", "--print-timeout")
    assert ("--model", "gemini-3-flash-preview") == command_preview(command)[5:7]
    assert command_preview(command)[3] == "--print-timeout"
    assert antigravity_harness_env({})["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true"


@pytest.mark.asyncio
async def test_antigravity_cli_diagnostics_reports_missing_executable() -> None:
    diagnostics = await inspect_antigravity_cli(executable="agy-definitely-missing-for-alb-test")

    assert diagnostics.installed is False
    assert diagnostics.resolved_path is None
    assert diagnostics.version is None
    assert diagnostics.print_supported is False
    assert diagnostics.error == "agy-definitely-missing-for-alb-test was not found on PATH"


@pytest.mark.asyncio
async def test_antigravity_harness_selects_profile_and_settles_request(tmp_path) -> None:
    account = _account(
        account_id="agy-account",
        provider_id="antigravity",
        display_name="Antigravity",
        auth_mode="cli_keyring",
        api_key_encrypted=None,
        external_account_id="default",
    )
    routing = _RoutingService(account)
    runner = _AntigravityRunner()
    service = AntigravityHarnessService(routing, runner=runner)

    result = await service.print_prompt(
        AntigravityHarnessRequest(prompt="Say hi", workspace_path=str(tmp_path), timeout_seconds=5)
    )

    assert result.account.id == "agy-account"
    assert routing.provider_ids == ["antigravity"]
    assert routing.settlements == [("antigravity", "agy-account", AgentProviderUsageSettlementData(requests=1))]
    assert runner.commands[0].args[:4] == ("--print", "Say hi", "--print-timeout", "5s")
    assert "--dangerously-skip-permissions" not in runner.commands[0].args
    assert runner.envs[0]["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true"
    assert runner.envs[0]["AGY_CLI_PROFILE"] == "default"
    assert runner.envs[0]["ANTIGRAVITY_CLI_PROFILE"] == "default"


@pytest.mark.asyncio
async def test_antigravity_harness_does_not_settle_failed_cli_run(tmp_path) -> None:
    account = _account(
        account_id="agy-account",
        provider_id="antigravity",
        display_name="Antigravity",
        auth_mode="cli_keyring",
        api_key_encrypted=None,
    )
    routing = _RoutingService(account)
    runner = _AntigravityRunner(AntigravityProcessResult(exit_code=2, stdout="", stderr="failed", duration_ms=7))
    service = AntigravityHarnessService(routing, runner=runner)

    result = await service.print_prompt(
        AntigravityHarnessRequest(prompt="Say hi", workspace_path=str(tmp_path), timeout_seconds=5)
    )

    assert result.process.exit_code == 2
    assert routing.settlements == []


@pytest.mark.asyncio
async def test_antigravity_harness_rejects_blank_success_output(tmp_path) -> None:
    account = _account(
        account_id="agy-account",
        provider_id="antigravity",
        auth_mode="cli_keyring",
        external_account_id="default",
    )
    routing = _RoutingService(account)
    runner = _AntigravityRunner(
        AntigravityProcessResult(exit_code=0, stdout="", stderr="", duration_ms=12),
    )
    service = AntigravityHarnessService(routing, runner=runner)

    with pytest.raises(AntigravityHarnessExecutionError, match="without output"):
        await service.print_prompt(
            AntigravityHarnessRequest(prompt="Hi", workspace_path=str(tmp_path), timeout_seconds=5),
        )

    assert routing.settlements == []


@pytest.mark.asyncio
async def test_antigravity_harness_recovers_blank_success_output_from_conversation_db(tmp_path) -> None:
    account = _account(
        account_id="agy-account",
        provider_id="antigravity",
        auth_mode="cli_keyring",
        external_account_id="default",
    )
    app_data_dir = tmp_path / "agy-home"

    def _on_run(command) -> None:
        _write_fake_antigravity_conversation(
            app_data_dir,
            command.cwd,
            prompt="Hi",
            response="Recovered agy text.",
        )

    routing = _RoutingService(account)
    runner = _AntigravityRunner(
        AntigravityProcessResult(exit_code=0, stdout="", stderr="", duration_ms=12),
        on_run=_on_run,
    )
    service = AntigravityHarnessService(routing, runner=runner, app_data_dir=app_data_dir)

    result = await service.print_prompt(
        AntigravityHarnessRequest(prompt="Hi", workspace_path=str(tmp_path), timeout_seconds=5),
    )

    assert result.process.stdout == "Recovered agy text."
    assert routing.settlements == [("antigravity", "agy-account", AgentProviderUsageSettlementData(requests=1))]


@pytest.mark.asyncio
async def test_antigravity_harness_prefers_repeated_recovered_answer_over_opaque_token(tmp_path) -> None:
    account = _account(
        account_id="agy-account",
        provider_id="antigravity",
        auth_mode="cli_keyring",
        external_account_id="default",
    )
    app_data_dir = tmp_path / "agy-home"

    def _on_run(command) -> None:
        _write_fake_antigravity_conversation(
            app_data_dir,
            command.cwd,
            prompt="Reply with ALB_API_OK only.",
            response="synjuMPhde0KA",
            extra_response_fields=("ALB_API_OK", "ALB_API_OK"),
        )

    routing = _RoutingService(account)
    runner = _AntigravityRunner(
        AntigravityProcessResult(exit_code=0, stdout="", stderr="", duration_ms=12),
        on_run=_on_run,
    )
    service = AntigravityHarnessService(routing, runner=runner, app_data_dir=app_data_dir)

    result = await service.print_prompt(
        AntigravityHarnessRequest(
            prompt="Reply with ALB_API_OK only.",
            workspace_path=str(tmp_path),
            timeout_seconds=5,
        ),
    )

    assert result.process.stdout == "ALB_API_OK"
    assert routing.settlements == [("antigravity", "agy-account", AgentProviderUsageSettlementData(requests=1))]


@pytest.mark.asyncio
async def test_antigravity_interaction_maps_steps_text_and_usage(monkeypatch) -> None:
    upstream = _Response(
        {
            "id": "interaction_1",
            "agent": "antigravity-preview-05-2026",
            "steps": [
                {
                    "type": "model_output",
                    "content": [
                        {"type": "text", "text": "Official "},
                        {"type": "text", "text": "shape"},
                    ],
                }
            ],
            "usage": {"total_input_tokens": 4, "total_output_tokens": 2, "total_tokens": 6},
        }
    )
    session = _Session(upstream)

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)
    routing = _RoutingService(_account(account_id="agy-account", provider_id="antigravity"))
    service = AntigravityManagedAgentService(routing, decryptor=_Decryptor())

    response = await service.complete_chat(
        {"model": "antigravity-preview-05-2026", "messages": [{"role": "user", "content": "Do work"}]}
    )

    choices = cast(list[dict[str, Any]], response["choices"])
    message = cast(dict[str, Any], choices[0]["message"])
    assert message["content"] == "Official shape"
    assert session.calls[0]["headers"]["Api-Revision"] == "2026-05-20"
    assert session.calls[0]["json"]["model"] == "gemini-3-flash-preview"
    assert routing.settlements == [
        (
            "antigravity",
            "agy-account",
            AgentProviderUsageSettlementData(requests=1, prompt_tokens=4, completion_tokens=2, total_tokens=6),
        )
    ]


@pytest.mark.asyncio
async def test_antigravity_interaction_finalizes_api_key_when_provider_settlement_fails(monkeypatch) -> None:
    upstream = _Response(
        {
            "id": "interaction_1",
            "output_text": "done",
            "usage": {"total_input_tokens": 4, "total_output_tokens": 2, "total_tokens": 6},
        }
    )
    session = _Session(upstream)

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)
    api_key_usage = _ApiKeyUsageService()
    service = AntigravityManagedAgentService(
        _FailingSettlementRoutingService(_account(account_id="agy-account", provider_id="antigravity")),
        decryptor=_Decryptor(),
        api_key_service=api_key_usage,
    )
    context = AntigravityRuntimeRequestContext(
        api_key=ApiKeyData(
            id="key-1",
            name="Key",
            key_prefix="sk",
            allowed_models=None,
            enforced_model=None,
            enforced_reasoning_effort=None,
            enforced_service_tier=None,
            expires_at=None,
            is_active=True,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
        )
    )

    response = await service.create_interaction(
        {"agent": "antigravity-preview-05-2026", "input": "Do work", "environment": "remote"},
        context,
    )

    assert response["output_text"] == "done"
    assert api_key_usage.finalized == [("reservation-antigravity-preview-05-2026", 4, 2)]
    assert api_key_usage.released == []


def test_parse_chat_completion_request_validates_shape() -> None:
    with pytest.raises(GeminiRuntimeValidationError, match="model is required"):
        parse_chat_completion_request({"messages": [{"role": "user", "content": "Hi"}]})

    request = parse_chat_completion_request(
        {
            "model": "gemini-2.5-flash",
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
            "temperature": 0.2,
            "max_tokens": 8,
        }
    )

    assert request.model == "gemini-2.5-flash"
    assert request.stream is True
    assert request.temperature == 0.2
    assert request.max_tokens == 8


def test_parse_chat_completion_request_maps_gemini_thinking_controls() -> None:
    budget_request = parse_chat_completion_request(
        {
            "model": "gemini-2.5-flash",
            "messages": [{"role": "user", "content": "Hi"}],
            "thinkingBudget": 0,
        }
    )
    level_request = parse_chat_completion_request(
        {
            "model": "gemini-3-flash-preview",
            "messages": [{"role": "user", "content": "Hi"}],
            "thinking_level": "low",
        }
    )

    assert budget_request.thinking_budget == 0
    assert level_request.thinking_level == "low"


def test_parse_chat_completion_request_uses_max_completion_tokens_fallback() -> None:
    request = parse_chat_completion_request(
        {
            "model": "gemini-2.5-flash",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_completion_tokens": 32,
        }
    )

    assert request.max_tokens == 32


def test_parse_chat_completion_request_prefers_max_tokens_over_max_completion_tokens() -> None:
    request = parse_chat_completion_request(
        {
            "model": "gemini-2.5-flash",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 8,
            "max_completion_tokens": 32,
        }
    )

    assert request.max_tokens == 8


@pytest.mark.asyncio
async def test_complete_chat_selects_gemini_account_and_calls_native_endpoint(monkeypatch) -> None:
    upstream = _Response(
        {
            "responseId": "resp_1",
            "candidates": [{"content": {"parts": [{"text": "Hello"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1, "totalTokenCount": 3},
        }
    )
    session = _Session(upstream)

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)
    routing = _RoutingService(_account())
    service = GeminiRuntimeService(routing, decryptor=_Decryptor())

    response = await service.complete_chat(
        {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Hi"}]}
    )

    assert routing.provider_ids == ["gemini"]
    assert routing.settlements == [
        (
            "gemini",
            "gemini-account",
            AgentProviderUsageSettlementData(requests=1, prompt_tokens=2, completion_tokens=1, total_tokens=3),
        )
    ]
    choices = cast(list[dict[str, Any]], response["choices"])
    message = cast(dict[str, Any], choices[0]["message"])
    assert message["content"] == "Hello"
    assert session.calls[0]["url"].endswith("/models/gemini-2.5-flash:generateContent")
    assert session.calls[0]["headers"]["x-goog-api-key"] == "AIza-test-key"
    assert session.calls[0]["json"] == {"contents": [{"role": "user", "parts": [{"text": "Hi"}]}]}


@pytest.mark.asyncio
async def test_complete_chat_finalizes_api_key_when_provider_settlement_fails(monkeypatch) -> None:
    upstream = _Response(
        {
            "responseId": "resp_1",
            "candidates": [{"content": {"parts": [{"text": "Hello"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1, "totalTokenCount": 3},
        }
    )
    session = _Session(upstream)

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)
    api_key_usage = _ApiKeyUsageService()
    service = GeminiRuntimeService(
        _FailingSettlementRoutingService(_account()),
        decryptor=_Decryptor(),
        api_key_service=api_key_usage,
    )
    context = GeminiRuntimeRequestContext(
        api_key=ApiKeyData(
            id="key-1",
            name="Key",
            key_prefix="sk",
            allowed_models=None,
            enforced_model=None,
            enforced_reasoning_effort=None,
            enforced_service_tier=None,
            expires_at=None,
            is_active=True,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
        )
    )

    response = await service.complete_chat(
        {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Hi"}]},
        context,
    )

    choices = cast(list[dict[str, Any]], response["choices"])
    message = cast(dict[str, Any], choices[0]["message"])
    assert message["content"] == "Hello"
    assert api_key_usage.finalized == [("reservation-gemini-2.5-flash", 2, 1)]
    assert api_key_usage.released == []


@pytest.mark.asyncio
async def test_stream_chat_translates_gemini_sse_to_openai_sse(monkeypatch) -> None:
    upstream = _Response(
        chunks=[
            b'data: {"responseId":"resp_2","candidates":[{"content":{"parts":[{"text":"Hel"}]}}]}\r\n\r\n',
            (
                b'data: {"responseId":"resp_2","candidates":'
                b'[{"content":{"parts":[{"text":"lo"}]},"finishReason":"STOP"}]}\n\n'
            ),
        ]
    )
    session = _Session(upstream)

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)
    routing = _RoutingService(_account())
    service = GeminiRuntimeService(routing, decryptor=_Decryptor())

    body = await service.stream_chat(
        {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Hi"}], "stream": True}
    )
    chunks = [chunk async for chunk in body]

    assert session.calls[0]["url"].endswith("/models/gemini-2.5-flash:streamGenerateContent?alt=sse")
    assert '"object":"chat.completion.chunk"' in chunks[0]
    assert '"content":"Hel"' in chunks[0]
    assert '"content":"lo"' in chunks[1]
    assert chunks[-1] == "data: [DONE]\n\n"
    assert routing.settlements == [
        ("gemini", "gemini-account", AgentProviderUsageSettlementData(requests=1)),
    ]


@pytest.mark.asyncio
async def test_stream_chat_decodes_utf8_split_across_chunks(monkeypatch) -> None:
    text = "H" + chr(233)
    event = (
        'data: {"responseId":"resp_utf8","candidates":[{"content":{"parts":[{"text":"'
        + text
        + '"}]},"finishReason":"STOP"}]}\n\n'
    ).encode("utf-8")
    split_at = event.index(chr(233).encode("utf-8")) + 1
    upstream = _Response(chunks=[event[:split_at], event[split_at:]])
    session = _Session(upstream)

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)
    service = GeminiRuntimeService(_RoutingService(_account()), decryptor=_Decryptor())

    body = await service.stream_chat(
        {"model": "gemini-3-flash-preview", "messages": [{"role": "user", "content": "Hi"}], "stream": True}
    )
    chunks = [chunk async for chunk in body]

    assert "H\\u00e9" in chunks[0]


@pytest.mark.asyncio
async def test_stream_chat_settles_request_when_cancelled_after_account_selection(monkeypatch) -> None:
    session = _Session(_CancelledResponse())

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)
    api_key_usage = _ApiKeyUsageService()
    routing = _RoutingService(_account())
    service = GeminiRuntimeService(routing, decryptor=_Decryptor(), api_key_service=api_key_usage)
    context = GeminiRuntimeRequestContext(
        api_key=ApiKeyData(
            id="key-1",
            name="Key",
            key_prefix="sk",
            allowed_models=None,
            enforced_model=None,
            enforced_reasoning_effort=None,
            enforced_service_tier=None,
            expires_at=None,
            is_active=True,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
        )
    )

    body = await service.stream_chat(
        {"model": "gemini-3-flash-preview", "messages": [{"role": "user", "content": "Hi"}], "stream": True},
        context,
    )

    with pytest.raises(asyncio.CancelledError):
        async for _chunk in body:
            pass

    assert api_key_usage.released == ["reservation-gemini-3-flash-preview"]
    assert api_key_usage.finalized == []
    assert routing.settlements == []


@pytest.mark.asyncio
async def test_stream_chat_settles_partial_usage_when_stream_fails_after_chunks(monkeypatch) -> None:
    session = _Session(_FailingAfterChunkResponse())

    @asynccontextmanager
    async def _lease() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(runtime_service_module, "lease_http_session", _lease)
    api_key_usage = _ApiKeyUsageService()
    routing = _RoutingService(_account())
    service = GeminiRuntimeService(routing, decryptor=_Decryptor(), api_key_service=api_key_usage)
    context = GeminiRuntimeRequestContext(
        api_key=ApiKeyData(
            id="key-1",
            name="Key",
            key_prefix="sk",
            allowed_models=None,
            enforced_model=None,
            enforced_reasoning_effort=None,
            enforced_service_tier=None,
            expires_at=None,
            is_active=True,
            created_at=datetime.now(timezone.utc),
            last_used_at=None,
        )
    )
    body = await service.stream_chat(
        {"model": "gemini-3-flash-preview", "messages": [{"role": "user", "content": "Hi"}], "stream": True},
        context,
    )

    chunks: list[str] = []
    with pytest.raises(RuntimeError, match="stream failed"):
        async for chunk in body:
            chunks.append(chunk)

    assert chunks
    assert api_key_usage.released == []
    assert api_key_usage.finalized == [("reservation-gemini-3-flash-preview", 2, 1)]
    assert routing.settlements == [
        (
            "gemini",
            "gemini-account",
            AgentProviderUsageSettlementData(requests=1, prompt_tokens=2, completion_tokens=1, total_tokens=3),
        )
    ]
