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

## 2026-07-13: "Not loading" — shared global Python lost the whole voice-pipeline stack

**Problem**: `start-echo.bat` did nothing / exited immediately after ~3 weeks on the shelf.
Launch died at the very first import (`audio.py` → `sounddevice`), which prints an error and
calls `sys.exit()`, killing the whole process before any engine started.

**Root cause**: `start-echo.bat` ran bare `python main.py` against *system* Python 3.11.9 (no
venv). Since the last live run (2026-06-24), that shared global env had been silently clobbered:
`torch` was downgraded to the CPU wheel (`2.11.0+cpu`, `cuda.is_available()=False`) and the
entire audio stack — `sounddevice`, `soundfile`, `faster-whisper`, `ctranslate2`, `webrtcvad` —
was gone. Only the Ib-Lite deps survived. Almost certainly another project's `pip install`
stepping on the shared interpreter. The code and both servers (LM Studio :1234,
Kokoro-FastAPI :8880) were fine the whole time.

**Fix**: Gave Echo a dedicated venv at `echo_stage0/.venv` and pointed `start-echo.bat` at it
(`.venv\Scripts\python.exe`, with a guard that errors clearly if the venv is missing).
Reinstalled the full stack there. Did NOT reinstall CUDA torch: faster-whisper does CUDA via
`ctranslate2` (independent of torch) and the Ib-Lite embedder is CPU-by-design — so the CPU
torch wheel is correct and lighter. Verified faster-whisper loads `float16` on the RTX 5080.

**Rule**: A project with heavy/native deps must NOT rely on the shared global Python — pin it to
its own venv so another project's install can't clobber it. (Cross-project env lesson.)

**Prevention**: `start-echo.bat` now fails loudly if `.venv` is missing (no silent bare-python
fallback), so a wiped env surfaces immediately instead of a cryptic import death.

**Also**: `webrtcvad` has no prebuilt wheel and fails to build on Windows/Py3.11 (needs a C
compiler), which aborted the whole `pip install -r requirements.txt` (pip rolls back the batch).
Made it optional in requirements.txt — PTT (SPACE) is the default input and `vad.py` degrades to
PTT-only gracefully, so it's zero functional loss. For hands-free VAD later: `pip install
webrtcvad-wheels` (drop-in, same `import webrtcvad`).
