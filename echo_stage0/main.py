"""
Echo Stage 5 (Part 1) -- Ib-Lite Memory

Streaming pipeline with session management and the Ib-Lite memory subsystem
(local SQLite: Core/Policy/Preference/Fact/Episodic). Core + Policy are injected
into the system prompt at session start; relevant Facts/Episodes are retrieved
per turn; a background significance gate decides what to save after each turn.

Controls:
  SPACE  -- push-to-talk (hold to record, release to process)
  M      -- toggle mute
  S      -- toggle Maximum Snark Mode (locks snark to 10 for the session)
  Q      -- quit (double-press required mid-conversation)
  Say "Echo, that's all for now" to end session gracefully
  Say "Echo, maximum snark mode" to lock snark to 10
"""

import sys
import time
import threading

import keyboard
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
from session import Session, is_signoff, is_forget, is_max_snark, load_config
from summarizer import generate_summary
from ib_lite import IbLite
from persona import build_system_prompt
from daily_state import get_daily_snark_level


# ── Console UI ───────────────────────────────────────────────────────────

def draw_status(status="--", vad="--", mute="off", stt=0.0, fa=0.0, session_id="", turn=0):
    """Print a single-line status update, overwriting the previous one."""
    mute_str = " [MUTED]" if mute == "ON" else ""
    vad_str = f" vad:{vad}" if vad not in ("--", "off (PTT only)") else ""
    timing = ""
    if stt > 0:
        timing = f"  |  STT {stt:.2f}s, 1st-audio {fa:.2f}s"
    session_str = f"  |  turn {turn}" if turn > 0 else ""
    line = f"  [{status}]{mute_str}{vad_str}{session_str}{timing}  [SPACE:talk M:mute S:snark Q:quit]"

    sys.stdout.write(f"\r\033[K{line}")
    sys.stdout.flush()


def clear_status_line():
    sys.stdout.write("\r\033[K")
    sys.stdout.flush()


def print_conversation(speaker: str, text: str):
    clear_status_line()
    print(f"  {speaker}: {text}")


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

    print_conversation("You", transcript)
    session.add_user_turn(transcript, stt_latency)

    # ── Personality + memory: assemble the full system prompt ──
    # This is a real exchange — advance the anti-drift counter FIRST so the anchor
    # fires on exchanges 8, 16, 24... (build_system_prompt checks count % 8 == 0).
    # Assembly order: persona block (with today's snark) → Core/Policy/Prefs slab
    # (Ib-Lite) → retrieved Fact/Episodic memory → anti-drift anchor.
    exchange_n = session.increment_exchange()
    snark_level = session.effective_snark

    core_block = ""
    memory_block = ""
    memories_injected = 0
    memory_retrieval_ms = 0.0
    if ib and ib.available:
        core_block = ib.build_context_block()
        memory_block, memory_retrieval_ms, memories_injected = ib.read_memory(transcript)

    system_prompt = build_system_prompt(exchange_n, snark_level, core_block, memory_block)

    # ── LLM streaming -> TTS chunks -> audio queue ──
    audio_q.start()
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
    except Exception as e:
        print(f"  [LLM error: {e}]")

    audio_q.finish()

    full_response = full_response.strip()
    if full_response:
        print_conversation("Echo", full_response)
        history.append({"role": "user", "content": transcript})
        history.append({"role": "assistant", "content": full_response})
        session.add_echo_turn(full_response, (t_first_audio - t0) if t_first_audio else 0.0)

        # ── Memory write (off the hot path): significance gate, background thread ──
        if ib and ib.available:
            turn_text = f"{session.user_name or 'Michael'}: {transcript}\nEcho: {full_response}"
            ib.write_memory(session.session_id, turn_text)

    # ── Timing ──
    actual_ttft = llm_timing.get("ttft", 0.0)
    first_audio = (t_first_audio - t0) if t_first_audio else 0.0
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
    llm = LLMClient()
    tts = TTSEngine()
    vad = VADDetector()

    # Initialize memory (Ib-Lite: local SQLite, Core + Policy injected at session start)
    ib = IbLite(llm.model_name)

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

    print(f"  Session: {session.session_id}")
    print(f"  Snark level: {session.daily_snark}/10")
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
    muted = False

    # Q double-press tracking
    q_first_press_time = 0.0
    q_warned = False

    # ── VAD audio callback ──
    vad_buffer = []

    def audio_vad_callback(indata, frames, time_info, status):
        if not sm.can_record:
            return
        recorder._buffer.append(indata.copy())

        if vad.available and sm.state == State.LISTENING:
            mono = indata[:, 0] if indata.ndim > 1 else indata.flatten()
            vad_buffer.extend(mono)
            while len(vad_buffer) >= FRAME_SIZE:
                frame = np.array(vad_buffer[:FRAME_SIZE], dtype=np.float32)
                del vad_buffer[:FRAME_SIZE]
                if vad.process_frame(frame) == "speech_start":
                    speech_start_event.set()

        elif vad.available and sm.state == State.RECORDING:
            mono = indata[:, 0] if indata.ndim > 1 else indata.flatten()
            vad_buffer.extend(mono)
            while len(vad_buffer) >= FRAME_SIZE:
                frame = np.array(vad_buffer[:FRAME_SIZE], dtype=np.float32)
                del vad_buffer[:FRAME_SIZE]
                if vad.process_frame(frame) == "speech_end":
                    speech_end_event.set()

    # ── Keyboard handlers ──
    def on_key(event):
        nonlocal muted, q_first_press_time, q_warned
        if event.event_type != keyboard.KEY_DOWN:
            if event.name == "space":
                space_released.set()
            return

        if event.name == "q":
            if not session.has_turns:
                # No conversation yet — exit immediately
                quit_event.set()
            else:
                now = time.monotonic()
                if q_warned and (now - q_first_press_time) < 3.0:
                    # Second Q within 3 seconds — force exit
                    quit_event.set()
                else:
                    # First Q — warn
                    q_first_press_time = now
                    q_warned = True
                    clear_status_line()
                    print("  [Press Q again within 3s to exit without saving session]")
        elif event.name == "m":
            muted = not muted
            mute_toggle_event.set()
        elif event.name == "s":
            # Toggle Maximum Snark Mode (locks snark to 10 for the session).
            session.max_snark = not session.max_snark
            clear_status_line()
            state = "ON (snark locked to 10)" if session.max_snark else "off (back to daily)"
            print(f"  [Maximum Snark Mode: {state}]")
        elif event.name == "space":
            space_pressed.set()

    keyboard.hook(on_key)

    # ── Start audio stream ──
    import sounddevice as sd
    audio_stream = sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="float32",
        callback=audio_vad_callback, blocksize=FRAME_SIZE,
    )
    audio_stream.start()

    signoff_triggered = False

    try:
        while not sm.is_shutdown:
            # ── LISTENING ──
            if sm.state == State.LISTENING:
                draw_status(
                    status="LISTENING", vad="active" if vad.available else "off",
                    mute="ON" if muted else "off",
                    stt=last_stt, fa=last_fa, turn=session.turn_count,
                )

                speech_start_event.clear()
                speech_end_event.clear()
                space_pressed.clear()
                space_released.clear()
                recorder._buffer = []
                vad_buffer.clear()
                vad.reset()
                q_warned = False  # reset Q warning on new listening cycle

                while not sm.is_shutdown:
                    if quit_event.is_set():
                        sm.transition(State.SHUTDOWN)
                        break
                    if mute_toggle_event.is_set():
                        mute_toggle_event.clear()
                        if muted:
                            sm.transition(State.MUTED)
                            break
                        draw_status(
                            status="LISTENING", vad="active" if vad.available else "off",
                            mute="ON" if muted else "off",
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
                    status="RECORDING...", vad="active" if vad.available else "off",
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
                    if mute_toggle_event.is_set():
                        mute_toggle_event.clear()
                        if not muted:
                            sm.transition(State.LISTENING)
                            break
                    time.sleep(0.05)

    except KeyboardInterrupt:
        pass
    finally:
        keyboard.unhook_all()
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
