from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from minicc.core.protocol import BashAction
from minicc.core.state import RunState


DecisionType = Literal["allow", "deny", "require_approval", "rewrite"]


@dataclass(frozen=True)
class PolicyDecision:
    type: DecisionType
    reason: str
    rewritten_action: BashAction | None = None
    approval_question: str | None = None
    policy_name: str = ""


class Policy(Protocol):
    name: str

    def evaluate(self, action: BashAction, state: RunState) -> PolicyDecision:
        ...


class PolicyChain:
    def __init__(self, policies: list[Policy] | None = None) -> None:
        self.policies = policies or []

    def evaluate(self, action: BashAction, state: RunState) -> PolicyDecision:
        current_action = action
        rewrite_decision: PolicyDecision | None = None

        for policy in self.policies:
            decision = policy.evaluate(current_action, state)
            if decision.type == "allow":
                continue
            if decision.type == "rewrite":
                if decision.rewritten_action is None:
                    return PolicyDecision(
                        type="deny",
                        reason=f"{policy.name} returned rewrite without rewritten_action.",
                        policy_name=policy.name,
                    )
                current_action = decision.rewritten_action
                rewrite_decision = decision
                continue
            return decision

        if rewrite_decision is not None:
            return rewrite_decision
        return PolicyDecision(type="allow", reason="All policies allowed action.", policy_name="PolicyChain")
