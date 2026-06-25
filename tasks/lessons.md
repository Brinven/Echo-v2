# Echo — Lessons Learned

## 2026-04-05: Windows `localhost` adds ~2s to every HTTP request

**Problem**: LLM TTFT was 2.3s in the pipeline but 0.2s in standalone tests.
Traced to `client.chat.completions.create()` blocking for 2.05s before returning.

**Root cause**: On Windows, `localhost` resolves to both `::1` (IPv6) and
`127.0.0.1` (IPv4). The `httpx` library (used by the `openai` Python client)
tries IPv6 first, which times out after ~2s when the server only listens on
IPv4, then falls back. Standalone tests were fast because httpx's connection
pool reused the successful IPv4 connection between rapid calls. In the real
pipeline, connections expired between calls.

**Fix**: Use `127.0.0.1` instead of `localhost` in all HTTP URLs.

**Rule**: On Windows, ALWAYS use `127.0.0.1` for local service URLs, never
`localhost`. This applies to LM Studio, Kokoro-FastAPI, and any other local
API server.

**Impact**: First-audio dropped from 5.3s to 0.8s (6.5x improvement).

## 2026-06-24: Gemma 4 12B QAT is a thinking model — gate returned empty output

**Problem**: The Ib-Lite significance gate returned empty `content` for fact/preference
turns (json_parse_failed) while correctly returning `{"save": false}` for smalltalk. The
empty ones also took longer (4.0s vs 3.2s).

**Root cause**: `gemma-4-12b-it-qat@q4_k_xl` in LM Studio is a *thinking* model. It emitted
all its output into `reasoning_content` (397 reasoning tokens), hit `max_tokens=150`
(finish_reason=length), and produced an EMPTY `content` — the intended JSON never made it
out of the reasoning channel. Smalltalk happened to fit a short answer.

**Fix**: Pass `reasoning_effort="none"` on the gate completion. Verified the alternatives
do NOT work for this template: `reasoning_effort="low"` still burned 147 reasoning tokens;
`chat_template_kwargs.enable_thinking=false` likewise; raising `max_tokens=700` just burned
697 reasoning tokens in 16s without finishing. Only `"none"` disabled it → clean JSON in ~1s.

**Rule**: When calling a local model for STRUCTURED output (JSON gates, extractors), assume
it may be a thinking model and explicitly disable reasoning with `reasoning_effort="none"`.
Check `finish_reason` and `reasoning_tokens` when `content` comes back empty. (Cross-project
LM Studio fact — also retained to `axly-infra`.)

**Prevention**: Any future structured-output call against an unknown LM Studio model should
default to `reasoning_effort="none"` and validate the JSON, rather than trusting `content`.
