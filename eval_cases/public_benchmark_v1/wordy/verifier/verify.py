import os
import sys
from pathlib import Path

workspace = Path(os.environ["MINICC_WORKSPACE"]).resolve()
if not workspace.is_dir() or workspace.parent == workspace:
    raise SystemExit("invalid workspace")
sys.path.insert(0, str(workspace))
from wordy import answer  # noqa: E402

CASES = {
    "What is 5?": 5,
    "What is 5 plus 13?": 18,
    "What is 7 minus 3?": 4,
    "What is 6 multiplied by 4?": 24,
    "What is 20 divided by 5?": 4,
    "What is 2 plus 3 multiplied by 4?": 14,
}
for question, expected in CASES.items():
    if answer(question) != expected:
        raise SystemExit(f"wrong answer for {question!r}")
raise SystemExit(0)
