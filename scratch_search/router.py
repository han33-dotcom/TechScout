"""All inference goes directly to OpenRouter. Prompts and outputs stay in the run trace."""
import asyncio
import json
import os
import time
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def load_key(path=Path(".env")):
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key and path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "OPENROUTER_API_KEY":
                key = value.strip().strip("\"'")
                break
    if not key:
        raise ValueError("Set OPENROUTER_API_KEY in the environment or the worktree .env file")
    return key


class Router:
    def __init__(self, model, max_calls=24):
        self.model = model
        self.key = load_key()
        self.max_calls = max_calls
        self.calls = []

    async def ask(self, role, instructions, data):
        if len(self.calls) >= self.max_calls:
            raise RuntimeError("model call allowance reached")
        entry = {"role": role, "model": self.model, "cost_usd": None, "status": "started"}
        self.calls.append(entry)
        start = time.monotonic()
        try:
            packet = await asyncio.to_thread(self._post, instructions, data)
            usage = packet.get("usage") or {}
            entry.update(provider=packet.get("provider"), response_id=packet.get("id"),
                         prompt_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"),
                         cost_usd=str(usage["cost"]) if usage.get("cost") is not None else None)
            answer = json.loads(packet["choices"][0]["message"]["content"])
            if not isinstance(answer, dict):
                raise ValueError("expected a JSON object")
            entry.update(status="ok", decision=answer)
            return answer
        except Exception as exc:
            entry.update(status="failed", error=type(exc).__name__)
            raise
        finally:
            entry["seconds"] = round(time.monotonic() - start, 3)

    def _post(self, instructions, data):
        payload = {"model": self.model, "max_tokens": 2200, "temperature": 0,
                   "response_format": {"type": "json_object"}, "provider": {"require_parameters": True},
                   "messages": [{"role": "system", "content": instructions},
                                {"role": "user", "content": json.dumps(data, ensure_ascii=False)}]}
        request = Request("https://openrouter.ai/api/v1/chat/completions",
                          data=json.dumps(payload).encode(),
                          headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=65) as response:
                return json.load(response, parse_float=Decimal)
        except HTTPError as exc:
            raise RuntimeError(f"OpenRouter returned HTTP {exc.code}") from None
