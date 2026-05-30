import re

from minicc.core.state import new_run_id


def test_new_run_id_has_sortable_timestamp_prefix() -> None:
    run_id = new_run_id()

    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{8}", run_id)
