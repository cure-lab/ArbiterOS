from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, dict):
            rows.append(item)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def extract_last_json(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for i in range(len(text) - 1, -1, -1):
        if text[i] != "{":
            continue
        try:
            obj, end = decoder.raw_decode(text[i:])
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if text[i + end :].strip():
            continue
        return obj
    return None


def parse_llm_json_text(text: str) -> dict[str, Any] | None:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        obj = extract_last_json(text)
    return obj if isinstance(obj, dict) else None


def load_llm_config(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    data = read_json(path)
    if not isinstance(data, dict) or not data.get("enabled"):
        return None
    required = ("api_url", "api_key", "model")
    if not all(isinstance(data.get(key), str) and str(data[key]).strip() for key in required):
        return None
    return data


def extract_message_text(message: Any) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        out: list[str] = []
        for item in message:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                out.append(item["text"])
        return "".join(out)
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
    return ""


def call_llm_json(
    *,
    config: dict[str, Any],
    messages: list[dict[str, str]],
    temperature: float = 0,
) -> dict[str, Any]:
    payload = {
        "model": config["model"],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }
    req = urllib.request.Request(
        str(config["api_url"]),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config['api_key']}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=float(config.get("timeout_s", 60))) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "error": f"http_{exc.code}", "response_text": body, "response_json": parse_llm_json_text(body)}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": f"url_error:{exc.reason}", "response_text": "", "response_json": None}

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {"ok": False, "error": "invalid_provider_json", "response_text": body, "response_json": None}

    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices:
        return {"ok": False, "error": "missing_choices", "response_text": body, "response_json": None}
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content_text = extract_message_text(message)
    content_json = parse_llm_json_text(content_text)
    if content_json is None:
        return {"ok": False, "error": "model_response_not_json", "response_text": content_text or body, "response_json": None}
    return {"ok": True, "error": None, "response_text": content_text, "response_json": content_json, "provider_response": parsed}
