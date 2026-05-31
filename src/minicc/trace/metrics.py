from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from minicc.core.state import RunState


def metrics_path_for(state: RunState) -> Path | None:
    if state.run_dir is None:
        return None
    return state.run_dir / "metrics.json"


def write_metrics(state: RunState, path: Path | None = None) -> Path | None:
    target = path or metrics_path_for(state)
    if target is None:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(metrics_snapshot(state), ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def metrics_snapshot(state: RunState) -> dict[str, Any]:
    data = dict(state.metrics)
    data["run_id"] = state.run_id
    data["status"] = state.status
    data["final_answer_present"] = state.final_answer is not None
    return data
