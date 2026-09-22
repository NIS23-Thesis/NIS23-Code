"""
CLIVE Emulator.

Loads an EmulationPlan and launches one process for each
emulated Node.
"""

from __future__ import annotations

import multiprocessing as mp
import os

from nodebootstrapper import run_node
from orchestrator import EmulationPlan


def _prefer_fork():
    if "fork" in mp.get_all_start_methods():
        try:
            mp.set_start_method(
                "fork",
                force=True,
            )
        except RuntimeError:
            pass


_prefer_fork()


class Emulator:

    def __init__(
        self,
        plan_path: str,
        results_dir: str,
    ):
        self.plan_path = plan_path
        self.results_dir = results_dir

        self.plan = EmulationPlan.load(
            plan_path
        )

    def run(self):
        os.makedirs(
            self.results_dir,
            exist_ok=True,
        )

        processes = []

        for node in self.plan.nodes:

            process = mp.Process(
                target=run_node,
                args=(
                    node.name,
                    self.plan_path,
                    self.results_dir,
                ),
            )

            process.start()
            processes.append(process)

        for process in processes:
            process.join()

        return self.results_dir