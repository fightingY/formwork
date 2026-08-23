"""Re-entrant turn loop for V5 conversation sessions.

The session engine owns *what a turn means*:

1. load the session record and replay ``transcript.jsonl`` into a history list,
2. build a fresh ``RunState`` for the user message (``goal`` = this turn's message),
3. hand it to an injected ``loop_factory`` that assembles an :class:`AgentLoop`
   (provider, policy chain, executor -- all the settings wiring lives in
   ``cli.py`` so this module never imports the CLI),
4. run exactly one loop; if it pauses on a destructive-command approval gate,
   resolve it through the injected ``on_approval`` callback and resume until the
   turn reaches a terminal state,
5. append ``user``+``assistant`` rows to the transcript and record the run_id.

Each turn is still one ``run_id`` with its own trace/metrics under
``sessions/<id>/runs/<run_id>/`` (plan §3), so the eval/ledger evidence layer is
untouched.  The engine deliberately has no notion of UI: the CLI REPL and the
web chat server are both clients of ``submit_turn``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from minicc.core.loop import AgentLoop, AgentLoopResult, BashExecutor
from minicc.core.session import SessionManager
from minicc.core.session_store import SessionStore
from minicc.core.state import RunState, load_run_state

LoopFactory = Callable[[RunState], AgentLoop]
ApprovalCallback = Callable[[RunState], str]


@dataclass
class SessionTurnResult:
    run_id: str
    user_message: str
    assistant_reply: str
    status: str
    state: RunState
    loop_result: AgentLoopResult | None


# Turn-end memory seam (V5 §6 #7 / V5.1 §4.1): fired once per *committed* turn —
# after the loop returns, before the transcript rows are appended.  The L1
# distiller plugs in here once V5.1 lands; L0 is the transcript itself.  The
# hook must not raise (SessionEngine degrades a failing hook to a metric).
TurnEndHook = Callable[[str, SessionTurnResult], None]


def _is_deny(decision: str) -> bool:
    return decision.strip().lower().startswith("deny")


def _deny_reason(decision: str) -> str:
    decision = decision.strip()
    if decision.lower() == "deny":
        return "User denied the action."
    rest = decision[4:].lstrip(": ").strip()
    return rest or "User denied the action."


class SessionEngine:
    def __init__(
        self,
        store: SessionStore,
        *,
        loop_factory: LoopFactory,
        session: SessionManager | None = None,
        executor: BashExecutor | None = None,
        on_approval: ApprovalCallback | None = None,
        on_turn_end: TurnEndHook | None = None,
    ) -> None:
        self.store = store
        self._loop_factory = loop_factory
        self._session = session or SessionManager()
        self._executor = executor
        self._on_approval = on_approval
        self._on_turn_end = on_turn_end

    def submit_turn(self, session_id: str, user_message: str) -> SessionTurnResult:
        """Run one conversational turn for ``session_id`` and persist it."""
        record = self.store.load(session_id)
        history = self.store.history_messages(session_id)

        state = RunState.start(
            user_message,
            workspace_host_path=Path(record.project_root),
        )
        state.run_dir = self.store.session_runs_dir(session_id) / state.run_id
        state.artifacts_dir = state.run_dir / "artifacts"
        # Prior turns ride along on the state so ContextBuilder can inject them;
        # transcript.jsonl remains the single source of truth (plan §5.1).
        state.session_history = history

        loop_result = self._run_turn(state)
        reply = self._assistant_reply(state)

        result = SessionTurnResult(
            run_id=state.run_id,
            user_message=user_message,
            assistant_reply=reply,
            status=state.status,
            state=state,
            loop_result=loop_result,
        )

        # A deferred destructive-approval gate must NOT end the turn: the caller
        # resolves it via ``resolve_turn`` and the transcript rows are written
        # once the turn reaches a terminal state.
        if state.status == "waiting_approval" and state.pending_action is not None:
            return result

        self._invoke_turn_end(session_id, result)
        self.store.append_message(session_id, "user", user_message, run_id=state.run_id)
        self.store.append_message(session_id, "assistant", reply, run_id=state.run_id)
        self.store.add_turn(session_id, state.run_id)

        return result

    def resolve_turn(self, session_id: str, run_id: str, decision: str) -> SessionTurnResult:
        """Resume a turn that paused on a destructive-approval gate.

        Loads the run (``goal`` = the turn's user message), applies the
        approve/deny decision, resumes the loop to a terminal state, and only
        then writes the ``user``+``assistant`` rows into the transcript.
        """
        state = load_run_state(self.store.session_runs_dir(session_id) / run_id / "state.json")
        state.session_history = self.store.history_messages(session_id)

        if state.pending_action is None:
            raise ValueError(f"Run {run_id} has no pending action to resolve.")

        if _is_deny(decision):
            self._session.deny(state, _deny_reason(decision))
        else:
            self._session.approve(state)
        if self._executor is None:
            raise ValueError("A SessionEngine with no executor cannot resolve approvals.")
        self._session.apply_pending_approval_result(state, self._executor)

        loop_result = None
        if state.status == "running":
            loop_result = self._loop_factory(state).run(state)
        reply = self._assistant_reply(state)

        result = SessionTurnResult(
            run_id=run_id,
            user_message=state.goal,
            assistant_reply=reply,
            status=state.status,
            state=state,
            loop_result=loop_result,
        )

        self._invoke_turn_end(session_id, result)
        self.store.append_message(session_id, "user", state.goal, run_id=run_id)
        self.store.append_message(session_id, "assistant", reply, run_id=run_id)
        self.store.add_turn(session_id, run_id)

        return result

    def _run_turn(self, state: RunState) -> AgentLoopResult:
        result = self._loop_factory(state).run(state)
        # Destructive-command approval gates continue the SAME turn (plan §4:
        # chat safety = approval gate, not snapshot copies).  ``ask`` actions
        # (no pending_action) end the turn, so the question becomes the reply.
        if self._executor is None or self._on_approval is None:
            return result
        while state.status == "waiting_approval" and state.pending_action is not None:
            decision = self._on_approval(state)
            if _is_deny(decision):
                self._session.deny(state, _deny_reason(decision))
            else:
                self._session.approve(state)
            self._session.apply_pending_approval_result(state, self._executor)
            # apply_pending_approval_result mutates state.status; cast so mypy
            # does not keep the loop-entry ``waiting_approval`` narrowing.
            if cast(str, state.status) != "running":
                break
            result = self._loop_factory(state).run(state)
        return result

    def _invoke_turn_end(self, session_id: str, result: SessionTurnResult) -> None:
        """Fire the L1 distillation seam; a failing hook must never fail the turn.

        This is the extension point V5.1 fills with the SQLite/FTS5 L1 distiller
        (V5_1_MEMORY_REDESIGN_PLAN §4.1).  L0 — the transcript — is already the
        hook's input source; here we only guarantee the callback runs at the
        committed-turn boundary and degrades to a metric on error.
        """
        if self._on_turn_end is None:
            return
        try:
            self._on_turn_end(session_id, result)
        except Exception:
            metrics = result.state.metrics
            metrics["memory_turn_end_hook_errors"] = int(
                metrics.get("memory_turn_end_hook_errors", 0)
            ) + 1

    @staticmethod
    def _assistant_reply(state: RunState) -> str:
        if state.final_answer:
            return state.final_answer
        if state.status == "waiting_approval":
            if state.approval_question:
                return state.approval_question
            if state.open_questions:
                return state.open_questions[-1]
            return "Waiting for approval."
        if state.state_summary:
            return state.state_summary
        return ""