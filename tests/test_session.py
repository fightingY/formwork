from minicc.core.protocol import BashAction
from minicc.core.session import SessionManager
from minicc.core.state import Observation, RunState


class FakeExecutor:
    def __init__(self) -> None:
        self.actions: list[BashAction] = []

    def run(self, action: BashAction, state: RunState) -> Observation:
        self.actions.append(action)
        return Observation(kind="command_result", exit_code=0, stdout_preview="approved")


def test_session_manager_approve_then_apply_executes_pending_action(tmp_path) -> None:
    state = RunState.start("approve", run_dir=tmp_path)
    state.status = "waiting_approval"
    state.pending_action = BashAction(command="echo ok")
    session = SessionManager()
    executor = FakeExecutor()

    session.approve(state)
    session.apply_pending_approval_result(state, executor)

    assert state.status == "running"
    assert state.pending_action is None
    assert executor.actions == [BashAction(command="echo ok")]
    assert state.metrics["bash_actions"] == 1
    assert state.last_observation is not None
    assert state.last_observation.stdout_preview == "approved"


def test_session_manager_deny_then_apply_returns_approval_observation(tmp_path) -> None:
    state = RunState.start("deny", run_dir=tmp_path)
    state.status = "waiting_approval"
    state.pending_action = BashAction(command="pip install pytest")
    session = SessionManager()

    session.deny(state, "no network")
    session.apply_pending_approval_result(state, FakeExecutor())

    assert state.status == "running"
    assert state.pending_action is None
    assert state.last_observation is not None
    assert state.last_observation.kind == "approval_result"
    assert "no network" in state.last_observation.message
