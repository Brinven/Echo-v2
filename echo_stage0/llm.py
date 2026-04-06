"""
LLM wrapper for Echo.

Connects to LM Studio's OpenAI-compatible API at localhost:1234.
Auto-detects loaded models. Supports both blocking and streaming generation.
"""

import sys
import re
from collections.abc import Generator
from openai import OpenAI, APIConnectionError, APITimeoutError


SYSTEM_PROMPT = (
    "You are Echo, a helpful voice assistant. You are running locally on the "
    "user's PC. Keep responses conversational and concise -- you are speaking "
    "aloud, not writing. Avoid lists, bullet points, and markdown formatting. "
    "Prefer 2-4 sentences unless the user explicitly asks for more detail. "
    "When the user asks you to remember something, acknowledge it briefly "
    "and naturally in your response."
)
LM_STUDIO_URL = "http://127.0.0.1:1234/v1"
TIMEOUT_S = 30

# Sentence boundary: period, exclamation, or question mark followed by space or end
_SENTENCE_END = re.compile(r'[.!?](?:\s|$)')
_MIN_CHUNK_WORDS = 3
_MAX_BUFFER_TOKENS = 150  # flush if no sentence boundary found after this many tokens


class LLMClient:
    """LLM client for LM Studio via OpenAI-compatible API."""

    def __init__(self):
        self._client = OpenAI(
            base_url=LM_STUDIO_URL,
            api_key="not-needed",
            timeout=TIMEOUT_S,
        )
        self._model = None
        self._detect_model()

    def _detect_model(self):
        """Auto-detect available models from LM Studio."""
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

        if len(available) == 1:
            self._model = available[0]
        else:
            print("\n  Available models:")
            for i, name in enumerate(available, 1):
                print(f"    {i}. {name}")
            while True:
                choice = input(f"  Select model [1-{len(available)}]: ").strip()
                if choice.isdigit() and 1 <= int(choice) <= len(available):
                    self._model = available[int(choice) - 1]
                    break
                print("  Invalid choice, try again.")

        print(f"  LLM: {self._model} via LM Studio")

    def generate(self, user_text: str, history: list[dict] | None = None) -> str:
        """
        Send user text to LLM and return the full response (blocking).

        Args:
            user_text: The user's transcribed speech
            history: Optional conversation history (list of role/content dicts)

        Returns:
            LLM response text
        """
        messages = self._build_messages(user_text, history)
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                stream=False,
            )
            return response.choices[0].message.content.strip()

        except APITimeoutError:
            raise TimeoutError(
                f"LLM did not respond within {TIMEOUT_S}s. "
                "Check LM Studio -- the model may be too large for this hardware."
            )

    def stream_sentences(
        self, user_text: str, history: list[dict] | None = None,
        timing: dict | None = None,
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

        Yields:
            Sentence-sized text chunks
        """
        import time

        messages = self._build_messages(user_text, history)
        t_start = time.perf_counter()

        try:
            stream = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                stream=True,
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

    def _build_messages(self, user_text: str, history: list[dict] | None = None) -> list[dict]:
        """Build the messages list for a chat completion."""
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_text})
        return messages

    @property
    def model_name(self) -> str:
        return self._model
