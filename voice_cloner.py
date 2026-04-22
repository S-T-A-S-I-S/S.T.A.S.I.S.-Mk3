"""
Voice Cloner — clones the user's voice from a recorded sample.

How it works
------------
1. User records 15–30 s of speech via the web UI (Settings → Train My Voice).
   The browser uploads the audio to POST /api/voice/sample.
2. server.py saves the file to data/voice/sample.wav (auto-converts from WebM).
3. On the next TTS call, synth() runs Coqui XTTS v2 with sample.wav as the
   speaker reference.  The model clones the voice on-the-fly — no separate
   "training" step is needed beyond providing a clean sample.

TTS priority order
------------------
1. Coqui XTTS v2     — cloned user voice  (GPU or CPU, ~1.8 GB model download)
2. OpenAI TTS nova   — if OPENAI_API_KEY is set
3. pyttsx3 SAPI5     — always available on Windows

Install XTTS:   pip install TTS
Install ffmpeg: winget install ffmpeg     (needed for WebM → WAV conversion)
"""
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

_DIR = Path(__file__).parent / "data" / "voice"
_DIR.mkdir(parents=True, exist_ok=True)

SAMPLE_PATH  = _DIR / "sample.wav"     # reference recording
PROFILE_FILE = _DIR / "profile.json"   # metadata
MIN_DURATION = 10.0                     # seconds — reject anything shorter


# ── XTTS availability check ───────────────────────────────────────────────────

_xtts_model   = None   # lazy-loaded
_xtts_checked = False

def _try_load_xtts():
    global _xtts_model, _xtts_checked
    if _xtts_checked:
        return _xtts_model
    _xtts_checked = True
    try:
        import torch, functools
        # PyTorch 2.6 changed weights_only default to True, breaking Coqui's loader.
        _orig_load = torch.load
        torch.load = functools.partial(_orig_load, weights_only=False)

        from TTS.api import TTS as _TTS  # type: ignore
        print("[VoiceCloner] Loading XTTS v2 model (first run: downloads ~1.8 GB)…")
        _xtts_model = _TTS("tts_models/multilingual/multi-dataset/xtts_v2", gpu=False)
        print("[VoiceCloner] XTTS v2 ready.")
    except ImportError as e:
        print(f"[VoiceCloner] Missing dependency: {e} — falling back to OpenAI/pyttsx3.")
    except Exception as e:
        print(f"[VoiceCloner] XTTS load error: {e}")
    return _xtts_model


# ── Audio conversion (WebM → WAV) ─────────────────────────────────────────────

def convert_to_wav(src: Path, dst: Path) -> bool:
    """Convert any audio file to 22050 Hz mono WAV using ffmpeg."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", str(src),
             "-ar", "22050", "-ac", "1", str(dst)],
            capture_output=True, timeout=30,
        )
        return r.returncode == 0 and dst.exists() and dst.stat().st_size > 1000
    except FileNotFoundError:
        print("[VoiceCloner] ffmpeg not found — trying to use audio as-is.")
        return False
    except Exception as e:
        print(f"[VoiceCloner] Conversion error: {e}")
        return False


def _audio_duration(path: Path) -> float:
    """Return duration in seconds via ffprobe, or 0.0 on failure."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


# ── Public API ────────────────────────────────────────────────────────────────

def has_sample() -> bool:
    return SAMPLE_PATH.exists() and SAMPLE_PATH.stat().st_size > 10_000


def validate_sample() -> str:
    """
    Check the saved voice sample.
    Returns an empty string on success, or an error message.
    """
    if not SAMPLE_PATH.exists():
        return "No sample recorded yet."
    dur = _audio_duration(SAMPLE_PATH)
    if dur > 0 and dur < MIN_DURATION:
        return f"Sample too short ({dur:.1f} s). Record at least {MIN_DURATION:.0f} s."
    if SAMPLE_PATH.stat().st_size < 10_000:
        return "Sample file appears empty or corrupt."
    # Write profile
    import json
    with open(PROFILE_FILE, "w") as f:
        json.dump({"created": time.time(), "duration": dur, "path": str(SAMPLE_PATH)}, f)
    return ""   # OK


def synth(text: str) -> Optional[bytes]:
    """
    Synthesize speech using the cloned voice (XTTS v2).
    Returns WAV bytes, or None if XTTS is not available / sample missing.
    """
    if not has_sample():
        return None
    model = _try_load_xtts()
    if model is None:
        return None

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        out = f.name
    try:
        model.tts_to_file(
            text=text,
            speaker_wav=str(SAMPLE_PATH),
            language="en",
            file_path=out,
        )
        with open(out, "rb") as f:
            data = f.read()
        return data if len(data) > 100 else None
    except Exception as e:
        print(f"[VoiceCloner] synth error: {e}")
        return None
    finally:
        try:
            os.unlink(out)
        except Exception:
            pass


def status() -> dict:
    """Return cloner status for /health endpoint."""
    return {
        "has_sample": has_sample(),
        "xtts_available": (_xtts_model is not None) if _xtts_checked else "unchecked",
        "sample_path": str(SAMPLE_PATH) if has_sample() else None,
    }
