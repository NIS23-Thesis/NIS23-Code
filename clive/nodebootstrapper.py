"""
CLIVE Node and NodeBootstrapper.

Node represents one emulated device.

The protocol is supplied separately. CLIVE does not implement FLAME
protocol logic.
"""

from __future__ import annotations

import json
import os
import time

from nodesocket import NodeSocket
from orchestrator import EmulationPlan


class Protocol:
    """
    Minimal protocol interface.

    A real protocol such as FLAME can implement this interface later.
    """

    def __init__(self, node):
        self.node = node

    def step(self, tick):
        pass

    def receive(self, data, source):
        pass

    def close(self):
        pass


class SimpleProtocol(Protocol):
    """
    Small protocol used only to verify that CLIVE works.

    Every second, the node sends a small payload to every other node.
    """

    def __init__(self, node):
        super().__init__(node)
        self.events = []

    def step(self, tick):
        interval = max(
            1,
            int(
                1.0
                / self.node.plan.mobility_config.tick_interval
            ),
        )

        if tick % interval != 0:
            return

        message = (
            f"HELLO FROM {self.node.spec.name} "
            f"AT TICK {tick}"
        ).encode()

        for other in self.node.plan.nodes:

            if other.name == self.node.spec.name:
                continue

            sent = self.node.sendto(
                message,
                (
                    other.overlay_ip,
                    other.overlay_port,
                ),
            )

            self.events.append({
                "type": "send",
                "tick": tick,
                "destination": other.name,
                "sent": sent,
            })

    def receive(self, data, source):
        self.events.append({
            "type": "receive",
            "source": source,
            "data": data.decode(
                errors="replace"
            ),
        })


class Node:

    def __init__(
        self,
        spec,
        plan: EmulationPlan,
        node_socket: NodeSocket,
        protocol_class=SimpleProtocol,
    ):
        self.spec = spec
        self.plan = plan
        self.socket = node_socket

        self.protocol = protocol_class(self)

    def sendto(self, data, destination):
        return self.socket.sendto(
            data,
            destination,
        )

    def _receive_available(self):
        while True:
            result = self.socket.recvfrom()

            if result is None:
                break

            data, source = result

            self.protocol.receive(
                data,
                source,
            )

    def run(self):
        tick_interval = (
            self.plan.mobility_config.tick_interval
        )

        duration = self.plan.config.duration_s

        start = time.monotonic()
        last_tick = -1

        while True:
            elapsed = time.monotonic() - start

            if elapsed >= duration:
                break

            tick = int(
                elapsed / tick_interval
            )

            if tick != last_tick:
                self.socket.set_tick(tick)

                self._receive_available()

                self.protocol.step(tick)

                last_tick = tick

            next_tick = (
                start
                + (tick + 1) * tick_interval
            )

            time.sleep(
                max(
                    0.0,
                    min(
                        tick_interval,
                        next_tick
                        - time.monotonic(),
                    ),
                )
            )

        self._receive_available()
        self.protocol.close()
        self.socket.close()

        return (
            self.socket.metrics.snapshot(),
            getattr(
                self.protocol,
                "events",
                [],
            ),
        )


def run_node(
    node_name,
    plan_path,
    results_dir,
):
    """
    Multiprocessing entry point for one Node.
    """

    plan = EmulationPlan.load(
        plan_path
    )

    spec = plan.node_by_name(
        node_name
    )

    node_socket = NodeSocket(
        spec,
        plan,
        plan.config.seed,
    )

    node = Node(
        spec,
        plan,
        node_socket,
        protocol_class=SimpleProtocol,
    )

    metrics, events = node.run()

    os.makedirs(
        results_dir,
        exist_ok=True,
    )

    with open(
        os.path.join(
            results_dir,
            f"{node_name}.metrics.json",
        ),
        "w",
    ) as f:
        json.dump(
            metrics,
            f,
            indent=2,
        )

    with open(
        os.path.join(
            results_dir,
            f"{node_name}.events.jsonl",
        ),
        "w",
    ) as f:
        for event in events:
            f.write(
                json.dumps(event)
                + "\n"
            )

    return node_name