"""
Offline tests for visual input Level 1 — the llm.py message seam. No model, no LM Studio.

Covers:
  - image_content: the single source of the OpenAI content-array wire format.
  - collapse_image_history: keep-latest-photo — only USER list-content entries flatten,
    text survives, assistant entries untouched, idempotent.
  - _build_messages: no-image path byte-identical to pre-vision; image path puts the
    content array on the FINAL user message only; history passes through untouched.
  - supports_vision: type=="vlm" from the native endpoint, fail-soft True on error/no
    model, ~10s TTL cache (second call must not re-probe), cache keyed by model id.

Run:  python test_vision.py     (exit 0 = all assertions passed)
"""

import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import llm as llm_mod
from llm import (
    LLMClient, IMAGE_COLLAPSED_NOTE, image_content, collapse_image_history,
)


def _bare_client(model="test/model-x") -> LLMClient:
    """An LLMClient without __init__ — offline tests must never touch LM Studio."""
    client = object.__new__(LLMClient)
    client._model = model
    client._sampler = dict(llm_mod._DEFAULT_SAMPLER)
    client._vision_cache = None
    return client


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def run() -> None:
    print("\n── Vision: image_content (offline) ──")

    content = image_content("what is this?", "QUJD", "image/png")
    assert content[0] == {"type": "text", "text": "what is this?"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "data:image/png;base64,QUJD"
    assert image_content("x", "QUJD")[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    print("  [PASS] text part + data-URI image part; mime defaults to jpeg")

    print("\n── Vision: collapse_image_history (offline) ──")

    history = [
        {"role": "user", "content": "plain turn"},
        {"role": "user", "content": image_content("[Michael] look at this", "QUJD")},
        {"role": "assistant", "content": "That's a fine plant."},
    ]
    n = collapse_image_history(history)
    assert n == 1
    assert history[0]["content"] == "plain turn"
    assert history[1]["content"] == f"[Michael] look at this {IMAGE_COLLAPSED_NOTE}"
    assert history[2]["content"] == "That's a fine plant."
    print("  [PASS] only the user list-content entry flattens; text + placeholder survive")

    assert collapse_image_history(history) == 0, "collapse must be idempotent"
    print("  [PASS] idempotent — a second pass touches nothing")

    # An assistant entry with list content (shouldn't exist, but must not be eaten).
    weird = [{"role": "assistant", "content": [{"type": "text", "text": "hi"}]}]
    assert collapse_image_history(weird) == 0 and isinstance(weird[0]["content"], list)
    print("  [PASS] assistant entries are never collapsed, even with list content")

    print("\n── Vision: _build_messages (offline) ──")

    client = _bare_client()
    hist = [{"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "reply"}]
    plain = client._build_messages("hello", hist, "SYS")
    assert plain == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "hello"},
    ]
    print("  [PASS] no-image path is byte-identical to the pre-vision shape")

    with_img = client._build_messages("hello", hist, "SYS", image_b64="QUJD")
    assert with_img[:3] == plain[:3], "history must pass through untouched"
    final = with_img[3]
    assert final["role"] == "user" and isinstance(final["content"], list)
    assert final["content"][0] == {"type": "text", "text": "hello"}
    assert final["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,QUJD"
    print("  [PASS] image_b64 puts the content array on the final user message only")

    photo_hist = [{"role": "user", "content": image_content("old photo turn", "T0xE")}]
    mixed = client._build_messages("new turn", photo_hist, "SYS")
    assert isinstance(mixed[1]["content"], list), "an earlier photo turn must survive as-is"
    assert mixed[2] == {"role": "user", "content": "new turn"}
    print("  [PASS] an earlier photo turn in history rides through untouched")

    print("\n── Vision: supports_vision (offline, monkeypatched httpx) ──")

    real_get = llm_mod.httpx.get
    calls = {"n": 0}
    try:
        def fake_get(url, timeout=None):
            calls["n"] += 1
            return _FakeResponse({"data": [
                {"id": "test/model-x", "type": "vlm"},
                {"id": "text-only", "type": "llm"},
            ]})
        llm_mod.httpx.get = fake_get

        client = _bare_client("test/model-x")
        assert client.supports_vision() is True
        assert client.supports_vision() is True and calls["n"] == 1, \
            "second call within the TTL must not re-probe"
        print("  [PASS] vlm → True; TTL cache holds (one probe for two calls)")

        client._model = "text-only"
        assert client.supports_vision() is False and calls["n"] == 2, \
            "a model change must invalidate the cache"
        assert client.supports_vision() is False and calls["n"] == 2
        print("  [PASS] llm → False; cache is keyed by model id; False is cached too")

        client._model = "not-in-the-list"
        assert client.supports_vision() is True
        print("  [PASS] model missing from the listing → True (LM Studio stays the authority)")

        def boom(url, timeout=None):
            calls["n"] += 1
            raise llm_mod.httpx.ConnectError("down")
        llm_mod.httpx.get = boom
        client = _bare_client("test/model-x")
        assert client.supports_vision() is True
        probes = calls["n"]
        assert client.supports_vision() is True and calls["n"] == probes, \
            "errors must be cached — a down LM Studio costs one probe per TTL"
        print("  [PASS] probe error → True, and the error result is cached (no hammering)")

        # Expire the cache by hand: the next call probes again.
        client._vision_cache = (client._vision_cache[0], client._vision_cache[1],
                                time.monotonic() - LLMClient._VISION_TTL_S - 1)
        client.supports_vision()
        assert calls["n"] == probes + 1
        print("  [PASS] an expired cache entry re-probes")

        client._model = None
        assert client.supports_vision() is True
        print("  [PASS] no model selected → True (nothing to grey out yet)")
    finally:
        llm_mod.httpx.get = real_get

    print("\n  All vision seam checks passed.\n")


if __name__ == "__main__":
    run()
