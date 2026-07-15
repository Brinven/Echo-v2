"""
Voice enrollment CLI for Echo (Stage 6 Part 1).

Records a few seconds from the mic, computes a speaker voiceprint (ECAPA), and saves
it under a name in echo_speakers.json. This is how Michael enrolls himself and a small
roster of known people, and how you curate them.

Usage (run from echo_stage0/, with the venv active and LM Studio not required):
  python enroll.py Michael                 # record & enroll "Michael" (do this FIRST)
  python enroll.py Jon --seconds 6         # longer capture
  python enroll.py Jon --samples 3         # average 3 short recordings (more robust)
  python enroll.py --list                  # show enrolled profiles
  python enroll.py --rm Jon                 # remove a profile

Enrolling the first profile flips `enabled` to true in echo_speakers.json (activates
speaker awareness in the voice loop). Enroll MICHAEL FIRST — otherwise, once other
profiles exist, his own turns read as "unknown" and won't be saved to memory.

Note: an in-conversation flow also exists — say "Echo, this is Jon" during a session.
This CLI is the reliable path (clean audio, repeatable, testable).
"""

import argparse
import sys
import time

import numpy as np

from audio import AudioRecorder, SAMPLE_RATE
from speaker_id import SpeakerRegistry, build_embedder

GREEN = "\033[32m"; YELLOW = "\033[33m"; CYAN = "\033[36m"; DIM = "\033[2m"; RESET = "\033[0m"

# Reject captures shorter than this — too little audio makes a weak, unreliable print.
_MIN_SECONDS = 2.0
# RMS below this reads as silence / mic not picking up — warn rather than enroll noise.
_MIN_RMS = 0.005


def _record_once(seconds: int) -> np.ndarray:
    """Record `seconds` of 16 kHz mono float32 audio from the default mic."""
    rec = AudioRecorder()
    print(f"  {DIM}Get ready...{RESET}")
    for n in (3, 2, 1):
        print(f"  {n}...")
        time.sleep(0.6)
    print(f"  {CYAN}● Recording {seconds}s — speak naturally (say anything).{RESET}")
    rec.start()
    time.sleep(seconds)
    audio = rec.stop()
    print(f"  {DIM}done ({len(audio) / SAMPLE_RATE:.1f}s captured){RESET}")
    return audio


def _capture_embedding(embedder, seconds: int, samples: int) -> np.ndarray | None:
    """Record `samples` clips and return their averaged, L2-normalized embedding.

    Returns None if every clip was too short / too quiet to use.
    """
    vecs = []
    for i in range(samples):
        if samples > 1:
            print(f"\n  {CYAN}Sample {i + 1}/{samples}{RESET}")
        audio = _record_once(seconds)
        if len(audio) < SAMPLE_RATE * _MIN_SECONDS:
            print(f"  {YELLOW}too short — skipping this sample{RESET}")
            continue
        rms = float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0
        if rms < _MIN_RMS:
            print(f"  {YELLOW}very quiet (rms={rms:.4f}) — is the mic live? skipping{RESET}")
            continue
        vecs.append(embedder.embed(audio))

    if not vecs:
        return None
    mean = np.mean(np.stack(vecs), axis=0)
    norm = float(np.linalg.norm(mean))
    return mean / norm if norm > 0 else mean


def cmd_list(registry: SpeakerRegistry) -> int:
    print(f"\n{CYAN}Enrolled voiceprints{RESET} ({registry.count})"
          f"  {DIM}[enabled={registry.enabled}, threshold={registry.match_threshold}]{RESET}")
    if not registry.count:
        print(f"  {DIM}(none — run: python enroll.py Michael){RESET}")
        return 0
    for p in registry.profiles:
        print(f"  {p.get('name')!r:16} model={p.get('model')}  enrolled={p.get('enrolled_at')}")
    return 0


def cmd_remove(registry: SpeakerRegistry, name: str) -> int:
    if registry.remove(name):
        registry.save()
        print(f"{GREEN}removed voiceprint for {name!r}{RESET}")
        return 0
    print(f"{YELLOW}no enrolled profile named {name!r}{RESET}")
    return 1


def cmd_enroll(registry: SpeakerRegistry, name: str, seconds: int, samples: int) -> int:
    # Force the embedder on regardless of the `enabled` flag — enrollment is explicit.
    cfg = dict(registry.config)
    cfg["enabled"] = True
    embedder = build_embedder(cfg)
    if embedder is None:
        print(f"{YELLOW}Speaker embedder unavailable.{RESET} Install it into the venv:")
        print(f"  {DIM}pip install torchaudio --index-url https://download.pytorch.org/whl/cpu{RESET}")
        print(f"  {DIM}pip install speechbrain==1.1.0{RESET}")
        print(f"  {DIM}(first run downloads the ~89 MB ECAPA model once){RESET}")
        return 1

    replacing = registry.has(name)
    print(f"\n{CYAN}Enrolling {name!r}{RESET}"
          + (f"  {YELLOW}(replacing existing print){RESET}" if replacing else ""))
    emb = _capture_embedding(embedder, seconds, samples)
    if emb is None:
        print(f"{YELLOW}No usable audio captured — nothing enrolled.{RESET}")
        return 1

    registry.enroll(name, emb)
    # Activate speaker awareness now that at least one print exists.
    registry.config["enabled"] = True
    registry.save()
    print(f"{GREEN}✓ enrolled {name!r}{RESET} ({registry.count} profile(s) total; speaker awareness enabled)")
    if not any(n.lower() == "michael" for n in registry.names):
        print(f"  {YELLOW}Reminder: enroll Michael too (python enroll.py Michael) so his turns"
              f" are attributed.{RESET}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Enroll / curate Echo speaker voiceprints.")
    p.add_argument("name", nargs="?", help="name to enroll (e.g. Michael)")
    p.add_argument("--seconds", "-s", type=int, default=None, help="recording length (default from config)")
    p.add_argument("--samples", "-n", type=int, default=1, help="clips to average for a sturdier print")
    p.add_argument("--list", action="store_true", help="list enrolled profiles")
    p.add_argument("--rm", metavar="NAME", default=None, help="remove a profile by name")
    return p


def main() -> int:
    args = build_parser().parse_args()
    registry = SpeakerRegistry()

    if args.list:
        return cmd_list(registry)
    if args.rm:
        return cmd_remove(registry, args.rm)
    if not args.name:
        build_parser().print_help()
        return 2

    seconds = args.seconds if args.seconds is not None else registry.enroll_seconds
    samples = max(1, args.samples)
    return cmd_enroll(registry, args.name, seconds, samples)


if __name__ == "__main__":
    sys.exit(main())
