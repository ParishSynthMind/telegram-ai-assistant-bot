#!/usr/bin/env python3
"""Transcribe one audio file to text, locally and free, with faster-whisper.
Usage: python transcribe.py <audio file>   (prints only the transcript)

Audio is decoded with ffmpeg into a float array first, so any format
Telegram sends (voice notes are Opus in an Ogg container) works."""
import subprocess
import sys

import numpy as np
from faster_whisper import WhisperModel

raw = subprocess.run(
    ["ffmpeg", "-nostdin", "-v", "error", "-i", sys.argv[1], "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
    capture_output=True, check=True,
).stdout
audio = np.frombuffer(raw, dtype=np.float32)
model = WhisperModel("small.en", device="cpu", compute_type="int8")  # English; use "small" for other languages
segments, _ = model.transcribe(audio, vad_filter=True, beam_size=5)
print(" ".join(s.text.strip() for s in segments).strip())
