"""V4 childrun compatibility exports."""
from minicc.multi_agent import (
    ChildEvent,
    ChildResult,
    ChildRunProvider,
    InProcessChildRunProvider,
    SubprocessChildRunProvider,
    childrun_main,
)

__all__ = [
    "ChildEvent", "ChildResult", "ChildRunProvider", "InProcessChildRunProvider",
    "SubprocessChildRunProvider", "childrun_main",
]
