import os
import sys
from pathlib import Path

workspace = Path(os.environ["MINICC_WORKSPACE"]).resolve()
sys.path.insert(0, str(workspace))
from rest_api import UserStore  # noqa: E402

store = UserStore()
alice = store.create("Alice")
if alice != {"id": 1, "name": "Alice"}:
    raise SystemExit("create contract failed")
if store.get(1) != alice or store.get(99) is not None:
    raise SystemExit("get contract failed")
if store.update(1, "Alicia") != {"id": 1, "name": "Alicia"}:
    raise SystemExit("update contract failed")
if not store.delete(1) or store.delete(1) or store.get(1) is not None:
    raise SystemExit("delete contract failed")
raise SystemExit(0)
