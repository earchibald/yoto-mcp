"""Spoken prompts made on this machine: text → mp3 (ollama speech model) → transcript check.

The display text keeps real spellings ("Hilo Book 4: Waking the Monsters"); the spoken text runs
through a pronunciation map first ("Hilo" → "High-low"). Clips are cached by model, voice and
spoken text, so asking again reuses the file, and Yoto dedupes the upload by its sha256.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from . import paths

DEFAULT_MODEL = "phonon"
DEFAULT_VOICE = "Harper"
DEFAULT_PRONUNCIATIONS = {"Hilo": "High-low"}
_NUMBERS = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty"
).split()


@dataclass
class Clip:
    path: Path
    spoken: str
    transcript: str | None
    match: float | None
    cached: bool

    def info(self) -> dict:
        out: dict = {"file": str(self.path), "spoken_text": self.spoken, "cached": self.cached}
        if self.transcript is not None:
            out["transcript"] = self.transcript
            out["match"] = round(self.match or 0.0, 2)
        return out


def settings() -> dict:
    """Voice defaults from ~/.yoto/voice.json, if the user saved any."""
    f = paths.home() / "voice.json"
    data = json.loads(f.read_text()) if f.exists() else {}
    return {
        "model": data.get("model", DEFAULT_MODEL),
        "voice": data.get("voice", DEFAULT_VOICE),
        "pronunciations": {**DEFAULT_PRONUNCIATIONS, **(data.get("pronunciations") or {})},
    }


def spoken_text(text: str, pronunciations: dict[str, str] | None = None) -> str:
    out = text.replace("—", ", ").replace("–", ", ")
    for word, say in (pronunciations or {}).items():
        out = re.sub(rf"\b{re.escape(word)}\b", say, out, flags=re.I)
    return out


def _words(s: str) -> str:
    s = s.casefold()
    s = re.sub(
        r"\b(\d{1,2})\b", lambda m: _NUMBERS[int(m.group(1))] if int(m.group(1)) <= 20 else m.group(1), s
    )
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def similarity(said: str, heard: str) -> float:
    a, b = _words(said).replace(" ", ""), _words(heard).replace(" ", "")
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def available() -> dict:
    return {
        "ollama": bool(shutil.which("ollama")),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "stt": bool(shutil.which("mlx_whisper") or shutil.which("whisper")),
    }


def synthesize(text: str, out: Path, model: str, voice: str) -> None:
    """Run the speech model and encode its audio as mp3 (raises on failure)."""
    have = available()
    if not (have["ollama"] and have["ffmpeg"]):
        raise RuntimeError(
            "Local TTS needs `ollama` (with the speech model pulled) and `ffmpeg` on PATH. "
            f"Missing: {', '.join(k for k in ('ollama', 'ffmpeg') if not have[k])}."
        )
    tmp = out.with_suffix(".part.mp3")
    gen = subprocess.Popen(["ollama", "run", model, "--voice", voice, text], stdout=subprocess.PIPE)
    enc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            "-",
            "-codec:a",
            "libmp3lame",
            "-qscale:a",
            "2",
            str(tmp),
        ],
        stdin=gen.stdout,
        capture_output=False,
    )
    assert gen.stdout is not None
    gen.stdout.close()
    if gen.wait() != 0 or enc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"TTS failed for {text!r} (model {model!r}, voice {voice!r}).")
    tmp.replace(out)


def transcribe(path: Path) -> str | None:
    """Speech-to-text with mlx_whisper or whisper, if one is installed."""
    tool = shutil.which("mlx_whisper") or shutil.which("whisper")
    if not tool:
        return None
    with tempfile.TemporaryDirectory() as d:
        run = subprocess.run(
            [
                tool,
                str(path),
                "--output-format",
                "txt",
                "--output-dir",
                d,
                "--verbose",
                "False",
                "--language",
                "en",
            ],
            capture_output=True,
            timeout=300,
        )
        files = list(Path(d).glob("*.txt"))
        if run.returncode != 0 or not files:
            return None
        return files[0].read_text().strip()


def speak(
    text: str,
    voice: str | None = None,
    model: str | None = None,
    pronunciations: dict[str, str] | None = None,
    check: bool = True,
) -> Clip:
    cfg = settings()
    model = model or cfg["model"]
    voice = voice or cfg["voice"]
    said = spoken_text(text, {**cfg["pronunciations"], **(pronunciations or {})})
    key = hashlib.sha256(f"{model}\0{voice}\0{said}".encode()).hexdigest()[:20]
    out = paths.sub("tts") / f"{key}.mp3"
    cached = out.exists() and out.stat().st_size > 0
    if not cached:
        synthesize(said, out, model, voice)
    heard = transcribe(out) if check else None
    return Clip(out, said, heard, similarity(said, heard) if heard is not None else None, cached)
