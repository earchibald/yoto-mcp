---
name: voice-prompts
description: Making spoken prompts for Yoto cards with yoto_tts — display text vs spoken text, the pronunciation map, the default voice, transcript checks and when to regenerate. Use when a card needs announcements, choice prompts, title tracks or a spoken intro.
---

# Spoken prompts

`yoto_tts(text)` speaks text on this machine (ollama, default model `phonon`, voice `Harper`),
transcribes the clip with whisper, uploads it, and returns media to use as a track's `audio`.

- **Display text vs spoken text.** Pass the text as the card should show it ("Hilo Book 4: Waking
  the Monsters"). The pronunciation map rewrites it for speaking: "Hilo" becomes "High-low" by
  default. Pass more as `pronunciations={"Gina": "Jee-na"}`. Dashes become pauses.
- **Check the transcript.** Each result has `transcript` and `match` (0–1). Below 0.75 the result
  carries a warning. Tell the user, and suggest a pronunciation or a rewording. Whisper often hears
  invented names wrong even when they're spoken right ("High-low" → "Hi, low"), so a low match on a
  name means listen, not necessarily fix.
- **Same text, same clip.** Clips are cached by model, voice and spoken text, and Yoto dedupes the
  upload. Re-running a template reuses the same audio, so its plan is a no-op.
- **Defaults** live in `~/.yoto/voice.json` (`model`, `voice`, `pronunciations`), if the user wants
  to change them for good.
- **Keep prompts short and instructive** for a child: "For book 5, click left. For other books,
  click right."
- **Regenerate** a spoken intro whenever chapters are added, removed or moved, because it names
  track numbers. Plans warn about this.
- If `yoto_tts` says ollama or ffmpeg is missing, the user needs `ollama pull phonon` and
  `brew install ffmpeg`. Speech-to-text is optional (`mlx_whisper` or `whisper`).
