"""Writes scripts/audio_lines.json (tier 4-5 lines) for scripts/make_audio.ps1 to record."""
import json

from .config import ROOT
from .messages import AUDIO_KEYS, MESSAGES

if __name__ == "__main__":
    (ROOT / "scripts" / "audio_lines.json").write_text(json.dumps({k: MESSAGES[k]["text"] for k in AUDIO_KEYS}, indent=1))
    print(f"wrote {len(AUDIO_KEYS)} lines")
