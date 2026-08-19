import os
import sys
from pathlib import Path

workspace = Path(os.environ["MINICC_WORKSPACE"]).resolve()
sys.path.insert(0, str(workspace))
from scale_generator import chromatic_scale  # noqa: E402

if chromatic_scale("C") != ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]:
    raise SystemExit("C chromatic scale mismatch")
if chromatic_scale("F#") != ["F#", "G", "G#", "A", "A#", "B", "C", "C#", "D", "D#", "E", "F"]:
    raise SystemExit("enharmonic tonic mismatch")
if chromatic_scale("Bb", "mmmmmmmmmmmm") != ["Bb", "B", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A"]:
    raise SystemExit("flat tonic mismatch")
raise SystemExit(0)
