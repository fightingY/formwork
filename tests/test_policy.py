from minicc.config import (
    BudgetSettings,
    ContextSettings,
    PolicySettings,
    SandboxSettings,
    Settings,
)
from minicc.core.protocol import BashAction
from minicc.core.state import RunState
from minicc.policy.approval import ApprovalPolicy
from minicc.policy.base import PolicyChain
from minicc.policy.budget import BudgetPolicy
from minicc.policy.command import CommandPolicy
from minicc.policy.factory import build_policy_chain
from minicc.policy.network import NetworkPolicy
from minicc.policy.path import PathPolicy


def test_command_policy_denies_sudo() -> None:
    decision = CommandPolicy().evaluate(BashAction(command="sudo apt update"), RunState.start("test"))

    assert decision.type == "deny"
    assert decision.policy_name == "CommandPolicy"


def test_path_policy_denies_sensitive_path() -> None:
    decision = PathPolicy().evaluate(BashAction(command="cat /root/.ssh/id_rsa"), RunState.start("test"))

    assert decision.type == "deny"


def test_network_policy_requires_approval_in_locked_mode() -> None:
    decision = NetworkPolicy(mode="locked", require_approval=True).evaluate(
        BashAction(command="pip install pytest"),
        RunState.start("test"),
    )

    assert decision.type == "require_approval"
    assert "network" in decision.reason.lower()


def test_budget_policy_rewrites_timeout() -> None:
    decision = BudgetPolicy(max_action_timeout_sec=5).evaluate(
        BashAction(command="sleep 10", timeout_sec=60),
        RunState.start("test"),
    )

    assert decision.type == "rewrite"
    assert decision.rewritten_action is not None
    assert decision.rewritten_action.timeout_sec == 5


def test_approval_policy_requires_approval_for_destructive_command() -> None:
    decision = ApprovalPolicy().evaluate(BashAction(command="rm -r build"), RunState.start("test"))

    assert decision.type == "require_approval"


def test_approval_policy_requires_approval_for_combined_rm_flags() -> None:
    policy = ApprovalPolicy()

    assert policy.evaluate(BashAction(command="rm -rf tmp_build"), RunState.start("test")).type == "require_approval"
    assert policy.evaluate(BashAction(command="rm -fr tmp_build"), RunState.start("test")).type == "require_approval"
    assert (
        policy.evaluate(BashAction(command="rm --recursive --force tmp_build"), RunState.start("test")).type
        == "require_approval"
    )


def test_policy_chain_denies_dangerous_rm_before_approval() -> None:
    decision = PolicyChain([CommandPolicy(), ApprovalPolicy()]).evaluate(
        BashAction(command="rm -rf /"),
        RunState.start("test"),
    )

    assert decision.type == "deny"
    assert decision.policy_name == "CommandPolicy"


def test_policy_chain_allows_normal_locked_command_without_approval() -> None:
    decision = PolicyChain(
        [
            CommandPolicy(),
            PathPolicy(),
            NetworkPolicy(mode="locked", require_approval=True),
            ApprovalPolicy(),
        ]
    ).evaluate(BashAction(command="python -m unittest discover -s tests"), RunState.start("test"))

    assert decision.type == "allow"


def test_network_policy_can_deny_in_locked_mode_without_approval() -> None:
    decision = NetworkPolicy(mode="locked", require_approval=False).evaluate(
        BashAction(command="curl https://example.test"),
        RunState.start("test"),
    )

    assert decision.type == "deny"


def test_network_policy_ignores_network_words_inside_file_content() -> None:
    policy = NetworkPolicy(mode="locked", require_approval=False)
    heredoc = """cat > ONBOARDING.md << 'EOF'
## Risk
The package currently has no `pip install` workflow.
EOF"""

    assert policy.evaluate(BashAction(command=heredoc), RunState.start("test")).type == "allow"
    assert (
        policy.evaluate(
            BashAction(command="printf '%s\\n' 'No pip install workflow' > ONBOARDING.md"),
            RunState.start("test"),
        ).type
        == "allow"
    )


def test_network_policy_still_denies_executed_package_install_commands() -> None:
    policy = NetworkPolicy(mode="locked", require_approval=False)

    assert policy.evaluate(BashAction(command="pip install pytest"), RunState.start("test")).type == "deny"
    assert (
        policy.evaluate(
            BashAction(command="cd /workspace && python -m pip install pytest"),
            RunState.start("test"),
        ).type
        == "deny"
    )
    assert (
        policy.evaluate(
            BashAction(command="bash -c 'pip install pytest'"),
            RunState.start("test"),
        ).type
        == "deny"
    )


def test_approval_policy_can_be_disabled() -> None:
    decision = ApprovalPolicy(enabled=False).evaluate(BashAction(command="rm -r build"), RunState.start("test"))

    assert decision.type == "allow"


def test_policy_chain_returns_first_blocking_decision() -> None:
    decision = PolicyChain(
        [
            CommandPolicy(),
            NetworkPolicy(mode="locked", require_approval=True),
        ]
    ).evaluate(BashAction(command="sudo pip install pytest"), RunState.start("test"))

    assert decision.type == "deny"
    assert decision.policy_name == "CommandPolicy"


def test_policy_factory_always_includes_approval_policy() -> None:
    settings = Settings(
        sandbox=SandboxSettings(),
        budget=BudgetSettings(),
        context=ContextSettings(),
        policy=PolicySettings(require_approval_for_destructive=False),
    )

    chain = build_policy_chain(settings)

    assert [policy.name for policy in chain.policies] == [
        "CommandPolicy",
        "PathPolicy",
        "NetworkPolicy",
        "BudgetPolicy",
        "ApprovalPolicy",
    ]
    decision = chain.evaluate(BashAction(command="rm -r build"), RunState.start("test"))
    assert decision.type == "allow"
