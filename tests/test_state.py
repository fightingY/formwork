import re

from minicc.core.protocol import BashAction
from minicc.core.state import RunState, load_run_state, new_run_id, save_run_state


def test_new_run_id_has_sortable_timestamp_prefix() -> None:
    run_id = new_run_id()

    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{8}", run_id)


def test_run_state_round_trips_pending_action(tmp_path) -> None:
    state = RunState.start("install deps", run_dir=tmp_path)
    state.status = "waiting_approval"
    state.pending_action = BashAction(command="pip install pytest", timeout_sec=9, purpose="install")
    state.approval_question = "Approve network?"

    path = save_run_state(state)
    loaded = load_run_state(path)

    assert loaded.run_id == state.run_id
    assert loaded.run_dir == tmp_path
    assert loaded.status == "waiting_approval"
    assert loaded.pending_action == state.pending_action
    assert loaded.approval_question == "Approve network?"
