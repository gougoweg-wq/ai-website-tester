"""Tiny AI client used by the tester.

Picks a provider automatically:
  1. ANTHROPIC_API_KEY  -> Claude (Anthropic Messages API)
  2. OPENAI_API_KEY     -> any OpenAI-compatible API (OpenAI, Gemini, Groq, OpenRouter, Ollama...)
                           set OPENAI_BASE_URL to use a non-OpenAI endpoint
  3. nothing set        -> free keyless endpoint at text.pollinations.ai (good enough for a demo)
Keys can live in a .env file in the project folder.
Responses are cached in .ai_cache/ so re-running the same test gives the same answer.
"""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".ai_cache"


def _load_dotenv():
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class AI:
    def __init__(self, enabled=True):
        _load_dotenv()
        self.enabled = enabled
        self.calls = 0
        if not enabled:
            self.provider, self.model = "none", None
        elif os.getenv("ANTHROPIC_API_KEY"):
            self.provider, self.model = "anthropic", os.getenv("AI_MODEL", "claude-opus-5-5")
        elif os.getenv("OPENAI_API_KEY"):
            self.provider, self.model = "openai", os.getenv("AI_MODEL", "gpt-4o-mini")
        else:
            self.provider, self.model = "pollinations", os.getenv("AI_MODEL", "openai")

    @property
    def label(self):
        return "disabled" if self.provider == "none" else f"{self.provider} ({self.model})"

    def _post(self, url, headers, body):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read().decode())

    def _call(self, system, user):
        if self.provider == "anthropic":
            data = self._post(
                "https://api.anthropic.com/v1/messages",
                {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
                {"model": self.model, "max_tokens": 4000, "system": system,
                 "messages": [{"role": "user", "content": user}]})
            return "".join(b.get("text", "") for b in data["content"])
        if self.provider == "openai":
            base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
            url, headers = base + "/chat/completions", {
                "Authorization": "Bearer " + os.environ["OPENAI_API_KEY"], "Content-Type": "application/json"}
        else:
            url, headers = "https://text.pollinations.ai/openai", {"Content-Type": "application/json"}
        data = self._post(url, headers, {"model": self.model, "messages": [
            {"role": "system", "content": system}, {"role": "user", "content": user}]})
        return data["choices"][0]["message"]["content"]

    def ask_json(self, system, user):
        """Send a prompt and return parsed JSON, or None if AI is off / failed."""
        if not self.enabled:
            return None
        key = hashlib.sha256(f"{self.provider}|{self.model}|{system}|{user}".encode()).hexdigest()[:24]
        cached = CACHE / f"{key}.json"
        if cached.exists():
            return json.loads(cached.read_text())
        system += "\nAnswer with valid JSON only. No markdown, no commentary."
        for attempt in range(6):
            try:
                self.calls += 1
                if self.provider == "pollinations":
                    time.sleep(10)  # the free endpoint allows only a few requests per minute
                result = parse_json(self._call(system, user))
                if result is not None:
                    CACHE.mkdir(exist_ok=True)
                    cached.write_text(json.dumps(result, indent=2))
                    return result
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError, TypeError) as e:
                print(f"   [AI] attempt {attempt + 1} failed: {e} — retrying")
            time.sleep(15 * (attempt + 1))  # free endpoints rate-limit; wait and retry
        return None


def parse_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    for candidate in (text, _between(text, "{", "}"), _between(text, "[", "]")):
        if candidate:
            try:
                return json.loads(candidate)
            except ValueError:
                pass
    return None


def _between(text, open_ch, close_ch):
    a, b = text.find(open_ch), text.rfind(close_ch)
    return text[a:b + 1] if a != -1 and b > a else None
