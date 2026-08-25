import os
import sys
from pathlib import Path

workspace = Path(os.environ["MINICC_WORKSPACE"]).resolve()
sys.path.insert(0, str(workspace))
from variable_length_quantity import decode, encode  # noqa: E402

for number, encoded in {
    0: [0],
    127: [127],
    128: [129, 0],
    8192: [192, 0],
    16383: [255, 127],
    16384: [129, 128, 0],
    0x0FFFFFFF: [255, 255, 255, 127],
}.items():
    if encode(number) != encoded or decode(encoded) != number:
        raise SystemExit(f"VLQ mismatch for {number}")
raise SystemExit(0)
