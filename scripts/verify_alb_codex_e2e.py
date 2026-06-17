from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real Codex e2e smoke against an ALB instance.")
    parser.add_argument("--base-url", default="http://127.0.0.1:2455")
    parser.add_argument("--auth-json", type=Path, help="Optional Codex auth.json export to import into this ALB.")
    parser.add_argument("--api-key", help="Existing ALB API key. If omitted, the script creates a temporary key.")
    parser.add_argument("--model", default="gpt-5.2")
    parser.add_argument("--prompt", default="Reply with ALB_CODEX_OK only.")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    health = _request_json("GET", f"{base_url}/health", timeout=args.timeout)
    if health.get("status") != "ok":
        raise SystemExit(f"ALB health is not ok: {health!r}")

    if args.auth_json is not None:
        _import_auth_json(base_url, args.auth_json, timeout=args.timeout)

    api_key = args.api_key or _create_api_key(base_url, timeout=args.timeout)
    started = time.perf_counter()
    response = _request_json(
        "POST",
        f"{base_url}/v1/responses",
        payload={"model": args.model, "input": args.prompt},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=args.timeout,
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    output_text = _response_output_text(response).strip()
    ok = output_text == "ALB_CODEX_OK"
    print(
        json.dumps(
            {
                "ok": ok,
                "model": args.model,
                "responseId": response.get("id"),
                "outputText": output_text,
                "elapsedMs": elapsed_ms,
            },
            ensure_ascii=False,
        )
    )
    return 0 if ok else 1


def _import_auth_json(base_url: str, auth_json_path: Path, *, timeout: float) -> None:
    if not auth_json_path.is_file():
        raise SystemExit(f"auth json not found: {auth_json_path}")
    boundary = f"----alb-smoke-{uuid4().hex}"
    content_type = mimetypes.guess_type(auth_json_path.name)[0] or "application/json"
    body = b"".join(
        (
            f"--{boundary}\r\n".encode(),
            (
                'Content-Disposition: form-data; name="auth_json"; '
                f'filename="{auth_json_path.name}"\r\nContent-Type: {content_type}\r\n\r\n'
            ).encode(),
            auth_json_path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        )
    )
    _request_json(
        "POST",
        f"{base_url}/api/accounts/import",
        raw_body=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        timeout=timeout,
    )


def _create_api_key(base_url: str, *, timeout: float) -> str:
    payload = {"name": f"alb-codex-e2e-{uuid4().hex[:8]}"}
    data = _request_json("POST", f"{base_url}/api/api-keys/", payload=payload, timeout=timeout)
    key = data.get("key")
    if not isinstance(key, str) or not key:
        raise SystemExit("API key creation did not return a key")
    return key


def _request_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    raw_body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float,
) -> dict[str, Any]:
    request_headers = {"Accept": "application/json", **(headers or {})}
    body = raw_body
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw_error = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"{method} {url} failed with HTTP {exc.code}: {_redact(raw_error)}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"{method} {url} failed: {exc.reason}") from exc
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{method} {url} did not return JSON: {_redact(raw)}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f"{method} {url} returned non-object JSON")
    return parsed


def _response_output_text(payload: dict[str, Any]) -> str:
    value = payload.get("output_text")
    if isinstance(value, str):
        return value
    output = payload.get("output")
    if not isinstance(output, list):
        return ""
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
    return "".join(parts)


def _redact(value: str) -> str:
    redacted = value
    for marker in ("accessToken", "refreshToken", "idToken", "api_key", "apiKey"):
        redacted = redacted.replace(marker, f"{marker[:3]}...")
    return redacted[:1000]


if __name__ == "__main__":
    sys.exit(main())
