import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flame.communication import PacketType
from flame.neighbor import AssociationState, NeighborModule, NodeRole
from tests.simulation.run_node import InteractiveNode


class _RecordingOutput:
    def __init__(self):
        self.messages = []

    def write(self, message):
        self.messages.append(message)


class _RecordingCommunication:
    def __init__(self):
        self.messages = []

    def send_message(self, message, destination):
        self.messages.append((message, destination))


class RunNodeAssociationRefreshTests(unittest.TestCase):
    def test_associated_sensor_refreshes_membership_on_hello(self):
        node = InteractiveNode.__new__(InteractiveNode)
        node.role = 0
        node.model_id = 1
        node._active_cluster_head = None
        node._next_join_id = 16
        node.neighbor = NeighborModule()
        node.output = _RecordingOutput()
        node.communication = _RecordingCommunication()

        cluster_head = ("192.168.1.12", 50000)
        node.neighbor.add_neighbor(
            ip_address=cluster_head[0],
            port=cluster_head[1],
            node_role=NodeRole.CLUSTER_HEAD,
            association_state=AssociationState.ASSOCIATED,
            last_heard=100.0,
            expiration_time=130.0,
            model_id=1,
            join_id=15,
        )

        node._handle_hello({"model_id": 1}, cluster_head)

        neighbor = node.neighbor.get_neighbor(*cluster_head)
        message, destination = node.communication.messages[0]

        self.assertEqual(message["packet_type"], PacketType.JOIN)
        self.assertEqual(message["join_id"], 15)
        self.assertEqual(destination, cluster_head)
        self.assertEqual(neighbor.association_state, AssociationState.ASSOCIATED)
        self.assertIn("sending JOIN", node.output.messages[0])


if __name__ == "__main__":
    unittest.main()
