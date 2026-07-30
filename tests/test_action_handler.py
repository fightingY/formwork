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


def test_action_handler_persists_ambiguous_execution_before_executor_failure(tmp_path) -> None:
    class FailingExecutor:
        def run(self, action: BashAction, state: RunState) -> Observation:
            raise RuntimeError("process disappeared")

    state = RunState.start("ambiguous", run_dir=tmp_path)

    try:
        ActionHandler(FailingExecutor()).handle(BashAction(command="write-change"), state)
    except RuntimeError as exc:
        assert "process disappeared" in str(exc)
    else:
        raise AssertionError("Expected executor failure")

    assert state.execution_journal == [
        {
            "execution_id": "execution-0001",
            "status": "started",
            "command": "write-change",
            "started_at": state.execution_journal[0]["started_at"],
        }
    ]


def test_action_handler_counts_repeated_reads_and_searches() -> None:
    state = RunState.start("inspect")
    handler = ActionHandler(FakeExecutor())

    handler.handle(BashAction(command="rg TODO src"), state)
    handler.handle(BashAction(command="rg TODO src"), state)
    handler.handle(BashAction(command="Get-Content src/app.py"), state)
    handler.handle(BashAction(command="Get-Content src/app.py"), state)
    blocked = handler.handle(BashAction(command="Get-Content src/app.py"), state)

    assert state.metrics["search_actions"] == 2
    assert state.metrics["repeated_searches"] == 1
    assert state.metrics["file_read_actions"] == 3
    assert state.metrics["repeated_file_reads"] == 2
    assert blocked.steps[0].observation.kind == "policy_violation"
    assert "repeated I/O guard" in blocked.steps[0].observation.message


def test_action_handler_does_not_count_cat_heredoc_writes_as_file_reads() -> None:
    state = RunState.start("write fixture")
    handler = ActionHandler(FakeExecutor())

    handler.handle(
        BashAction(
            command="cat > src/app.py << 'EOF'\nprint('first')\nEOF",
        ),
        state,
    )
    handler.handle(
        BashAction(
            command="cat << 'EOF' > src/other.py\nprint('second')\nEOF",
        ),
        state,
    )
    handler.handle(BashAction(command="cat src/app.py"), state)

    assert state.metrics["file_read_actions"] == 1
    assert state.metrics["repeated_file_reads"] == 0


def test_action_handler_finds_cat_read_after_multiline_heredoc_write() -> None:
    state = RunState.start("write then read")
    handler = ActionHandler(FakeExecutor())

    handler.handle(
        BashAction(
            command=(
                "cat > src/app.py << 'EOF'\n"
                "print('first')\n"
                "EOF\n"
                "cat src/secret.py"
            ),
        ),
        state,
    )

    assert state.metrics["file_read_actions"] == 1


def test_action_handler_counts_cat_input_redirection_and_wrapped_cat_reads() -> None:
    state = RunState.start("redirected reads")
    handler = ActionHandler(FakeExecutor())

    handler.handle(BashAction(command="cat > output.txt < input.txt"), state)
    handler.handle(BashAction(command="/bin/cat src/app.py"), state)
    handler.handle(BashAction(command="command cat src/other.py"), state)

    assert state.metrics["file_read_actions"] == 3


def test_action_handler_ignores_comparison_operators_inside_heredoc_body() -> None:
    state = RunState.start("write comparison")
    handler = ActionHandler(FakeExecutor())

    handler.handle(
        BashAction(
            command=(
                "cat > src/validator.py << 'EOF'\n"
                "ok = all('0' <= char <= '9' for char in suffix)\n"
                "EOF"
            ),
        ),
        state,
    )

    assert state.metrics["file_read_actions"] == 0


def test_action_handler_counts_file_argument_after_output_redirection_as_read() -> None:
    state = RunState.start("copy input")
    handler = ActionHandler(FakeExecutor())

    handler.handle(
        BashAction(command="cat > output.txt input.txt"),
        state,
    )

    assert state.metrics["file_read_actions"] == 1
