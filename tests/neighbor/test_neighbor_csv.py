import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flame.neighbor.neighbor_module import (
    AssociationState,
    NeighborModule,
    NodeRole,
)


class NeighborCsvExportTests(unittest.TestCase):
    def test_export_contains_registered_sensors_and_final_states(self):
        now = 1_700_000_000.0
        module = NeighborModule()
        module.add_neighbor(
            ip_address="192.168.1.3",
            port=5000,
            node_role=NodeRole.SENSOR_NODE,
            association_state=AssociationState.ASSOCIATED,
            last_heard=now - 9,
            expiration_time=now + 21,
            model_id=None,
        )
        module.add_neighbor(
            ip_address="192.168.1.4",
            port=5001,
            node_role=NodeRole.SENSOR_NODE,
            association_state=AssociationState.ASSOCIATED,
            last_heard=now - 40,
            expiration_time=now - 1,
            model_id=7,
        )
        module.add_neighbor(
            ip_address="192.168.1.10",
            port=6000,
            node_role=NodeRole.CLUSTER_HEAD,
            association_state=AssociationState.DISCOVERED,
            last_heard=now - 2,
            expiration_time=now + 28,
            model_id=3,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = module.export_csv(Path(temp_dir) / "neighbors.csv", current_time=now)
            with output_path.open(newline="", encoding="utf-8") as csv_file:
                rows = list(csv.DictReader(csv_file))

        self.assertEqual(
            list(rows[0]),
            [
                "IP Address",
                "Port",
                "Node Type",
                "Association State",
                "Last Heard",
                "Expiration Time",
                "Model ID",
            ],
        )
        self.assertEqual(len(rows), 3)

        rows_by_ip = {row["IP Address"]: row for row in rows}
        self.assertEqual(rows_by_ip["192.168.1.3"]["Node Type"], "Sensor Node")
        self.assertEqual(rows_by_ip["192.168.1.3"]["Association State"], "ASSOCIATED")
        self.assertEqual(rows_by_ip["192.168.1.3"]["Model ID"], "N/A")
        self.assertEqual(rows_by_ip["192.168.1.4"]["Association State"], "INACTIVE")
        self.assertEqual(rows_by_ip["192.168.1.4"]["Model ID"], "7")
        self.assertEqual(rows_by_ip["192.168.1.10"]["Node Type"], "Cluster Head")


if __name__ == "__main__":
    unittest.main()

