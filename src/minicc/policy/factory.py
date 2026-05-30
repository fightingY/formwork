from __future__ import annotations

from minicc.config import Settings
from minicc.policy.approval import ApprovalPolicy
from minicc.policy.base import PolicyChain
from minicc.policy.budget import BudgetPolicy
from minicc.policy.command import CommandPolicy
from minicc.policy.network import NetworkPolicy
from minicc.policy.path import PathPolicy


def build_policy_chain(settings: Settings) -> PolicyChain:
    policies = [
        CommandPolicy(deny_sudo=settings.policy.deny_sudo),
        PathPolicy(),
        NetworkPolicy(
            mode=settings.sandbox.mode,
            require_approval=settings.policy.require_approval_for_network,
        ),
        BudgetPolicy(
            max_bash_actions=settings.budget.max_bash_actions,
            max_action_timeout_sec=settings.budget.max_action_timeout_sec,
        ),
        ApprovalPolicy(enabled=settings.policy.require_approval_for_destructive),
    ]
    return PolicyChain(policies)
