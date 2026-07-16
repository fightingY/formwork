from minicc.core.action_handler import ActionHandler
from minicc.core.protocol import AskAction, BashAction, FinalAction
from minicc.core.state import Observation, RunState
from minicc.policy.base import PolicyChain, PolicyDecision
from minicc.trace.recorder import TraceRecorder


class FakeExecutor:
    def __init__(self) -> None:
        self.actions: list[BashAction] = []

    def run(self, action: BashAction, state: RunState) -> Observation:
        self.actions.append(action)
        return Observation(kind="command_result", exit_code=0, stdout_preview="ok")


def test_action_handler_final_completes_state() -> None:
    state = RunState.start("finish")

    outcome = ActionHandler(FakeExecutor()).handle(FinalAction(answer="done"), state)

    assert outcome.should_continue is False
    assert state.status == "completed"
    assert state.final_answer == "done"


def test_action_handler_ask_waits_and_saves_state(tmp_path) -> None:
    state = RunState.start("ask", run_dir=tmp_path)

    outcome = ActionHandler(FakeExecutor()).handle(AskAction(question="Which branch?"), state)

    assert outcome.should_continue is False
    assert state.status == "waiting_approval"
    assert state.approval_question == "Which branch?"
    assert (tmp_path / "state.json").exists()


def test_action_handler_executes_rewritten_action() -> None:
    class RewritePolicy:
        name = "RewritePolicy"

        def evaluate(self, action: BashAction, state: RunState) -> PolicyDecision:
            return PolicyDecision(
                type="rewrite",
                reason="shorten timeout",
                rewritten_action=BashAction(command=action.command, timeout_sec=5),
                policy_name=self.name,
            )

    executor = FakeExecutor()
    state = RunState.start("rewrite")

    outcome = ActionHandler(
        executor,
        policy_chain=PolicyChain([RewritePolicy()]),
    ).handle(BashAction(command="sleep 100", timeout_sec=60), state)

    assert outcome.steps[0].action == BashAction(command="sleep 100", timeout_sec=5)
    assert executor.actions == [BashAction(command="sleep 100", timeout_sec=5)]
    assert state.metrics["bash_actions"] == 1


def test_action_handler_traces_action_and_policy_error_observation() -> None:
    class DenyPolicy:
        name = "DenyPolicy"

        def evaluate(self, action: BashAction, state: RunState) -> PolicyDecision:
            return PolicyDecision(type="deny", reason="blocked", policy_name=self.name)

    trace = TraceRecorder()
    state = RunState.start("deny trace")

    outcome = ActionHandler(
        FakeExecutor(),
        policy_chain=PolicyChain([DenyPolicy()]),
        trace=trace,
    ).handle(BashAction(command="rm -rf /"), state)

    assert outcome.steps[0].observation.kind == "policy_violation"
    assert [event["event"] for event in trace.events] == [
        "action_started",
        "policy_decision",
        "observation_created",
    ]
    assert trace.events[0]["action"]["type"] == "bash"
    assert trace.events[1]["decision_type"] == "deny"
