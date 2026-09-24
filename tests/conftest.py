"""Suite-wide fixtures."""

import pytest

from headstart import spare_egress


@pytest.fixture(autouse=True)
def _in_memory_egress_daemon():
    """Every test gets a spare-egress daemon that never leaves the process (ADR-0195).

    Unstubbed, a rotation runs ``sudo -n systemctl restart warp-svc`` (``launchctl kickstart`` on a
    Mac) against the developer's real WARP daemon, and a dial or a trace goes down the real tunnel.
    One test did restart it (pid 96855 -> 97119). A test that exercises the real daemon's commands
    installs ``spare_egress.WarpDaemon()`` itself, after stubbing ``subprocess`` and the trace.
    """
    previous = spare_egress.use_daemon(spare_egress.InMemoryEgressDaemon())
    yield
    spare_egress.use_daemon(previous)
