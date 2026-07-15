"""
Echo Stage 5 (Part 1) -- Ib-Lite Memory

Streaming pipeline with session management and the Ib-Lite memory subsystem
(local SQLite: Core/Policy/Preference/Fact/Episodic). Core + Policy are injected
into the system prompt at session start; relevant Facts/Episodes are retrieved
per turn; a background significance gate decides what to save after each turn.

Controls (Stage 8): ALL controls live on the dashboard — http://127.0.0.1:7862 by default.
There are NO keyboard commands. The old global `keyboard` hook fired for every keystroke on
the machine regardless of focus, so typing anywhere (the dashboard's own enroll box, another
terminal tab, Discord) drove Echo — 'm' muted it, 'q' quit it, 'l' wedged it in a blocking
model picker. One input surface now; Ctrl+C still stops the process.

  Talk (press & hold)  -- push-to-talk; hands-free VAD runs at home (see vad_default_for_location)
  Mute / Max Snark / Home / Jeep / Web / Model / Stop -- dashboard buttons
  Say "Echo, that's all for now" to end session gracefully
  Say "Echo, maximum snark mode" to lock snark to 10

Model selection: `python main.py --model <name-or-substring>` (or set ECHO_MODEL) pins a
model; otherwise a filter-picker runs at startup (Enter reuses your last pick). See
audition.md for the full model-testing workflow.
"""

import sys
import time
import random
import threading

import numpy as np

from audio import AudioRecorder, SAMPLE_RATE
from audio_queue import AudioQueue
from timer import PipelineTimer
from logger import SessionLogger
from stt import STTEngine
from llm import LLMClient
from tts import TTSEngine
from vad import VADDetector, FRAME_SIZE
from state import State, StateMachine
from session import (
    Session, is_signoff, is_forget, is_max_snark, is_stay_offline, is_go_online,
    is_location_override, is_enroll_command, is_enroll_cancel, load_config, save_config,
    vad_default_for_location,
)
from summarizer import generate_summary
from ib_lite import IbLite
from ib_lite.embedder import preload as embedder_preload
from persona import build_system_prompt, mood_opener, VOICE_PREVIEW_LINE
from persona_check import SelfCheckRunner, SELF_CHECK_EVERY, RECENT_K
from daily_state import get_daily_snark_level
from search import build_provider, load_search_config, format_search_block
from search_decision import decide_search
from location import resolve_location
from speaker_id import SpeakerRegistry, build_embedder
from webui import EchoControl, start_webui
import gpu


# How much audio to keep while idle in LISTENING. Capture must begin when RECORDING starts, so the
# idle buffer is trimmed to this rolling pre-roll instead of growing for the whole idle period.
# It is KEPT (not cleared) entering RECORDING for two reasons: VAD only fires ~3 frames (90ms) after
# speech begins, and a press-and-hold Talk press always lands slightly after Michael starts talking.
# 0.5s comfortably covers both without dragging in the previous turn. (tasks/lessons.md 2026-07-15)
PRE_ROLL_S = 0.5
PRE_ROLL_SAMPLES = int(SAMPLE_RATE * PRE_ROLL_S)


def trim_to_preroll(buffer: list, max_samples: int = PRE_ROLL_SAMPLES) -> None:
    """Drop the OLDEST chunks in-place until `buffer` holds at most `max_samples`.

    Called from the audio callback while idle in LISTENING. Always leaves at least one chunk so
    a press that lands between callbacks still has audio. Chunk-granular by design: it trims whole
    callback blocks (30ms each) rather than slicing arrays, so the hot path stays allocation-free.
    """
    buffered = sum(len(chunk) for chunk in buffer)
    while buffered > max_samples and len(buffer) > 1:
        buffered -= len(buffer.pop(0))


# ── Console UI ───────────────────────────────────────────────────────────

def draw_status(status="--", vad="--", mute="off", stt=0.0, fa=0.0, session_id="", turn=0):
    """Print a single-line status update, overwriting the previous one."""
    mute_str = " [MUTED]" if mute == "ON" else ""
    vad_str = f" vad:{vad}" if vad not in ("--", "off (PTT only)") else ""
    timing = ""
    if stt > 0:
        timing = f"  |  STT {stt:.2f}s, 1st-audio {fa:.2f}s"
    session_str = f"  |  turn {turn}" if turn > 0 else ""
    # No key hints: Stage 8 removed the keyboard hook — every control lives on the dashboard.
    line = f"  [{status}]{mute_str}{vad_str}{session_str}{timing}  [controls: dashboard]"

    sys.stdout.write(f"\r\033[K{line}")
    sys.stdout.flush()


def clear_status_line():
    sys.stdout.write("\r\033[K")
    sys.stdout.flush()


def _parse_model_arg(argv: list[str]) -> str | None:
    """Pull a --model <value> (or --model=<value>) pin from argv. CLI overrides ECHO_MODEL."""
    for i, a in enumerate(argv):
        if a == "--model" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--model="):
            return a.split("=", 1)[1]
    return None


def print_conversation(speaker: str, text: str):
    clear_status_line()
    print(f"  {speaker}: {text}")


_DEFAULT_FILLERS = ["Let me look that up, Michael.", "One moment."]


def _pick_filler(search_config: dict | None) -> str:
    """Pick an in-character 'going online' filler line (rotates for variety)."""
    lines = (search_config or {}).get("filler_lines") or _DEFAULT_FILLERS
    return random.choice(lines)


def _print_vram_hint() -> None:
    """Print the VRAM suspect line after an LLM failure ('' → nothing, e.g. no nvidia-smi)."""
    hint = gpu.vram_hint()
    if hint:
        print(f"  {hint}")


def _recent_echo_replies(history: list[dict], k: int) -> list[str]:
    """The last k Echo (assistant) replies from history, oldest→newest — the probe's input."""
    echoes = [m["content"] for m in history if m.get("role") == "assistant" and m.get("content")]
    return echoes[-k:]


# ── Pipeline ─────────────────────────────────────────────────────────────

def run_streaming_pipeline(
    audio: np.ndarray,
    stt: STTEngine,
    llm: LLMClient,
    tts: TTSEngine,
    audio_q: AudioQueue,
    logger: SessionLogger,
    history: list[dict],
    sm: StateMachine,
    session: Session,
    vad_mode: str,
    ib: IbLite | None = None,
    search_provider=None,
    search_config: dict | None = None,
    self_check: SelfCheckRunner | None = None,
    speaker_embedder=None,
    speaker_registry: SpeakerRegistry | None = None,
) -> dict | None:
    """
    Run the streaming pipeline. Returns timing info dict, or None on error.
    If sign-off is detected, returns {"signoff": True, "transcript": ...}.
    """
    input_duration = len(audio) / SAMPLE_RATE

    if len(audio) < SAMPLE_RATE * 0.3:
        print("  [Too short -- speak longer]")
        return None

    t0 = time.perf_counter()

    # ── STT ──
    t_stt_start = time.perf_counter()
    try:
        transcript = stt.transcribe(audio)
    except Exception as e:
        print(f"  [STT error: {e}]")
        return None
    t_stt_end = time.perf_counter()
    stt_latency = t_stt_end - t_stt_start

    if not transcript.strip():
        print("  [No speech detected]")
        return None

    # ── In-conversation enrollment capture (Stage 6 Part 1) ──
    # If a prior turn armed enrollment ("Echo, this is Jon"), THIS utterance's audio is the
    # voiceprint sample. Not a real exchange — no counter advance, never gated to memory.
    if session.enrolling and speaker_embedder is not None and speaker_registry is not None:
        pending = session.enrolling
        print_conversation("You", transcript)
        session.add_user_turn(transcript, stt_latency)
        if is_enroll_cancel(transcript):
            session.enrolling = None
            reply = "Alright, cancelled — no new voice saved."
        elif len(audio) < SAMPLE_RATE * 2.0:
            # Too little audio for a reliable print — keep enrolling armed and re-prompt.
            reply = f"That was too short to learn your voice, {pending}. Give me a full sentence when you're ready."
        else:
            try:
                emb = speaker_embedder.embed(audio)
                speaker_registry.enroll(pending, emb)
                speaker_registry.config["enabled"] = True
                speaker_registry.save()
                session.enrolling = None
                reply = f"Got it — I'll know your voice now, {pending}."
            except Exception as e:
                print(f"  [enroll error: {e}]")
                session.enrolling = None
                reply = "Something went wrong saving that voice. We can try again another time."
        print_conversation("Echo", reply)
        audio_q.start()
        try:
            tts_audio, tts_sr = tts.synthesize(reply)
            audio_q.enqueue(tts_audio, tts_sr)
            sm.transition(State.SPEAKING)
        except Exception as e:
            print(f"  [TTS error on enroll: {e}]")
        audio_q.finish()
        session.add_echo_turn(reply, 0.0)
        return {"stt": stt_latency, "first_audio": 0.0, "passed": True}

    # ── Sign-off check ──
    if is_signoff(transcript):
        print_conversation("You", transcript)
        session.add_user_turn(transcript, stt_latency)
        return {"signoff": True, "transcript": transcript, "stt": stt_latency}

    # ── Forget correction: "Echo, forget that" ──
    if is_forget(transcript) and ib and ib.available:
        print_conversation("You", transcript)
        session.add_user_turn(transcript, stt_latency)
        forgotten = ib.forget_last_fact()
        if forgotten:
            reply = f"Okay, I've let that go — the part about {forgotten['value']}."
        else:
            reply = "There's nothing recent for me to forget."
        print_conversation("Echo", reply)
        audio_q.start()
        try:
            tts_audio, tts_sr = tts.synthesize(reply)
            audio_q.enqueue(tts_audio, tts_sr)
            sm.transition(State.SPEAKING)
        except Exception as e:
            print(f"  [TTS error on forget: {e}]")
        audio_q.finish()
        session.add_echo_turn(reply, 0.0)
        return {"stt": stt_latency, "first_audio": 0.0, "passed": True}

    # ── Maximum Snark Mode: "Echo, maximum snark mode" ──
    # Locks snark to 10 for the rest of this session. Not a real exchange — does not
    # advance the exchange counter and is never gated to memory.
    if is_max_snark(transcript):
        print_conversation("You", transcript)
        session.add_user_turn(transcript, stt_latency)
        session.max_snark = True
        reply = "Maximum snark it is, Michael. You asked for it."
        print_conversation("Echo", reply)
        audio_q.start()
        try:
            tts_audio, tts_sr = tts.synthesize(reply)
            audio_q.enqueue(tts_audio, tts_sr)
            sm.transition(State.SPEAKING)
        except Exception as e:
            print(f"  [TTS error on max-snark: {e}]")
        audio_q.finish()
        session.add_echo_turn(reply, 0.0)
        return {"stt": stt_latency, "first_audio": 0.0, "passed": True}

    # ── Web-search off/on switch: "Echo, stay offline" / "go back online" ──
    # Session-scoped (session.web_search_off). Not a real exchange — no counter
    # advance, never gated. Mutually exclusive patterns (offline vs online keyword).
    if is_stay_offline(transcript) or is_go_online(transcript):
        going_offline = is_stay_offline(transcript)
        print_conversation("You", transcript)
        session.add_user_turn(transcript, stt_latency)
        session.web_search_off = going_offline
        reply = (
            "Alright, I'll stay offline — just me, no web."
            if going_offline
            else "Back online. I'll look things up when it actually helps."
        )
        print_conversation("Echo", reply)
        audio_q.start()
        try:
            tts_audio, tts_sr = tts.synthesize(reply)
            audio_q.enqueue(tts_audio, tts_sr)
            sm.transition(State.SPEAKING)
        except Exception as e:
            print(f"  [TTS error on search-toggle: {e}]")
        audio_q.finish()
        session.add_echo_turn(reply, 0.0)
        return {"stt": stt_latency, "first_audio": 0.0, "passed": True}

    # ── Location override: "Echo, we're in the Jeep" / "we're home" ──
    # Session-scoped (session.location). Not a real exchange — no counter advance,
    # never gated. Next launch re-resolves from the network.
    loc_override = is_location_override(transcript)
    if loc_override:
        print_conversation("You", transcript)
        session.add_user_turn(transcript, stt_latency)
        session.location = loc_override
        reply = "Buckle up, Michael." if loc_override == "jeep" else "Home it is, Michael."
        print_conversation("Echo", reply)
        audio_q.start()
        try:
            tts_audio, tts_sr = tts.synthesize(reply)
            audio_q.enqueue(tts_audio, tts_sr)
            sm.transition(State.SPEAKING)
        except Exception as e:
            print(f"  [TTS error on location-override: {e}]")
        audio_q.finish()
        session.add_echo_turn(reply, 0.0)
        return {"stt": stt_latency, "first_audio": 0.0, "passed": True}

    # ── Voice enrollment start: "Echo, this is Jon" ──
    # Arms enrollment; the NEXT utterance's audio becomes the voiceprint. Not a real
    # exchange — no counter advance, never gated. Only when speaker awareness is active.
    enroll_name = is_enroll_command(transcript)
    if enroll_name and speaker_embedder is not None and speaker_registry is not None:
        print_conversation("You", transcript)
        session.add_user_turn(transcript, stt_latency)
        session.enrolling = enroll_name
        reply = f"Say a few words so I can learn your voice, {enroll_name} — a full sentence is plenty."
        print_conversation("Echo", reply)
        audio_q.start()
        try:
            tts_audio, tts_sr = tts.synthesize(reply)
            audio_q.enqueue(tts_audio, tts_sr)
            sm.transition(State.SPEAKING)
        except Exception as e:
            print(f"  [TTS error on enroll-start: {e}]")
        audio_q.finish()
        session.add_echo_turn(reply, 0.0)
        return {"stt": stt_latency, "first_audio": 0.0, "passed": True}

    # ── Speaker identification (Stage 6 Part 1) ──
    # Fingerprint this utterance and resolve who is talking. Feature off (embedder None) or no
    # enrolled roster → assume Michael (pre-Stage-6 behavior). No match → "unknown" (guarded,
    # and the memory guardrail below withholds the write — never misattributed to Michael).
    speaker_score = 0.0
    speaker_known = True
    if speaker_embedder is not None and speaker_registry is not None and speaker_registry.count > 0:
        try:
            _emb = speaker_embedder.embed(audio)
            _name, speaker_score = speaker_registry.identify(_emb)
        except Exception as e:
            print(f"  [speaker-id error: {e}]")
            _name = None
        session.current_speaker = _name or "unknown"
        speaker_known = _name is not None
        print(f"  [speaker: {session.current_speaker} ({speaker_score:.2f})]")
    else:
        session.current_speaker = session.user_name or "Michael"
    session.last_speaker_score = speaker_score

    print_conversation("You", transcript)
    session.add_user_turn(transcript, stt_latency)

    # ── Web search (Stage 5 Part 3): a SEPARATE reasoning call decides whether this
    # turn needs the web and builds the query; results inject into the character pass.
    # CoT-isolated — Echo sees results, not the reasoning. A search turn breaks the
    # <3s budget, so we speak an in-character filler the instant we decide to search
    # (covers latency + is the transparency cue that she's going online).
    #
    # audio_q is started HERE (once) so the filler and the streamed answer share one
    # playback cycle: the filler enqueues first (plays while the search runs), then the
    # answer chunks follow it seamlessly.
    # ── No model? Say so and stop here (Stage 8.1) ──
    # Startup no longer blocks on a picker, so Echo can be up with no model resolved (LM Studio
    # had nothing loaded, or config.json's last_model is gone). Everything above still works —
    # the command short-circuits and enrollment need no LLM — but a real turn does. Bail BEFORE
    # advancing the exchange counter or spending a search, and don't fake a persona reply out of
    # an error: an explicit notice beats a silent fallback.
    if not llm.model_name:
        print("  [No model selected — pick one in the dashboard dropdown (LM Studio must have it loaded)]")
        return None

    search_block = ""
    search_meta = {
        "web_search_triggered": False, "search_prefilter_hit": False,
        "search_decision_ms": 0.0, "search_query": None, "search_provider": None,
        "search_latency_ms": 0.0, "results_count": 0, "search_engines_used": None,
    }
    audio_q.start()
    if search_provider is not None and not session.web_search_off:
        decision = decide_search(transcript, llm.model_name)
        search_meta["search_prefilter_hit"] = decision["prefilter_hit"]
        search_meta["search_decision_ms"] = round(decision["decision_ms"], 1)
        if decision["search"] and decision["query"]:
            query = decision["query"]
            search_meta["web_search_triggered"] = True
            search_meta["search_query"] = query

            # Filler first (latency cover + transparency). Not logged as a turn.
            filler = _pick_filler(search_config)
            print_conversation("Echo", filler)
            try:
                f_audio, f_sr = tts.synthesize(filler)
                audio_q.enqueue(f_audio, f_sr)
                sm.transition(State.SPEAKING)
            except Exception as e:
                print(f"  [TTS error on filler: {e}]")

            # Search runs behind the filler. Provider never raises → [] on any failure,
            # and format_search_block([]) yields a graceful in-character 'came up empty'.
            top_k = int((search_config or {}).get("top_k", 5))
            t_search = time.perf_counter()
            results = search_provider.search(query, top_k=top_k)
            search_meta["search_latency_ms"] = round((time.perf_counter() - t_search) * 1000, 1)
            search_meta["results_count"] = len(results)
            search_meta["search_provider"] = (search_config or {}).get("provider", "searxng")
            engines = sorted({r.engine for r in results if r.engine})
            search_meta["search_engines_used"] = ",".join(engines) if engines else None
            search_block = format_search_block(query, results)
            print(f"  [web: '{query}' → {len(results)} results in {search_meta['search_latency_ms']:.0f}ms]")

    # ── Personality + memory: assemble the full system prompt ──
    # This is a real exchange — advance the anti-drift counter FIRST so the anchor
    # fires on exchanges 8, 16, 24... (build_system_prompt checks count % 8 == 0).
    # Assembly order: persona → Core/Policy/Prefs slab (Ib-Lite) → retrieved
    # Fact/Episodic memory → web-search block (this turn only) → anti-drift anchor.
    exchange_n = session.increment_exchange()
    snark_level = session.effective_snark

    core_block = ""
    memory_block = ""
    memories_injected = 0
    memory_retrieval_ms = 0.0
    if ib and ib.available:
        core_block = ib.build_context_block()
        memory_block, memory_retrieval_ms, memories_injected = ib.read_memory(transcript)

    # Mood opener only on the opening exchange; it fades after that. The self-check
    # correction (if the probe flagged a break on a prior cycle) is consumed here and
    # cleared — it steers exactly this one turn back into character, then decays.
    opener = session.mood_opener if exchange_n == 1 else ""
    correction = session.consume_persona_correction()
    system_prompt = build_system_prompt(
        exchange_n, snark_level, core_block, memory_block,
        search_block=search_block, mood_opener=opener, location=session.location,
        speaker=session.current_speaker, correction=correction,
    )
    if correction:
        print("  [self-check: steering back to character this turn]")

    # ── LLM streaming -> TTS chunks -> audio queue (already started above) ──
    t_llm_start = time.perf_counter()
    llm_timing = {}
    t_first_audio = None
    full_response = ""
    chunk_count = 0

    try:
        for sentence in llm.stream_sentences(transcript, history, timing=llm_timing, system_prompt=system_prompt):
            full_response += sentence + " "

            try:
                tts_audio, tts_sr = tts.synthesize(sentence)
            except Exception as e:
                print(f"  [TTS error on chunk: {e}]")
                continue

            audio_q.enqueue(tts_audio, tts_sr)
            chunk_count += 1

            if t_first_audio is None:
                t_first_audio = time.perf_counter()
                sm.transition(State.SPEAKING)

    except TimeoutError as e:
        print(f"  [LLM timeout: {e}]")
        _print_vram_hint()
    except Exception as e:
        # The likely culprit on this machine is VRAM, not code: the model JIT-loads on the first
        # request, so if Invoke or a forgotten model owns the card the load OOMs here and LM Studio
        # drops the connection — which reads as a bare, cryptic error. Say the real suspect out loud.
        print(f"  [LLM error: {e}]")
        _print_vram_hint()

    audio_q.finish()

    full_response = full_response.strip()
    if full_response:
        print_conversation("Echo", full_response)
        history.append({"role": "user", "content": transcript})
        history.append({"role": "assistant", "content": full_response})
        session.add_echo_turn(full_response, (t_first_audio - t0) if t_first_audio else 0.0)

        # ── Memory write (off the hot path): significance gate, background thread ──
        # Guardrail (Stage 6 Part 1): only Michael's turns write to memory. A guest's or an
        # unknown speaker's words are NOT attributed to Michael and are not stored — the gate
        # stays Michael-only by construction. (Guest-memory attribution is a later Part.)
        # Facts ABOUT a guest told BY Michael ("Jon loves hiking") still save — that's Michael's turn.
        if ib and ib.available and session.current_speaker_is_michael:
            turn_text = f"{session.current_speaker}: {transcript}\nEcho: {full_response}"
            ib.write_memory(session.session_id, turn_text)
        elif ib and ib.available:
            print(f"  [memory: skipped — speaker is {session.current_speaker}, not Michael]")

        # ── Persona self-check (off the hot path): every N exchanges, judge Echo's recent
        # replies on a background thread and queue a one-turn correction if she drifted.
        # Single-flight and exempt under Max Snark (handled inside maybe_run).
        if self_check is not None and exchange_n % SELF_CHECK_EVERY == 0:
            self_check.maybe_run(
                session, llm.model_name, _recent_echo_replies(history, RECENT_K), exchange_n
            )

    # ── Timing ──
    actual_ttft = llm_timing.get("ttft", 0.0)
    first_audio = (t_first_audio - t0) if t_first_audio else 0.0
    if search_meta["web_search_triggered"]:
        # Search turns are exempt from the <3s budget (decision + round-trip + answer).
        # first_audio here is time to the ANSWER audio, after the filler already played.
        passed = None
        print(
            f"  [SEARCH (exempt): answer-audio {first_audio:.2f}s | "
            f"decision {search_meta['search_decision_ms']:.0f}ms | "
            f"search {search_meta['search_latency_ms']:.0f}ms | {chunk_count} chunks]"
        )
    else:
        passed = first_audio < 1.5 if t_first_audio else False
        status_str = "PASS" if passed else "FAIL"
        print(f"  [{status_str}: 1st audio {first_audio:.2f}s | STT {stt_latency:.2f}s | TTFT {actual_ttft:.2f}s | {chunk_count} chunks]")

    logger.log_run(
        stage=5,
        model=llm.model_name,
        stt_backend=stt.backend,
        tts_backend=tts.backend,
        vad_mode=vad_mode,
        input_duration_s=input_duration,
        stt_latency_s=stt_latency,
        llm_first_token_s=actual_ttft,
        first_audio_s=first_audio,
        total_latency_s=time.perf_counter() - t0,
        transcript=transcript,
        response_full=full_response,
        memory_retrieval_ms=round(memory_retrieval_ms, 1),
        memories_injected=memories_injected,
        location=session.location,
        speaker=session.current_speaker,
        speaker_score=round(speaker_score, 4),
        speaker_known=speaker_known,
        # Search turns log passed_budget=None so they're excluded from the pass-rate.
        passed_budget=(None if search_meta["web_search_triggered"]
                       else (first_audio < 1.5 if t_first_audio else False)),
        **search_meta,
    )

    return {"stt": stt_latency, "first_audio": first_audio, "passed": passed}


# ── Sign-Off Sequence ────────────────────────────────────────────────────

def run_signoff(
    llm: LLMClient,
    tts: TTSEngine,
    audio_q: AudioQueue,
    session: Session,
    ib: IbLite | None = None,
):
    """Execute the sign-off sequence: goodbye TTS, summary, save files, write episodic."""
    user_name = session.user_name or "friend"

    # Step 1: Speak goodbye
    goodbye_text = f"Goodbye {user_name}, talk soon."
    clear_status_line()
    print(f"  Echo: {goodbye_text}")

    audio_q.start()
    try:
        tts_audio, tts_sr = tts.synthesize(goodbye_text)
        audio_q.enqueue(tts_audio, tts_sr)
    except Exception as e:
        print(f"  [TTS error on goodbye: {e}]")
    audio_q.finish()
    audio_q.wait_done()

    # Step 2: Notify
    print("  [Writing session summary... please wait]")

    # Step 3: Generate summary
    conversation_text = session.get_conversation_text()
    summary = generate_summary(
        conversation_text=conversation_text,
        user_name=session.user_name,
        model=llm.model_name,
    )

    # Step 4: Save files
    session_path = session.save_session_file()
    summary_path = session.save_summary_file(summary)
    print(f"  [Session saved: {session_path.name}]")
    print(f"  [Summary saved: {summary_path.name}]")

    if "_error" in summary:
        print(f"  [WARNING: Summary had errors: {summary['_error']}]")

    # Step 5: Episodic write — session summary -> episodic_memory (before ended_at)
    if ib and ib.available:
        wrote = ib.end_session(session.session_id, summary, turn_count=session.turn_count)
        print(f"  [Episodic memory written]" if wrote else "  [No episodic summary to write]")
    else:
        print("  [Memory unavailable — skipping episodic write]")

    # Step 6: Done
    print("  [Session complete. Goodbye.]")


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    print()
    print("=" * 50)
    print("  ECHO -- Stage 5 Ib-Lite Memory")
    print("=" * 50)
    print()
    print("  Initializing...")

    # Load config
    config = load_config()
    user_name = config.get("user_name", "")
    if user_name:
        print(f"  User: {user_name}")

    # Initialize backends
    stt = STTEngine()
    # Model selection: --model/ECHO_MODEL pin, else filter-picker (Enter reuses last_model).
    pinned_model = _parse_model_arg(sys.argv[1:])
    llm = LLMClient(pinned=pinned_model, last_model=config.get("last_model"))
    # Remember the picked model so Enter reuses it next launch.
    if llm.model_name and config.get("last_model") != llm.model_name:
        config["last_model"] = llm.model_name
        save_config(config)
    # Voice persists in config.json (like last_model), so a dashboard pick survives a restart.
    tts = TTSEngine(voice=config.get("voice"))
    vad = VADDetector()

    # Initialize memory (Ib-Lite: local SQLite, Core + Policy injected at session start)
    ib = IbLite(llm.model_name)

    # Preload the Ib-Lite embedder (Stage 8.1). all-MiniLM-L6-v2 is a lazy singleton that used to
    # load on the FIRST encode() — i.e. during the first turn's memory retrieval — stalling Echo
    # ~10s the moment Michael first spoke. Weights are cached; it's the load that's slow. Pay it
    # here, alongside the other engine loads, where a couple of seconds is invisible.
    if ib.available:
        print("  Memory: loading embedder...", end="", flush=True)
        try:
            print(f" done ({embedder_preload():.1f}s)")
        except Exception as e:
            # Non-fatal: retrieval would just pay the cost on the first turn, as it used to.
            print(f" skipped ({e})")

    # Initialize web search (Stage 5 Part 3). build_provider returns None if disabled in
    # echo_search.json. healthy() is a warn-don't-block probe — search is optional.
    search_config = load_search_config()
    search_provider = build_provider(search_config)
    if search_provider is not None:
        ok = search_provider.healthy()
        print(
            f"  Web search: {search_config.get('provider', 'searxng')} @ "
            f"{search_config.get('searxng_base_url')} "
            f"({'reachable' if ok else 'UNREACHABLE — will decline in character'})"
        )
    else:
        print("  Web search: disabled (echo_search.json)")

    # Persona self-check probe (Stage 5 Part 4): a background alignment check every N
    # exchanges that catches drift and queues a one-turn correction. Off the hot path.
    self_check = SelfCheckRunner()

    # Speaker awareness (Stage 6 Part 1): voice fingerprinting so Echo knows who is talking.
    # Fail-soft — the embedder is built only when enabled AND at least one voiceprint exists
    # (so an ordinary launch never triggers the one-time ECAPA download); build_embedder
    # returns None on any failure, and the pipeline then assumes Michael (pre-Stage-6 behavior).
    # Build the embedder whenever speaker-ID is ENABLED (not only when profiles exist) — enrollment
    # (GUI button / "Echo, this is X") needs the model loaded before anyone is on file. Identification
    # still requires >=1 profile (guarded in the pipeline). Default enabled=false keeps ordinary
    # launches free of the model load; opting in downloads it once, then loads in a few seconds.
    speaker_registry = SpeakerRegistry()
    speaker_embedder = build_embedder(speaker_registry.config) if speaker_registry.enabled else None
    if speaker_embedder is not None:
        owner = (user_name or "Michael").lower()
        michael_enrolled = any(n.lower() == owner for n in speaker_registry.names)
        if speaker_registry.count == 0:
            print("  Speaker ID: on — no voices enrolled yet (enroll via the dashboard, or: python enroll.py Michael)")
        else:
            print(f"  Speaker ID: on ({speaker_registry.count} enrolled: {', '.join(speaker_registry.names)})"
                  + ("" if michael_enrolled
                     else "  [WARN: Michael not enrolled — his turns read as 'unknown' and won't be saved]"))
    elif speaker_registry.enabled:
        print("  Speaker ID: enabled but the voice model couldn't load — assuming Michael (see logs)")
    else:
        print("  Speaker ID: off (echo_speakers.json — enable it to use voice-ID)")

    logger = SessionLogger()
    history: list[dict] = []
    vad_mode = "webrtcvad" if vad.available else "ptt-only"

    # Initialize session
    session = Session(
        model=llm.model_name,
        stt_backend=stt.backend,
        tts_backend=tts.backend,
        user_name=user_name,
    )
    ib.start_session(session.session_id)

    # Personality: today's snark level (daily roll, persisted in echo_daily_state.json).
    # Maximum Snark Mode (voice "maximum snark mode" or the S key) overrides to 10 in-session.
    session.daily_snark = get_daily_snark_level()

    # Location context (Stage 5 Part 5): resolve home/jeep/unknown once from the local
    # network fingerprint. Fail-soft to "unknown" (neutral); the voice override can flip it.
    session.location = resolve_location()
    # Hands-free is location-aware (Stage 8): on at home (quiet, hands busy), off in the Jeep
    # (road noise would trigger it constantly). The dashboard toggle overrides this any time,
    # and a location change re-applies the default.
    session.vad_enabled = vad_default_for_location(session.location)

    # Opening-tone modulation: nudge Echo warmer/lighter based on how the LAST session
    # ended (Ib-Lite episodic mood_signal). Applied only on the first exchange.
    if ib and ib.available:
        session.mood_opener = mood_opener(ib.last_mood_signal())

    print(f"  Session: {session.session_id}")
    print(f"  Snark level: {session.daily_snark}/10")
    print(f"  Location: {session.location}"
          + ('  (say "Echo, we\'re in the Jeep" to switch)' if session.location != "jeep" else ""))
    if session.mood_opener:
        print("  Opening tone: adjusted to last session's mood")
    print(f"  Self-check: every {SELF_CHECK_EVERY} exchanges "
          "(silent; logs to sessions/persona_divergence.jsonl)")
    print()
    print('  Say "Echo, that\'s all for now" to end session')
    draw_status(status="READY", vad=vad_mode, session_id=session.session_id)
    print()

    # ── State machine ──
    sm = StateMachine()
    recorder = AudioRecorder()
    audio_q = AudioQueue()
    last_stt = 0.0
    last_fa = 0.0

    # ── Events ──
    speech_start_event = threading.Event()
    speech_end_event = threading.Event()
    quit_event = threading.Event()
    mute_toggle_event = threading.Event()
    space_pressed = threading.Event()
    space_released = threading.Event()

    # ── Web dashboard / control panel (Stage 7) ──
    # EchoControl bridges the loop and the dashboard: the web drives Echo through the SAME
    # events/flags the keyboard sets (never the pipeline). `muted` now lives on control so the
    # keyboard and the dashboard share one source of truth. Fail-soft: start_webui returns None
    # (dashboard off) if disabled / flask missing / port taken — the voice loop is unaffected.
    control = EchoControl(
        session, sm, speaker_registry,
        space_pressed=space_pressed, space_released=space_released,
        mute_toggle_event=mute_toggle_event, quit_event=quit_event,
        model_name=llm.model_name, speaker_active=speaker_embedder is not None,
        vad_available=vad.available, list_models=llm.list_models,
        list_voices=tts.list_voices, voice_name=tts.voice,
        model_state=llm.model_state,
    )
    _webui = start_webui(control)
    if _webui:
        print(f"  Dashboard: {_webui[1]}   <- ALL controls live here (no keyboard commands)")
    else:
        # Stage 8 removed the global keyboard hook, so the dashboard is the ONLY control surface.
        # If it didn't start there is no way to talk, mute, or sign off — say so loudly rather
        # than let Michael discover it by pressing keys that no longer do anything.
        print("  Dashboard: OFF — WARNING: Echo has NO control surface (no keyboard commands "
              "since Stage 8). Fix echo_webui.json / flask / port 7862. Ctrl+C to quit.")

    # ── VAD audio callback ──
    vad_buffer = []

    def vad_active() -> bool:
        """Hands-free listening right now: engine present AND enabled for this session.

        Read live on every audio callback (never cached) so the dashboard toggle and location
        changes take effect immediately — the same reason effective snark is recomputed per turn.
        """
        return vad.available and session.vad_enabled

    def audio_vad_callback(indata, frames, time_info, status):
        if not sm.can_record:
            return
        recorder._buffer.append(indata.copy())

        # Capture must start when RECORDING starts — not when LISTENING did. While idle we keep
        # only a short pre-roll and drop the rest. Before 2026-07-15 the whole idle period was
        # captured, so pressing Talk replayed everything said since the last turn: press-speak-
        # release looked silent, and the NEXT press answered the PREVIOUS sentence. The pre-roll
        # is deliberately KEPT on the way into RECORDING so VAD never clips the speech onset
        # (it only fires ~3 frames / 90ms in). See tasks/lessons.md 2026-07-15.
        if sm.state == State.LISTENING:
            trim_to_preroll(recorder._buffer)

        if vad_active() and sm.state == State.LISTENING:
            mono = indata[:, 0] if indata.ndim > 1 else indata.flatten()
            vad_buffer.extend(mono)
            while len(vad_buffer) >= FRAME_SIZE:
                frame = np.array(vad_buffer[:FRAME_SIZE], dtype=np.float32)
                del vad_buffer[:FRAME_SIZE]
                if vad.process_frame(frame) == "speech_start":
                    speech_start_event.set()

        elif vad_active() and sm.state == State.RECORDING:
            mono = indata[:, 0] if indata.ndim > 1 else indata.flatten()
            vad_buffer.extend(mono)
            while len(vad_buffer) >= FRAME_SIZE:
                frame = np.array(vad_buffer[:FRAME_SIZE], dtype=np.float32)
                del vad_buffer[:FRAME_SIZE]
                if vad.process_frame(frame) == "speech_end":
                    speech_end_event.set()

    # Stage 8: there is NO keyboard handler any more. `keyboard.hook` was a system-wide low-level
    # hook — it fired for every keystroke on the machine regardless of focus, so typing anywhere
    # (the dashboard's enroll box, Claude Code in a Windows Terminal tab, Discord) drove Echo:
    # 'm' muted, 'q' quit, 'l' wedged the loop in a blocking model picker. Gating it on console
    # focus narrowed but could not close that hole (Windows Terminal tabs are indistinguishable
    # from each other). Every control now lives on the dashboard — one input surface, no hook.
    # See tasks/lessons.md 2026-07-15.

    # ── Start audio stream ──
    import sounddevice as sd
    audio_stream = sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="float32",
        callback=audio_vad_callback, blocksize=FRAME_SIZE,
    )
    audio_stream.start()

    def do_model_swap(new_model: str):
        """Mid-chat hot-swap to an explicitly chosen model. Called ONLY from the main loop.

        Conversation history is preserved (plain message dicts), so the new model continues the
        SAME chat. The new model JIT-loads in LM Studio, so the first reply after a swap may pause.

        The dashboard only ever sets `control.pending_model` (a string); the swap itself happens
        here, on the main thread, between turns. That keeps EchoControl's "never touches the
        pipeline" invariant AND means no swap can ever land mid-generation. It also replaces the
        old L-key picker, whose blocking input() wedged the whole loop (tasks/lessons.md 2026-07-15).
        """
        if not new_model or new_model == llm.model_name:
            return
        clear_status_line()
        llm.set_model(new_model)
        ib.set_model(new_model)   # keep the significance gate in sync with the voice
        config["last_model"] = new_model
        save_config(config)
        control.set_model_name(new_model)
        print(f"  [Now using {new_model} — first reply may pause while LM Studio loads it]")

    def do_voice_preview(name: str):
        """Speak the sample line in `name` WITHOUT adopting it. Main loop only, while idle.

        The mic is paused for the duration: in LISTENING the stream is live, so hands-free VAD
        would hear the preview and Echo would answer herself. Playback is synchronous
        (audio_q.finish blocks), so the mic is back before we return.
        """
        if not name:
            return
        clear_status_line()
        print(f"  [Preview: {name}]")
        try:
            audio_stream.stop()
        except Exception:
            pass
        try:
            preview_audio, preview_sr = tts.synthesize(VOICE_PREVIEW_LINE, voice=name)
            audio_q.start()
            audio_q.enqueue(preview_audio, preview_sr)
            audio_q.finish()
        except Exception as e:
            print(f"  [preview error: {e}]")
        try:
            audio_stream.start()
        except Exception:
            pass

    def do_voice_swap(new_voice: str):
        """Change Kokoro's voice. Called ONLY from the main loop, between turns.

        Parked by the dashboard (control.pending_voice) rather than applied on the web thread:
        synthesis is chunk-by-chunk, so a mid-reply change would switch voice mid-sentence.
        Persisted to config.json so an audition Michael likes survives the next launch.
        """
        if not new_voice or new_voice == tts.voice:
            return
        clear_status_line()
        tts.set_voice(new_voice)
        config["voice"] = new_voice
        save_config(config)
        control.set_voice_name(new_voice)
        print(f"  [Voice: {new_voice}]")

    signoff_triggered = False

    try:
        while not sm.is_shutdown:
            # ── LISTENING ──
            if sm.state == State.LISTENING:
                draw_status(
                    status="LISTENING", vad="active" if vad_active() else "off",
                    mute="ON" if control.muted else "off",
                    stt=last_stt, fa=last_fa, turn=session.turn_count,
                )

                speech_start_event.clear()
                speech_end_event.clear()
                space_pressed.clear()
                space_released.clear()
                recorder._buffer = []
                vad_buffer.clear()
                vad.reset()

                while not sm.is_shutdown:
                    if quit_event.is_set():
                        sm.transition(State.SHUTDOWN)
                        break
                    if control.pending_model:
                        do_model_swap(control.take_pending_model())
                        break  # re-enter LISTENING cleanly (resets buffers, redraws status)
                    if control.pending_voice:
                        do_voice_swap(control.take_pending_voice())
                        break
                    if control.pending_preview:
                        do_voice_preview(control.take_pending_preview())
                        break
                    if mute_toggle_event.is_set():
                        mute_toggle_event.clear()
                        if control.muted:
                            sm.transition(State.MUTED)
                            break
                        draw_status(
                            status="LISTENING", vad="active" if vad_active() else "off",
                            mute="ON" if control.muted else "off",
                            stt=last_stt, fa=last_fa, turn=session.turn_count,
                        )
                    if speech_start_event.is_set():
                        speech_start_event.clear()
                        sm.transition(State.RECORDING)
                        break
                    if space_pressed.is_set():
                        space_pressed.clear()
                        sm.transition(State.RECORDING)
                        break
                    time.sleep(0.02)

            # ── RECORDING ──
            elif sm.state == State.RECORDING:
                draw_status(
                    status="RECORDING...", vad="active" if vad_active() else "off",
                    mute="off", stt=last_stt, fa=last_fa, turn=session.turn_count,
                )

                while not sm.is_shutdown:
                    if quit_event.is_set():
                        sm.transition(State.SHUTDOWN)
                        break
                    if speech_end_event.is_set():
                        speech_end_event.clear()
                        sm.transition(State.PROCESSING)
                        break
                    if space_released.is_set():
                        space_released.clear()
                        sm.transition(State.PROCESSING)
                        break
                    time.sleep(0.02)

            # ── PROCESSING ──
            elif sm.state == State.PROCESSING:
                draw_status(
                    status="PROCESSING...", vad="paused", mute="off",
                    stt=last_stt, fa=last_fa, turn=session.turn_count,
                )

                audio_data = np.concatenate(recorder._buffer) if recorder._buffer else np.array([], dtype="float32")
                if audio_data.ndim > 1:
                    audio_data = audio_data[:, 0]
                recorder._buffer = []
                audio_stream.stop()

                result = run_streaming_pipeline(
                    audio=audio_data,
                    stt=stt, llm=llm, tts=tts,
                    audio_q=audio_q, logger=logger,
                    history=history, sm=sm,
                    session=session, vad_mode=vad_mode,
                    ib=ib,
                    search_provider=search_provider,
                    search_config=search_config,
                    self_check=self_check,
                    speaker_embedder=speaker_embedder,
                    speaker_registry=speaker_registry,
                )

                if result and result.get("signoff"):
                    # Sign-off detected — enter sign-off sequence
                    signoff_triggered = True
                    sm.transition(State.SIGN_OFF)
                else:
                    if result:
                        last_stt = result["stt"]
                        last_fa = result["first_audio"]

                    if sm.state == State.SPEAKING:
                        draw_status(
                            status="SPEAKING...", vad="paused", mute="off",
                            stt=last_stt, fa=last_fa, turn=session.turn_count,
                        )
                        audio_q.wait_done()

                    if sm.state != State.SHUTDOWN:
                        audio_stream.start()
                        sm.transition(State.LISTENING)

            # ── SIGN_OFF ──
            elif sm.state == State.SIGN_OFF:
                run_signoff(
                    llm=llm, tts=tts, audio_q=audio_q, session=session,
                    ib=ib,
                )
                sm.transition(State.SHUTDOWN)

            # ── MUTED ──
            elif sm.state == State.MUTED:
                draw_status(
                    status="MUTED", vad="paused", mute="ON",
                    stt=last_stt, fa=last_fa, turn=session.turn_count,
                )

                mute_toggle_event.clear()
                while not sm.is_shutdown:
                    if quit_event.is_set():
                        sm.transition(State.SHUTDOWN)
                        break
                    # Auditioning voices while muted is perfectly reasonable — mute is about the
                    # MIC, not the speaker. Without this the preview would queue and never play.
                    if control.pending_preview:
                        do_voice_preview(control.take_pending_preview())
                        draw_status(
                            status="MUTED", vad="paused", mute="ON",
                            stt=last_stt, fa=last_fa, turn=session.turn_count,
                        )
                    if mute_toggle_event.is_set():
                        mute_toggle_event.clear()
                        if not control.muted:
                            sm.transition(State.LISTENING)
                            break
                    time.sleep(0.05)

    except KeyboardInterrupt:
        pass
    finally:
        try:
            audio_stream.stop()
            audio_stream.close()
        except Exception:
            pass
        audio_q.stop()
        tts.shutdown()

    # If Q was pressed mid-conversation (no sign-off), still save session file
    if not signoff_triggered and session.has_turns:
        clear_status_line()
        print("  [Saving session log (no summary -- use sign-off for full save)]")
        session_path = session.save_session_file()
        print(f"  [Session saved: {session_path.name}]")

    print("\n  Goodbye!")


if __name__ == "__main__":
    main()
