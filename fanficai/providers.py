"""Pluggable text-generation backends.

Selection order (first that works wins):
  1. --provider flag
  2. ANTHROPIC_API_KEY  -> anthropic
  3. OPENAI_API_KEY     -> openai
  4. mock               -> offline template generator, no network, no key

The mock backend exists so the prototype is demonstrable and testable with
zero setup or spend.
"""

from __future__ import annotations

import json
import os
import random
import re
import urllib.error
import urllib.request

TIMEOUT = 120


class ProviderError(Exception):
    pass


class Provider:
    name = "base"

    def __init__(self) -> None:
        # tokens used by the most recent generate() call
        self.last_usage: dict[str, int] = {"in": 0, "out": 0}

    def generate(self, system: str, prompt: str, max_tokens: int = 2000) -> str:
        raise NotImplementedError

    def last_cost(self) -> float:
        """Estimate cost only when the user supplies current provider rates."""
        rate_in, rate_out = configured_price_per_mtok()
        u = self.last_usage
        return (u["in"] * rate_in + u["out"] * rate_out) / 1_000_000


def configured_price_per_mtok() -> tuple[float, float]:
    """Read optional USD-per-million-token rates without embedding stale prices."""
    try:
        return (
            float(os.environ.get("FANFICAI_INPUT_USD_PER_MTOK", "0") or 0),
            float(os.environ.get("FANFICAI_OUTPUT_USD_PER_MTOK", "0") or 0),
        )
    except ValueError:
        return (0.0, 0.0)


# --------------------------------------------------------------------------
# Hosted providers (stdlib HTTP only, no dependencies)
# --------------------------------------------------------------------------
class AnthropicProvider(Provider):
    name = "anthropic"
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        super().__init__()
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise ProviderError("ANTHROPIC_API_KEY is not set")
        self.model = model or os.environ.get(
            "FANFICAI_MODEL", "claude-sonnet-4-20250514"
        )

    def generate(self, system: str, prompt: str, max_tokens: int = 2000) -> str:
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        req = urllib.request.Request(
            self.URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        data = _post(req)
        usage = data.get("usage", {})
        self.last_usage = {
            "in": usage.get("input_tokens", 0),
            "out": usage.get("output_tokens", 0),
        }
        return "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        ).strip()


class OpenAIProvider(Provider):
    name = "openai"
    URL = "https://api.openai.com/v1/responses"

    def __init__(self, model: str | None = None, api_key: str | None = None):
        super().__init__()
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not self.api_key:
            raise ProviderError("OPENAI_API_KEY is not set")
        self.model = model or os.environ.get("FANFICAI_MODEL", "gpt-5.6-luna")

    def generate(self, system: str, prompt: str, max_tokens: int = 2000) -> str:
        body = {
            "model": self.model,
            "max_output_tokens": max_tokens,
            "instructions": system,
            "input": prompt,
        }
        req = urllib.request.Request(
            self.URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {self.api_key}",
            },
        )
        data = _post(req)
        usage = data.get("usage", {})
        self.last_usage = {
            "in": usage.get("input_tokens", 0),
            "out": usage.get("output_tokens", 0),
        }
        parts = []
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    parts.append(content.get("text", ""))
        result = "".join(parts).strip()
        if not result:
            raise ProviderError("OpenAI returned no text output")
        return result


class OllamaProvider(Provider):
    name = "ollama"

    def __init__(self, model: str | None = None, base_url: str | None = None):
        super().__init__()
        self.model = model or os.environ.get("FANFICAI_MODEL", "llama3.2")
        root = base_url or os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        self.url = f"{root.rstrip('/')}/api/generate"

    def generate(self, system: str, prompt: str, max_tokens: int = 2000) -> str:
        body = {
            "model": self.model,
            "system": system,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        req = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode("utf-8"),
            headers={"content-type": "application/json"},
        )
        data = _post(req)
        self.last_usage = {
            "in": data.get("prompt_eval_count", 0),
            "out": data.get("eval_count", 0),
        }
        return data.get("response", "").strip()


def _post(req: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ProviderError(_explain(e)) from None
    except urllib.error.URLError as e:
        raise ProviderError(f"network error: {e.reason}") from None


def _explain(e: urllib.error.HTTPError) -> str:
    """Turn an API error into something the user can act on."""
    raw = e.read().decode("utf-8", "replace")
    try:
        err = json.loads(raw).get("error", {})
        msg = err.get("message", raw)[:300]
        code = err.get("code") or err.get("type") or ""
    except (ValueError, AttributeError):
        msg, code = raw[:300], ""

    hints = {
        "insufficient_quota":
            "the API key is valid but the account has no usable credit. Add "
            "billing credit, or run with --provider mock to keep working offline.",
        "invalid_api_key":
            "the API key was rejected. Check it was copied whole and has not "
            "been revoked.",
        "model_not_found":
            "that model is not available to this account. Try --model gpt-4o-mini "
            "or set FANFICAI_MODEL.",
    }
    if e.code == 429 and code != "insufficient_quota":
        return f"HTTP 429 rate limited: {msg} Wait a moment and retry."
    hint = hints.get(code) or hints.get("invalid_api_key" if e.code == 401 else "")
    return f"HTTP {e.code} {code}: {msg}" + (f"\n  -> {hint}" if hint else "")


# --------------------------------------------------------------------------
# Offline mock
# --------------------------------------------------------------------------
class MockProvider(Provider):
    """Deterministic template generator. Produces structurally correct output
    (parseable outlines, prose of roughly the right length) without a model,
    so the pipeline can be demoed and unit-tested offline."""

    name = "mock"

    OPENERS = [
        "{who} had learned to read a room by its silences, and this one was screaming.",
        "The door gave on the third shove, and {who} went through it sideways.",
        "{who} counted three exits before anyone said hello.",
        "It started, as these things do, with a message {who} should not have opened.",
    ]
    MIDDLES = [
        "Somewhere below, the building settled with a sound like a held breath.",
        "{who} turned the thought over the way you test a loose tooth.",
        "Nothing in the plan had accounted for this, which was becoming the plan's defining feature.",
        "The light through the window moved a hand's width and neither of them filled the quiet.",
        "\"You could have told me,\" {who} said, and hated how steady it came out.",
        "\"I know,\" came the answer, too fast to be true.",
        "There was a version of this where {who} walked away. {who} could see it, clean as a photograph.",
    ]
    CLOSERS = [
        "Whatever came next, {who} was not going to meet it sitting down.",
        "{who} said the name out loud, and the room changed temperature.",
        "The message was still on the screen when the lights went out.",
    ]

    def __init__(self, seed: int | None = None):
        super().__init__()
        self.model = "mock"
        self.rng = random.Random(seed if seed is not None else 7)

    def generate(self, system: str, prompt: str, max_tokens: int = 2000) -> str:
        if "Draft a" in prompt and "chapter outline" in prompt:
            return self._outline(prompt)
        if "Give the author craft feedback" in prompt:
            return self._critique(prompt)
        return self._prose(prompt)

    # -- helpers ---------------------------------------------------------
    def _n_chapters(self, prompt: str) -> int:
        m = re.search(r"Draft a (\d+)-chapter", prompt)
        return int(m.group(1)) if m else 3

    def _cast(self, prompt: str) -> list[str]:
        names = re.findall(r"^  - ([^(|]+?) \(", prompt, re.M)
        return [n.strip() for n in names] or ["the narrator"]

    def _target_words(self, prompt: str) -> int:
        m = re.search(r"about (\d+) (?:more )?words", prompt)
        return int(m.group(1)) if m else 400

    def _outline(self, prompt: str) -> str:
        n = self._n_chapters(prompt)
        cast = self._cast(prompt)
        shapes = [
            ("Small Hours", "An ordinary night cracks open and the status quo stops holding."),
            ("The Favour", "A request that cannot be refused drags the cast into contact."),
            ("Tell Me Again", "Old history resurfaces; someone lies by omission."),
            ("Fault Lines", "The alliance strains under pressure it was not built for."),
            ("What It Costs", "A choice is made that cannot be walked back."),
            ("After", "The dust settles into something neither of them expected to want."),
        ]
        out = []
        for i in range(1, n + 1):
            title, summary = shapes[(i - 1) % len(shapes)]
            pov = cast[(i - 1) % len(cast)]
            out.append(
                f"## {i}. {title}\n"
                f"POV: {pov}\n"
                f"SUMMARY: {summary}\n"
                f"- {pov} is pulled into the situation against better judgement\n"
                f"- a conversation goes wrong in a way that reveals character\n"
                f"- the chapter ends on a decision or a door closing"
            )
        return "\n\n".join(out)

    def _prose(self, prompt: str) -> str:
        cast = self._cast(prompt)
        m = re.search(r"POV CHARACTER: (.+)", prompt)
        who = m.group(1).strip() if m else cast[0]
        beats = re.findall(r"^- (.+)$", prompt, re.M)
        target = self._target_words(prompt)

        pool: list[str] = []

        def next_middle() -> str:
            # draw without replacement so filler never repeats back to back
            nonlocal pool
            if not pool:
                pool = list(self.MIDDLES)
                self.rng.shuffle(pool)
            return pool.pop().format(who=who)

        paras: list[str] = [self.rng.choice(self.OPENERS).format(who=who)]
        for beat in beats:
            paras.append(f"{beat.rstrip('.').capitalize()}. " + next_middle())
        while len(" ".join(paras).split()) < target:
            paras.append(next_middle())
        paras.append(self.rng.choice(self.CLOSERS).format(who=who))
        return "\n\n".join(paras)

    def _critique(self, prompt: str) -> str:
        return (
            "1. What is working\n"
            "- The POV stays anchored in one head; no head-hopping.\n"
            "- Dialogue does more than transfer information.\n\n"
            "2. Characterisation drift: none spotted against the bible.\n\n"
            "3. Pacing: the middle third repeats a beat. Cut one exchange.\n\n"
            "4. Prose habits: filter verbs ('she felt', 'he noticed') hold the\n"
            "   reader at arm's length.\n"
            "   Before: She felt a cold wash of fear.\n"
            "   After:  The cold went through her before she named it.\n\n"
            "5. Highest-value next change: give the POV character one concrete\n"
            "   physical want in the scene so the reader has something to track.\n"
            "\n[mock backend: set ANTHROPIC_API_KEY or OPENAI_API_KEY for real critique]"
        )


# --------------------------------------------------------------------------
def get_provider(name: str | None = None, model: str | None = None) -> Provider:
    name = (name or os.environ.get("FANFICAI_PROVIDER") or "auto").lower()

    if name == "mock":
        return MockProvider()
    if name == "anthropic":
        return AnthropicProvider(model)
    if name == "openai":
        return OpenAIProvider(model)
    if name == "ollama":
        return OllamaProvider(model)
    if name != "auto":
        raise ProviderError(f"unknown provider {name!r}")

    for cls in (AnthropicProvider, OpenAIProvider):
        try:
            return cls(model)
        except ProviderError:
            continue
    return MockProvider()
