import os
import sys
from pathlib import Path

workspace = Path(os.environ["MINICC_WORKSPACE"]).resolve()
sys.path.insert(0, str(workspace))
from simple_linked_list import LinkedList  # noqa: E402

items = LinkedList([1, 2, 3])
if list(items) != [3, 2, 1] or items.pop() != 3 or list(items) != [2, 1]:
    raise SystemExit("push/pop contract failed")
if list(items.reversed()) != [1, 2]:
    raise SystemExit("reverse contract failed")
if LinkedList().pop() is not None:
    raise SystemExit("empty pop must return None")
raise SystemExit(0)
