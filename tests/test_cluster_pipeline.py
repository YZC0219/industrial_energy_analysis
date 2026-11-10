import pytest
from tools.cluster_pipeline import require_idle


def test_manual_trigger_requires_exclusive_idle_writer():
    idle = dict(old_writer_paused=True, old_active_runs=0, old_active_tasks=0, active_runs=0)
    require_idle(idle)
    for key, value in [('old_writer_paused', False), ('old_active_runs', 1),
                       ('old_active_tasks', 1), ('active_runs', 1)]:
        with pytest.raises(ValueError):
            require_idle({**idle, key: value})
