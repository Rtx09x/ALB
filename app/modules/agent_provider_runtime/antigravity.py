from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sqlite3
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class AntigravityHarnessValidationError(Exception):
    pass


class AntigravityHarnessExecutionError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class AntigravityHarnessRequest:
    prompt: str
    workspace_path: str
    timeout_seconds: int = 300
    model: str | None = None
    add_dirs: tuple[str, ...] = ()
    conversation_id: str | None = None
    continue_conversation: bool = False
    sandbox: str | None = None


@dataclass(frozen=True, slots=True)
class AntigravityHarnessCommand:
    executable: str
    args: tuple[str, ...]
    cwd: Path
    timeout_seconds: int
    display_args: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AntigravityProcessResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


@dataclass(frozen=True, slots=True)
class AntigravityCliDiagnosticsResult:
    executable: str
    resolved_path: str | None
    installed: bool
    version: str | None
    settings_path: str
    settings_exists: bool
    print_supported: bool
    print_timeout_supported: bool
    conversation_supported: bool
    add_dir_supported: bool
    sandbox_supported: bool
    model_supported: bool
    plugin_supported: bool
    error: str | None = None


class AntigravityProcessRunnerPort(Protocol):
    async def run(
        self,
        command: AntigravityHarnessCommand,
        *,
        env: Mapping[str, str],
    ) -> AntigravityProcessResult: ...


class AntigravitySubprocessRunner:
    async def run(
        self,
        command: AntigravityHarnessCommand,
        *,
        env: Mapping[str, str],
    ) -> AntigravityProcessResult:
        started_at = time.perf_counter()
        process = await asyncio.create_subprocess_exec(
            command.executable,
            *command.args,
            cwd=str(command.cwd),
            env=dict(env),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=command.timeout_seconds + 5,
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise AntigravityHarnessExecutionError("Antigravity CLI timed out") from exc
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        return AntigravityProcessResult(
            exit_code=process.returncode or 0,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            duration_ms=max(0, int((time.perf_counter() - started_at) * 1000)),
        )


def build_antigravity_command(
    request: AntigravityHarnessRequest,
    *,
    executable: str = "agy",
) -> AntigravityHarnessCommand:
    prompt = request.prompt.strip()
    if not prompt:
        raise AntigravityHarnessValidationError("prompt is required")
    if request.timeout_seconds < 1 or request.timeout_seconds > 1800:
        raise AntigravityHarnessValidationError("timeout_seconds must be between 1 and 1800")
    if request.conversation_id is not None and request.continue_conversation:
        raise AntigravityHarnessValidationError("conversation_id cannot be combined with continue_conversation")

    workspace_path = _existing_directory(request.workspace_path, base=None, field_name="workspace_path")
    add_dirs = tuple(
        _existing_directory(path, base=workspace_path, field_name="add_dirs").resolve() for path in request.add_dirs
    )

    args: list[str] = [
        "--print",
        prompt,
        "--print-timeout",
        f"{request.timeout_seconds}s",
    ]
    display_args: list[str] = [
        "--print",
        "<redacted>",
        "--print-timeout",
        f"{request.timeout_seconds}s",
    ]
    if request.model is not None:
        model = request.model.strip()
        if not model:
            raise AntigravityHarnessValidationError("model cannot be blank")
        args.extend(["--model", model])
        display_args.extend(["--model", model])

    if request.conversation_id is not None:
        conversation_id = request.conversation_id.strip()
        if not conversation_id:
            raise AntigravityHarnessValidationError("conversation_id cannot be blank")
        args.extend(["--conversation", conversation_id])
        display_args.extend(["--conversation", conversation_id])
    elif request.continue_conversation:
        args.append("--continue")
        display_args.append("--continue")
    for add_dir in add_dirs:
        args.extend(["--add-dir", str(add_dir)])
        display_args.extend(["--add-dir", str(add_dir)])
    if request.sandbox is not None:
        sandbox = request.sandbox.strip()
        if not sandbox:
            raise AntigravityHarnessValidationError("sandbox cannot be blank")
        args.extend(["--sandbox", sandbox])
        display_args.extend(["--sandbox", sandbox])

    _reject_dangerous_permission_flag(args)
    return AntigravityHarnessCommand(
        executable=executable,
        args=tuple(args),
        cwd=workspace_path.resolve(),
        timeout_seconds=request.timeout_seconds,
        display_args=tuple(display_args),
    )


def antigravity_harness_env(
    base_env: Mapping[str, str] | None = None,
    *,
    profile_id: str | None = None,
) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    env["AGY_CLI_DISABLE_AUTO_UPDATE"] = "true"
    if profile_id is not None and profile_id.strip():
        env["AGY_CLI_PROFILE"] = profile_id.strip()
        env["ANTIGRAVITY_CLI_PROFILE"] = profile_id.strip()
    return env


def command_preview(command: AntigravityHarnessCommand) -> tuple[str, ...]:
    return (command.executable, *command.display_args)


def recover_antigravity_print_output(
    workspace_path: Path,
    *,
    prompt: str,
    app_data_dir: Path | None = None,
    min_modified_at: float | None = None,
) -> str | None:
    root = Path.home() / ".gemini" / "antigravity-cli" if app_data_dir is None else app_data_dir
    conversation_id = _latest_conversation_id_for_workspace(root, workspace_path)
    if conversation_id is None:
        return None
    db_path = root / "conversations" / f"{conversation_id}.db"
    if not db_path.is_file():
        return None
    if min_modified_at is not None and db_path.stat().st_mtime + 1 < min_modified_at:
        return None
    return _latest_print_output_from_conversation_db(db_path, prompt=prompt)


async def inspect_antigravity_cli(
    *,
    executable: str = "agy",
    timeout_seconds: float = 5.0,
) -> AntigravityCliDiagnosticsResult:
    resolved_path = shutil.which(executable)
    settings_path = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
    if resolved_path is None:
        return AntigravityCliDiagnosticsResult(
            executable=executable,
            resolved_path=None,
            installed=False,
            version=None,
            settings_path=str(settings_path),
            settings_exists=settings_path.is_file(),
            print_supported=False,
            print_timeout_supported=False,
            conversation_supported=False,
            add_dir_supported=False,
            sandbox_supported=False,
            model_supported=False,
            plugin_supported=False,
            error=f"{executable} was not found on PATH",
        )

    version_result = await _run_antigravity_probe((resolved_path, "--version"), timeout_seconds=timeout_seconds)
    help_result = await _run_antigravity_probe((resolved_path, "--help"), timeout_seconds=timeout_seconds)
    help_text = f"{help_result.stdout}\n{help_result.stderr}"
    error = version_result.error or help_result.error
    return AntigravityCliDiagnosticsResult(
        executable=executable,
        resolved_path=resolved_path,
        installed=version_result.exit_code == 0 or help_result.exit_code == 0,
        version=_first_nonempty_line(version_result.stdout or version_result.stderr),
        settings_path=str(settings_path),
        settings_exists=settings_path.is_file(),
        print_supported="--print" in help_text,
        print_timeout_supported="--print-timeout" in help_text,
        conversation_supported="--conversation" in help_text and "--continue" in help_text,
        add_dir_supported="--add-dir" in help_text,
        sandbox_supported="--sandbox" in help_text,
        model_supported="--model" in help_text,
        plugin_supported="plugin" in help_text,
        error=error,
    )


def _existing_directory(path_value: str, *, base: Path | None, field_name: str) -> Path:
    raw = path_value.strip()
    if not raw:
        raise AntigravityHarnessValidationError(f"{field_name} cannot be blank")
    path = Path(raw)
    if not path.is_absolute():
        if base is None:
            raise AntigravityHarnessValidationError(f"{field_name} must be absolute")
        path = base / path
    if not path.is_dir():
        raise AntigravityHarnessValidationError(f"{field_name} must be an existing directory")
    return path


def _reject_dangerous_permission_flag(args: Sequence[str]) -> None:
    if "--dangerously-skip-permissions" in args:
        raise AntigravityHarnessValidationError("dangerous Antigravity permission bypass is not allowed")


def _latest_conversation_id_for_workspace(root: Path, workspace_path: Path) -> str | None:
    cache_path = root / "cache" / "last_conversations.json"
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    keys = {str(workspace_path), str(workspace_path.resolve())}
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _latest_print_output_from_conversation_db(db_path: Path, *, prompt: str) -> str | None:
    prompt_text = prompt.strip()
    try:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        rows = connection.execute(
            "select step_type, step_payload from steps order by case when step_type = 15 then 0 else 1 end, idx desc",
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        connection.close()

    for (_step_type, payload) in rows:
        if payload is None:
            continue
        candidates = [
            candidate.strip()
            for candidate in _protobuf_text_fields(bytes(payload))
            if _is_antigravity_print_output_candidate(candidate, prompt_text)
        ]
        if candidates:
            return _best_print_output_candidate(candidates)
    return None


def _best_print_output_candidate(candidates: list[str]) -> str:
    counts = Counter(candidates)
    return max(candidates, key=lambda value: (counts[value], _candidate_signal_score(value), len(value)))


def _candidate_signal_score(value: str) -> int:
    return sum(1 for char in value if char.isspace() or char in "_.,:;!?-")


def _is_antigravity_print_output_candidate(value: str, prompt: str) -> bool:
    text = value.strip()
    if not text or text == prompt:
        return False
    if any(ord(char) < 32 and char not in "\t\r\n" for char in text):
        return False
    if text in {"sessionID", "command(*)", "execute_url(*)", "read_url(*)"}:
        return False
    if text.startswith(("bot-", "file://")) or ":\\" in text:
        return False
    if _UUID_RE.search(text):
        return False
    if _SESSION_TOKEN_RE.fullmatch(text):
        return False
    return True


_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_SESSION_TOKEN_RE = re.compile(r"-?\d{8,}|[A-Za-z0-9_-]{16,}")


def _protobuf_text_fields(data: bytes, *, depth: int = 0) -> list[str]:
    if depth > 8:
        return []
    values: list[str] = []
    index = 0
    while index < len(data):
        try:
            key, index = _read_protobuf_varint(data, index)
        except ValueError:
            break
        field_number = key >> 3
        wire_type = key & 7
        if field_number <= 0:
            break
        try:
            if wire_type == 0:
                _, index = _read_protobuf_varint(data, index)
            elif wire_type == 1:
                index += 8
            elif wire_type == 2:
                length, index = _read_protobuf_varint(data, index)
                chunk = data[index : index + length]
                index += length
                text = _utf8_text_or_none(chunk)
                if text is not None:
                    values.append(text)
                if length <= 200_000:
                    values.extend(_protobuf_text_fields(chunk, depth=depth + 1))
            elif wire_type == 5:
                index += 4
            else:
                break
        except ValueError:
            break
    return values


def _read_protobuf_varint(data: bytes, index: int) -> tuple[int, int]:
    shift = 0
    result = 0
    while index < len(data) and shift < 70:
        byte = data[index]
        index += 1
        result |= (byte & 0x7F) << shift
        if byte & 0x80 == 0:
            return result, index
        shift += 7
    raise ValueError("invalid protobuf varint")


def _utf8_text_or_none(data: bytes) -> str | None:
    if not data or len(data) > 20_000:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    printable = sum(1 for char in text if char in "\t\r\n" or " " <= char <= "~")
    if printable / len(text) < 0.85:
        return None
    return text


@dataclass(frozen=True, slots=True)
class _ProbeResult:
    exit_code: int
    stdout: str
    stderr: str
    error: str | None


async def _run_antigravity_probe(command: Sequence[str], *, timeout_seconds: float) -> _ProbeResult:
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except (OSError, TimeoutError) as exc:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        return _ProbeResult(exit_code=1, stdout="", stderr="", error=str(exc))
    except asyncio.CancelledError:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        raise
    return _ProbeResult(
        exit_code=process.returncode or 0,
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
        error=None,
    )


def _first_nonempty_line(value: str) -> str | None:
    for line in value.splitlines():
        text = line.strip()
        if text:
            return text
    return None
