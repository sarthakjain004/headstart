"""The A/B harness's arm ordering (scripts/bench/tech_gate_bench.py).

Only the ordering: the rest of that script talks to live ATS origins. But the ordering is what
makes the measurement valid or not — a harness that always ran the gated arm second produced a
one-directional `desc_lost` on `jll.wd1.myworkdayjobs.com` (2, 47, 7, 59 across four pairs) that
was read as damage from the gate before the confound was spotted.
"""

import importlib.util
import pathlib

_SPEC = importlib.util.spec_from_file_location(
    "tech_gate_bench",
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts"
    / "bench"
    / "tech_gate_bench.py",
)
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)


def test_arm_order_alternates_so_neither_arm_is_always_second():
    assert bench.arm_order(0) == (False, True), "even repeat: control first"
    assert bench.arm_order(1) == (True, False), "odd repeat: gated first"
    assert bench.arm_order(2) == (False, True)

    # Over any even number of repeats each arm leads exactly half the time, which is the property
    # that makes a surviving one-directional result the mechanism rather than the running order.
    leads = [bench.arm_order(r)[0] for r in range(6)]
    assert leads.count(True) == leads.count(False) == 3


def test_ran_first_agrees_with_the_order_it_reports():
    for rep in range(4):
        first, second = bench.arm_order(rep)
        assert bench.ran_first(rep, first) is True
        assert bench.ran_first(rep, second) is False
