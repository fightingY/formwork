import os
import sys
from pathlib import Path

workspace = Path(os.environ["MINICC_WORKSPACE"]).resolve()
sys.path.insert(0, str(workspace))
from go_counting import score  # noqa: E402

cases = {
    (".",): (0, 0),
    ("X.", ".O"): (1, 1),
    ("XX.", "..O"): (2, 1),
    ("XXX", "OO.", "..."): (3, 2),
}
for board, expected in cases.items():
    if score(list(board)) != expected:
        raise SystemExit(f"score mismatch for {board!r}")
raise SystemExit(0)
