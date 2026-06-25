"""
LLM wrapper for Echo.

Connects to LM Studio's OpenAI-compatible API at localhost:1234.
Auto-detects loaded models. Supports both blocking and streaming generation.
"""

import os
import sys
import re
import json
import logging
from pathlib import Path
from collections.abc import Generator
from openai import OpenAI, APIConnectionError, APITimeoutError

logger = logging.getLogger(__name__)


def _resolve_pin(pin: str, available: list[str]) -> tuple[str | None, list[str]]:
    """Resolve a model pin (exact id, or case-insensitive substring) against the live list.

    Returns (resolved_id_or_None, substring_matches). An exact id wins outright. Otherwise a
    SINGLE substring match resolves; multiple matches return None plus the list so the caller
    can drop the user into the picker pre-filtered.
    """
    if pin in available:
        return pin, [pin]
    low = pin.lower()
    matches = [m for m in available if low in m.lower()]
    if len(matches) == 1:
        return matches[0], matches
    return None, matches


# Last-resort fallback only. From Stage 5 Part 2, the per-turn system prompt is
# assembled by persona.build_system_prompt() (persona block + Ib-Lite memory) and
# always passed in. This generic prompt is used only if no system_prompt is provided
# (e.g. a bare LLMClient call in a test).
DEFAULT_SYSTEM_PROMPT = (
    "You are Echo, a helpful voice assistant running locally on Michael's PC. "
    "Keep responses conversational and concise -- you are speaking aloud, "
    "not writing. Avoid lists, bullet points, and markdown formatting. "
    "Prefer 2-4 sentences unless Michael explicitly asks for more detail. "
    "When Michael asks you to remember something, acknowledge it briefly "
    "and naturally in your response. "
    "Use any personal context you have about Michael naturally, the way a "
    "close friend would -- without announcing that you remember it, without "
    "saying \"I remember\" or \"last time we spoke\". Simply know it and let "
    "it inform how you talk to him."
)
LM_STUDIO_URL = "http://127.0.0.1:1234/v1"
TIMEOUT_S = 30

# Character-pass sampler (PRD Stage 5 Part 2 §7). Loaded from echo_sampler.json at
# startup; these are the fail-soft defaults if the file is missing or corrupt. The
# significance gate (significance.py) is a SEPARATE call with its own temperature —
# these never touch it.
_SAMPLER_PATH = Path(__file__).resolve().parent / "echo_sampler.json"
_DEFAULT_SAMPLER = {
    "temperature": 0.72,
    "top_p": 0.90,
    "top_k": 40,
    "repeat_penalty": 1.08,
    "max_tokens": 300,
}


def _load_sampler() -> dict:
    """Load echo_sampler.json, falling back to documented defaults on any error."""
    sampler = dict(_DEFAULT_SAMPLER)
    try:
        with open(_SAMPLER_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in _DEFAULT_SAMPLER:
            if key in data:
                sampler[key] = data[key]
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"echo_sampler.json unreadable ({e}); using built-in sampler defaults")
    return sampler

# Sentence boundary: period, exclamation, or question mark followed by space or end
_SENTENCE_END = re.compile(r'[.!?](?:\s|$)')
_MIN_CHUNK_WORDS = 3
_MAX_BUFFER_TOKENS = 150  # flush if no sentence boundary found after this many tokens


class LLMClient:
    """LLM client for LM Studio via OpenAI-compatible API."""

    _PICK_DISPLAY_CAP = 30  # don't dump the whole (huge) model list unfiltered

    def __init__(self, pinned: str | None = None, last_model: str | None = None):
        self._client = OpenAI(
            base_url=LM_STUDIO_URL,
            api_key="not-needed",
            timeout=TIMEOUT_S,
        )
        self._model = None
        self._sampler = _load_sampler()
        self._detect_model(pinned=pinned, last_model=last_model)
        print(
            f"  Sampler: temp {self._sampler['temperature']}, top_p {self._sampler['top_p']}, "
            f"top_k {self._sampler['top_k']}, repeat {self._sampler['repeat_penalty']}, "
            f"max_tokens {self._sampler['max_tokens']} (reasoning off)"
        )

    def _completion_kwargs(self, messages: list[dict], stream: bool) -> dict:
        """Build chat.completions.create kwargs for the character pass.

        Applies the echo_sampler.json baseline and disables the model's thinking
        (reasoning_effort="none"). CoT isolation (PRD §6): Echo's character pass never
        reasons inline — any real reasoning is a separate call. Disabling thinking here
        also keeps TTFT low (Gemma 4 12B QAT is a thinking model; left on it burns a
        silent reasoning preamble before the first spoken token).

        top_k / repeat_penalty are not OpenAI-standard params; LM Studio accepts them
        via extra_body passthrough (verified against LM Studio's documented
        /v1/chat/completions payload params).
        """
        s = self._sampler
        return {
            "model": self._model,
            "messages": messages,
            "temperature": s["temperature"],
            "top_p": s["top_p"],
            "max_tokens": s["max_tokens"],
            "reasoning_effort": "none",
            "extra_body": {"top_k": s["top_k"], "repeat_penalty": s["repeat_penalty"]},
            "stream": stream,
        }

    def _detect_model(self, pinned: str | None = None, last_model: str | None = None):
        """Select the model at startup.

        Resolution order:
          1. A pin (the `pinned` arg, else the ECHO_MODEL env var) — name or substring.
             A unique match is used silently; an ambiguous one opens the picker pre-filtered.
          2. Exactly one model loaded → use it.
          3. Otherwise → interactive filter-picker, defaulting to `last_model` on Enter.
        """
        try:
            models = self._client.models.list()
            available = [m.id for m in models.data]
        except APIConnectionError:
            print(
                "\n ERROR: LM Studio not detected at localhost:1234.\n"
                "  Please start LM Studio and load a model.\n"
            )
            sys.exit(1)

        if not available:
            print(
                "\n ERROR: LM Studio is running but no models are loaded.\n"
                "  Please load a model in LM Studio.\n"
            )
            sys.exit(1)

        last = last_model if (last_model and last_model in available) else None
        pin = pinned or os.environ.get("ECHO_MODEL")

        chosen: str | None = None
        if pin:
            resolved, matches = _resolve_pin(pin, available)
            if resolved:
                self._model = resolved
                print(f"  LLM: {self._model} via LM Studio (pinned '{pin}')")
                return
            if matches:
                print(f"\n  '{pin}' matches {len(matches)} models — narrow it down:")
                chosen = self._pick_interactive(available, last, initial_filter=pin)
            else:
                print(f"\n  No model matches '{pin}'. Pick from the full list:")
                chosen = self._pick_interactive(available, last)
        elif len(available) == 1:
            self._model = available[0]
            print(f"  LLM: {self._model} via LM Studio")
            return
        else:
            chosen = self._pick_interactive(available, last)

        # The picker only returns None on explicit 'cancel'; at startup we must end with a model.
        while chosen is None:
            print("  (a model is required to start)")
            chosen = self._pick_interactive(available, last)
        self._model = chosen
        print(f"  LLM: {self._model} via LM Studio")

    def _pick_interactive(
        self, available: list[str], last_model: str | None = None, initial_filter: str = "",
    ) -> str | None:
        """Filter-picker REPL. Type a substring to narrow, a number to pick, Enter to reuse
        `last_model`, or 'cancel' to abort (returns None). The current/last model is marked '*'.
        """
        flt = initial_filter.strip()
        while True:
            matches = [m for m in available if flt.lower() in m.lower()] if flt else available
            if not matches:
                print(f"  (no model matches '{flt}' — filter cleared)")
                flt = ""
                continue

            if not flt and len(matches) > self._PICK_DISPLAY_CAP:
                print(f"  {len(matches)} models loaded — type a filter to narrow "
                      f"(e.g. 'qat', '12b', 'e4b', 'heretic').")
            else:
                print(f"  Models matching '{flt}':" if flt else "  Available models:")
                for i, name in enumerate(matches, 1):
                    mark = "*" if name == last_model else " "
                    print(f"   {mark}{i:3}. {name}")

            hint = []
            if last_model:
                hint.append(f"Enter=last({last_model})")
            hint += ["number=pick", "text=filter", "cancel=abort"]
            choice = input(f"  [{' | '.join(hint)}] > ").strip()

            if not choice:
                if last_model:
                    return last_model
                if len(matches) == 1:
                    return matches[0]
                print("  Type a filter or a number.")
                continue
            if choice.lower() == "cancel":
                return None
            if choice.isdigit():
                idx = int(choice)
                if 1 <= idx <= len(matches):
                    return matches[idx - 1]
                print(f"  Out of range (1-{len(matches)}).")
                continue
            flt = choice  # anything else is a new filter

    def set_model(self, name: str) -> None:
        """Swap the active model in place (mid-chat hot-swap). The gate is swapped separately."""
        self._model = name

    def list_models(self) -> list[str]:
        """Live model ids from LM Studio (empty list on error)."""
        try:
            return [m.id for m in self._client.models.list().data]
        except Exception as e:
            logger.error(f"could not list models: {e}")
            return []

    def pick_model_interactive(self) -> str | None:
        """Re-query LM Studio and run the filter-picker (current model offered as 'last').
        Returns the chosen id, or None if cancelled / nothing available."""
        available = self.list_models()
        if not available:
            print("  [No models available from LM Studio]")
            return None
        return self._pick_interactive(available, last_model=self._model)

    def generate(
        self, user_text: str, history: list[dict] | None = None,
        system_prompt: str | None = None,
    ) -> str:
        """
        Send user text to LLM and return the full response (blocking).

        Args:
            user_text: The user's transcribed speech
            history: Optional conversation history (list of role/content dicts)
            system_prompt: Optional system prompt override (default: DEFAULT_SYSTEM_PROMPT)

        Returns:
            LLM response text
        """
        messages = self._build_messages(user_text, history, system_prompt)
        try:
            response = self._client.chat.completions.create(
                **self._completion_kwargs(messages, stream=False)
            )
            choice = response.choices[0]
            content = (choice.message.content or "").strip()
            # Empty-content guard: with reasoning disabled this should never be empty.
            # If it is (finish_reason=length => the model still burned its budget on
            # reasoning_content), surface it loudly rather than returning a silent "".
            if not content:
                logger.warning(
                    f"LLM returned empty content (finish_reason="
                    f"{getattr(choice, 'finish_reason', '?')}). Reasoning may not be disabled."
                )
            return content

        except APITimeoutError:
            raise TimeoutError(
                f"LLM did not respond within {TIMEOUT_S}s. "
                "Check LM Studio -- the model may be too large for this hardware."
            )

    def stream_sentences(
        self, user_text: str, history: list[dict] | None = None,
        timing: dict | None = None, system_prompt: str | None = None,
    ) -> Generator[str, None, None]:
        """
        Stream LLM response and yield complete sentences.

        Yields text chunks at sentence boundaries (. ! ?) with a minimum
        word count to avoid tiny fragments. Flushes remaining text at
        end of stream.

        Args:
            user_text: The user's transcribed speech
            history: Optional conversation history
            timing: Optional dict — will be populated with 'ttft' (time to first token)
            system_prompt: Optional system prompt override (default: DEFAULT_SYSTEM_PROMPT)

        Yields:
            Sentence-sized text chunks
        """
        import time

        messages = self._build_messages(user_text, history, system_prompt)
        t_start = time.perf_counter()

        try:
            stream = self._client.chat.completions.create(
                **self._completion_kwargs(messages, stream=True)
            )
        except APITimeoutError:
            raise TimeoutError(
                f"LLM did not respond within {TIMEOUT_S}s. "
                "Check LM Studio -- the model may be too large for this hardware."
            )

        buffer = ""
        token_count = 0
        first_token_seen = False

        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content is None:
                continue

            if not first_token_seen:
                first_token_seen = True
                if timing is not None:
                    timing["ttft"] = time.perf_counter() - t_start

            buffer += delta.content
            token_count += 1

            # Look for sentence boundary with enough words accumulated
            match = _SENTENCE_END.search(buffer)
            if match:
                end_pos = match.end()
                candidate = buffer[:end_pos].strip()
                word_count = len(candidate.split())

                if word_count >= _MIN_CHUNK_WORDS:
                    yield candidate
                    buffer = buffer[end_pos:].lstrip()
                    token_count = 0
                    continue

            # Safety flush: if buffer is very long with no sentence boundary
            if token_count >= _MAX_BUFFER_TOKENS and buffer.strip():
                yield buffer.strip()
                buffer = ""
                token_count = 0

        # Flush remaining text
        if buffer.strip():
            yield buffer.strip()

        # Empty-content guard: no content delta ever arrived. With reasoning disabled
        # this should not happen; if it does the model likely emitted only
        # reasoning_content (thinking not actually off for this template).
        if not first_token_seen:
            logger.warning(
                "LLM stream produced no content tokens. Reasoning may not be disabled."
            )

    def _build_messages(
        self, user_text: str, history: list[dict] | None = None,
        system_prompt: str | None = None,
    ) -> list[dict]:
        """Build the messages list for a chat completion."""
        messages = [{"role": "system", "content": system_prompt or DEFAULT_SYSTEM_PROMPT}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_text})
        return messages

    @property
    def model_name(self) -> str:
        return self._model
