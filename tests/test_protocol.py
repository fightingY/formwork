import pytest

from minicc.core.protocol import AskAction, BashAction, FinalAction, ProtocolError, parse_action


def test_parse_bash_action_defaults_timeout() -> None:
    action = parse_action('{"type":"bash","command":"pytest -q","purpose":"run tests"}')

    assert action == BashAction(command="pytest -q", timeout_sec=60, purpose="run tests")


def test_parse_ask_action() -> None:
    action = parse_action('{"type":"ask","question":"Allow network access?"}')

    assert action == AskAction(question="Allow network access?")


def test_parse_final_action() -> None:
    action = parse_action('{"type":"final","answer":"Done."}')

    assert action == FinalAction(answer="Done.")


def test_rejects_markdown_wrapped_json() -> None:
    with pytest.raises(ProtocolError):
        parse_action('```json\n{"type":"final","answer":"Done."}\n```')


def test_caps_timeout_to_loop_budget() -> None:
    action = parse_action(
        '{"type":"bash","command":"sleep 999","timeout_sec":999}',
        max_timeout_sec=120,
    )

    assert isinstance(action, BashAction)
    assert action.timeout_sec == 120
