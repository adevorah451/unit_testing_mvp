"""One protocol, one run, one artifact.

A protocol turns a candidate into a verdict and writes its own run directory, so nothing scores a
second protocol as a side effect of scoring the first.

    from pipeline.data import Dataset
    from pipeline.protocols import TM

    data = Dataset.load("data/apps_hard.json")
    tm = TM(run_name="tm-v1", data=data, model=MODEL, runs=2)
    tm.run()
    rows = tm.get_records("test")

**Every protocol module is imported here, and that import IS its registration.** `Run.__init_subclass__`
is the only thing that fills `REGISTRY`, and `base.main` looks a run's protocol up there to rebuild it
in the subprocess — so deleting one of these as an unused name takes its protocol out of every
detached run and out of `Run.attach`.
"""

from __future__ import annotations

from .base import REGISTRY, Echo, Run, RunFailed, main
from .monitor_with_tests import MonitorWithTests
from .solve import TrustedSolve
from .tm import TM, TrustedMonitor
from .trigger_search import TriggerSearch
from .u_plans_t_writes import UPlansTWrites
from .unit_testing import UnitTesting
from .test_repair import TestRepair
from .second_revision import SecondRevision

__all__ = [
    "REGISTRY",
    "Echo",
    "MonitorWithTests",
    "Run",
    "RunFailed",
    "TM",
    "TrustedMonitor",
    "TrustedSolve",
    "TriggerSearch",
    "UPlansTWrites",
    "UnitTesting",
    "TestRepair",
    "SecondRevision",
    "main",
]
