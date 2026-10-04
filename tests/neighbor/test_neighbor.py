import unittest

from flame.neighbor import (
    AssociationState,
    NeighborModule,
    NodeRole,
)


class TestNeighborModule(unittest.TestCase):

    def test_01_add_neighbor(self):
        print("\n1. TEST ADDING A NEIGHBOR")

        module = NeighborModule()

        print("   Initial Neighbor Table:")
        print(f"   {module.get_neighbors()}")

        print("\n   Adding:")
        print("   IP Address: 172.20.10.3")
        print("   Port: 5000")
        print("   Role: Cluster Head")
        print("   State: DISCOVERED")
        print("   Last Heard: 100.0")
        print("   Expiration: 130.0")
        print("   Model ID: 2")

        module.add_neighbor(
            ip_address="172.20.10.3",
            port=5000,
            node_role=NodeRole.CLUSTER_HEAD,
            association_state=AssociationState.DISCOVERED,
            last_heard=100.0,
            expiration_time=130.0,
            model_id=2,
        )

        print("\n   Neighbor Table after adding:")
        print(f"   {module.get_neighbors()}")

        self.assertTrue(
            module.has_neighbor("172.20.10.3", 5000)
        )

        print("\n   PASS: Neighbor was added.")

    def test_02_get_neighbor(self):
        print("\n2. TEST RETRIEVING A NEIGHBOR")

        module = NeighborModule()

        print("   Adding neighbor first...")

        module.add_neighbor(
            ip_address="172.20.10.3",
            port=5000,
            node_role=NodeRole.CLUSTER_HEAD,
            association_state=AssociationState.DISCOVERED,
            last_heard=100.0,
            expiration_time=130.0,
            model_id=2,
        )

        print("   Searching for:")
        print("   IP Address: 172.20.10.3")
        print("   Port: 5000")

        neighbor = module.get_neighbor(
            "172.20.10.3",
            5000,
        )

        self.assertIsNotNone(neighbor)

        print("\n   Neighbor found:")
        print(f"   {neighbor}")

        self.assertEqual(neighbor.ip_address, "172.20.10.3")
        self.assertEqual(neighbor.port, 5000)
        self.assertEqual(
            neighbor.node_role,
            NodeRole.CLUSTER_HEAD,
        )
        self.assertEqual(
            neighbor.association_state,
            AssociationState.DISCOVERED,
        )

        print("\n   PASS: Neighbor information was retrieved.")

    def test_03_neighbor_states(self):
        print("\n3. TEST NEIGHBOR ASSOCIATION STATES")

        print("   Checking the four allowed states:")

        print(f"   1. {AssociationState.DISCOVERED.value}")
        print(f"   2. {AssociationState.JOIN_PENDING.value}")
        print(f"   3. {AssociationState.ASSOCIATED.value}")
        print(f"   4. {AssociationState.INACTIVE.value}")

        self.assertEqual(
            AssociationState.DISCOVERED.value,
            "DISCOVERED",
        )

        self.assertEqual(
            AssociationState.JOIN_PENDING.value,
            "JOIN_PENDING",
        )

        self.assertEqual(
            AssociationState.ASSOCIATED.value,
            "ASSOCIATED",
        )

        self.assertEqual(
            AssociationState.INACTIVE.value,
            "INACTIVE",
        )

        print("\n   PASS: All four association states exist.")

    def test_04_remove_neighbor(self):
        print("\n4. TEST REMOVING A NEIGHBOR")

        module = NeighborModule()

        print("   Adding neighbor:")

        module.add_neighbor(
            ip_address="172.20.10.3",
            port=5000,
            node_role=NodeRole.CLUSTER_HEAD,
            association_state=AssociationState.DISCOVERED,
            last_heard=100.0,
            expiration_time=130.0,
        )

        print(f"   Before removal: {module.get_neighbors()}")

        print("\n   Removing:")
        print("   IP Address: 172.20.10.3")
        print("   Port: 5000")

        module.remove_neighbor(
            "172.20.10.3",
            5000,
        )

        print("\n   After removal:")
        print(f"   {module.get_neighbors()}")

        self.assertFalse(
            module.has_neighbor("172.20.10.3", 5000)
        )

        print("\n   PASS: Neighbor was removed.")

    def test_05_get_all_neighbors(self):
        print("\n5. TEST RETRIEVING ALL NEIGHBORS")

        module = NeighborModule()

        print("   Adding Neighbor 1:")
        print("   172.20.10.3:5000")
        print("   Role: Cluster Head")
        print("   State: DISCOVERED")

        module.add_neighbor(
            ip_address="172.20.10.3",
            port=5000,
            node_role=NodeRole.CLUSTER_HEAD,
            association_state=AssociationState.DISCOVERED,
            last_heard=100.0,
            expiration_time=130.0,
        )

        print("\n   Adding Neighbor 2:")
        print("   172.20.10.4:5000")
        print("   Role: Sensor Node")
        print("   State: JOIN_PENDING")

        module.add_neighbor(
            ip_address="172.20.10.4",
            port=5000,
            node_role=NodeRole.SENSOR_NODE,
            association_state=AssociationState.JOIN_PENDING,
            last_heard=105.0,
            expiration_time=135.0,
        )

        print("\n   Retrieving all neighbors...")

        neighbors = module.get_neighbors()

        print(f"\n   Number of neighbors: {len(neighbors)}")

        for i, neighbor in enumerate(neighbors, start=1):
            print(f"\n   Neighbor {i}:")
            print(f"   IP Address: {neighbor.ip_address}")
            print(f"   Port: {neighbor.port}")
            print(f"   Role: {neighbor.node_role.value}")
            print(f"   State: {neighbor.association_state.value}")
            print(f"   Last Heard: {neighbor.last_heard}")
            print(f"   Expiration: {neighbor.expiration_time}")
            print(f"   Model ID: {neighbor.model_id}")

        self.assertEqual(len(neighbors), 2)

        print("\n   PASS: All neighbors were retrieved.")

    def test_06_sensor_discovers_cluster_head(self):
        print("\n6. TEST SENSOR NODE DISCOVERS CLUSTER HEAD")

        module = NeighborModule()

        current_time = 1000.0
        neighbor_timeout = 30.0

        print("   Local Node: Sensor Node")
        print("   Incoming Message: HELLO")
        print("   HELLO Sender: Cluster Head")
        print("   Sender IP: 172.20.10.3")
        print("   Sender Port: 5000")
        print("   Model ID: 2")

        module.process_hello(
            ip_address="172.20.10.3",
            port=5000,
            node_role=NodeRole.CLUSTER_HEAD,
            model_id=2,
            current_time=current_time,
            neighbor_timeout=neighbor_timeout,
        )

        neighbor = module.get_neighbor(
            "172.20.10.3",
            5000,
        )

        self.assertIsNotNone(neighbor)

        print("\n   Neighbor Table Entry:")
        print(f"   IP Address: {neighbor.ip_address}")
        print(f"   Port: {neighbor.port}")
        print(f"   Role: {neighbor.node_role.value}")
        print(f"   State: {neighbor.association_state.value}")
        print(f"   Last Heard: {neighbor.last_heard}")
        print(f"   Expiration: {neighbor.expiration_time}")
        print(f"   Model ID: {neighbor.model_id}")

        self.assertEqual(
            neighbor.node_role,
            NodeRole.CLUSTER_HEAD,
        )
        self.assertEqual(
            neighbor.association_state,
            AssociationState.DISCOVERED,
        )
        self.assertEqual(
            neighbor.last_heard,
            1000.0,
        )
        self.assertEqual(
            neighbor.expiration_time,
            1030.0,
        )
        self.assertEqual(
            neighbor.model_id,
            2,
        )

        print("\n   PASS: Sensor Node discovered the Cluster Head.")

    def test_07_cluster_head_receives_join(self):
        print("\n7. TEST CLUSTER HEAD RECEIVES JOIN")

        module = NeighborModule()

        current_time = 2000.0
        neighbor_timeout = 30.0
        join_id = 15

        print("   Local Node: Cluster Head")
        print("   Incoming Message: JOIN")
        print("   JOIN Sender: Sensor Node")
        print("   Sender IP: 172.20.10.4")
        print("   Sender Port: 5000")
        print("   Join ID: 15")
        print("   Model ID: 2")

        module.process_join(
            ip_address="172.20.10.4",
            port=5000,
            join_id=join_id,
            model_id=2,
            current_time=current_time,
            neighbor_timeout=neighbor_timeout,
        )

        neighbor = module.get_neighbor(
            "172.20.10.4",
            5000,
        )

        self.assertIsNotNone(neighbor)

        print("\n   Neighbor Table Entry:")
        print(f"   IP Address: {neighbor.ip_address}")
        print(f"   Port: {neighbor.port}")
        print(f"   Role: {neighbor.node_role.value}")
        print(f"   State: {neighbor.association_state.value}")
        print(f"   Last Heard: {neighbor.last_heard}")
        print(f"   Expiration: {neighbor.expiration_time}")
        print(f"   Model ID: {neighbor.model_id}")
        print(f"   Join ID: {neighbor.join_id}")

        self.assertEqual(
            neighbor.node_role,
            NodeRole.SENSOR_NODE,
        )

        self.assertEqual(
            neighbor.association_state,
            AssociationState.JOIN_PENDING,
        )

        self.assertEqual(
            neighbor.last_heard,
            2000.0,
        )

        self.assertEqual(
            neighbor.expiration_time,
            2030.0,
        )

        self.assertEqual(
            neighbor.model_id,
            2,
        )

        self.assertEqual(
            neighbor.join_id,
            15,
        )

        print(
            "\n   PASS: Cluster Head created a "
            "JOIN_PENDING Sensor Node entry."
        )

    def test_08_sensor_accepts_join(self):
        print("\n8. TEST SENSOR NODE ACCEPTS JOIN")

        module = NeighborModule()

        current_time = 2000.0
        neighbor_timeout = 30.0
        join_id = 15

        print("   Local Node: Sensor Node")
        print("   Existing State: JOIN_PENDING")
        print("   Cluster Head: 172.20.10.3:5000")
        print("   Pending Join ID: 15")

        module.add_neighbor(
            ip_address="172.20.10.3",
            port=5000,
            node_role=NodeRole.CLUSTER_HEAD,
            association_state=AssociationState.JOIN_PENDING,
            last_heard=current_time,
            expiration_time=current_time + neighbor_timeout,
            model_id=2,
            join_id=join_id,
        )

        print("\n   Incoming Message: JOIN_ACCEPT")
        print("   Join ID: 15")

        result = module.process_join_accept(
            ip_address="172.20.10.3",
            port=5000,
            join_id=15,
            current_time=2030.0,
            neighbor_timeout=neighbor_timeout,
        )

        neighbor = module.get_neighbor(
            "172.20.10.3",
            5000,
        )

        self.assertIsNotNone(neighbor)
        self.assertTrue(result)

        print("\n   Neighbor Table Entry:")
        print(f"   IP Address: {neighbor.ip_address}")
        print(f"   Port: {neighbor.port}")
        print(f"   Role: {neighbor.node_role.value}")
        print(f"   State: {neighbor.association_state.value}")
        print(f"   Last Heard: {neighbor.last_heard}")
        print(f"   Expiration: {neighbor.expiration_time}")
        print(f"   Model ID: {neighbor.model_id}")
        print(f"   Join ID: {neighbor.join_id}")

        self.assertEqual(
            neighbor.association_state,
            AssociationState.ASSOCIATED,
        )

        self.assertEqual(
            neighbor.last_heard,
            2030.0,
        )

        self.assertEqual(
            neighbor.expiration_time,
            2060.0,
        )

        print(
            "\n   PASS: Sensor Node accepted "
            "the JOIN_ACCEPT."
        )

    def test_09_rejects_wrong_join_id(self):
        print("\n9. TEST SENSOR NODE REJECTS WRONG JOIN ID")

        module = NeighborModule()

        current_time = 3000.0
        neighbor_timeout = 30.0
        pending_join_id = 15

        print("   Local Node: Sensor Node")
        print("   Existing State: JOIN_PENDING")
        print("   Cluster Head: 172.20.10.3:5000")
        print("   Pending Join ID: 15")

        module.add_neighbor(
            ip_address="172.20.10.3",
            port=5000,
            node_role=NodeRole.CLUSTER_HEAD,
            association_state=AssociationState.JOIN_PENDING,
            last_heard=current_time,
            expiration_time=current_time + neighbor_timeout,
            model_id=2,
            join_id=pending_join_id,
        )

        print("\n   Incoming Message: JOIN_ACCEPT")
        print("   Received Join ID: 99")

        result = module.process_join_accept(
            ip_address="172.20.10.3",
            port=5000,
            join_id=99,
            current_time=3030.0,
            neighbor_timeout=neighbor_timeout,
        )

        neighbor = module.get_neighbor(
            "172.20.10.3",
            5000,
        )

        self.assertIsNotNone(neighbor)
        self.assertFalse(result)

        print("\n   Neighbor Table Entry:")
        print(f"   IP Address: {neighbor.ip_address}")
        print(f"   Port: {neighbor.port}")
        print(f"   Role: {neighbor.node_role.value}")
        print(f"   State: {neighbor.association_state.value}")
        print(f"   Last Heard: {neighbor.last_heard}")
        print(f"   Expiration: {neighbor.expiration_time}")
        print(f"   Join ID: {neighbor.join_id}")

        self.assertEqual(
            neighbor.association_state,
            AssociationState.JOIN_PENDING,
        )

        self.assertEqual(
            neighbor.join_id,
            pending_join_id,
        )

        print(
            "\n   PASS: Wrong Join ID was rejected "
            "and state remained JOIN_PENDING."
        )

    def test_10_process_leave(self):
        print("\n10. TEST PROCESSING LEAVE")

        module = NeighborModule()

        current_time = 4000.0

        print("   Local Node: Cluster Head")
        print("   Existing Neighbor: Sensor Node")
        print("   Sensor IP: 172.20.10.4")
        print("   Sensor Port: 5000")
        print("   Existing State: ASSOCIATED")

        module.add_neighbor(
            ip_address="172.20.10.4",
            port=5000,
            node_role=NodeRole.SENSOR_NODE,
            association_state=AssociationState.ASSOCIATED,
            last_heard=3970.0,
            expiration_time=4000.0,
            model_id=2,
            join_id=15,
        )

        print("\n   Incoming Message: LEAVE")
        print("   Sender: 172.20.10.4:5000")

        result = module.process_leave(
            ip_address="172.20.10.4",
            port=5000,
            current_time=current_time,
        )

        neighbor = module.get_neighbor(
            "172.20.10.4",
            5000,
        )

        self.assertIsNotNone(neighbor)
        self.assertTrue(result)

        print("\n   Neighbor Table Entry:")
        print(f"   IP Address: {neighbor.ip_address}")
        print(f"   Port: {neighbor.port}")
        print(f"   Role: {neighbor.node_role.value}")
        print(f"   State: {neighbor.association_state.value}")
        print(f"   Last Heard: {neighbor.last_heard}")
        print(f"   Expiration: {neighbor.expiration_time}")
        print(f"   Model ID: {neighbor.model_id}")
        print(f"   Join ID: {neighbor.join_id}")

        self.assertEqual(
            neighbor.association_state,
            AssociationState.INACTIVE,
        )

        self.assertEqual(
            neighbor.last_heard,
            4000.0,
        )

        self.assertEqual(
            neighbor.expiration_time,
            4000.0,
        )

        print(
            "\n   PASS: Neighbor was marked INACTIVE "
            "after LEAVE."
        )

    def test_11_unknown_leave(self):
        print("\n11. TEST UNKNOWN NEIGHBOR LEAVE")

        module = NeighborModule()

        print("   Local Node: Cluster Head")
        print("   Incoming Message: LEAVE")
        print("   Sender: 172.20.10.99:5000")

        result = module.process_leave(
            ip_address="172.20.10.99",
            port=5000,
            current_time=5000.0,
        )

        print("\n   Processing Result:")
        print(f"   {result}")

        self.assertFalse(result)

        self.assertFalse(
            module.has_neighbor(
                "172.20.10.99",
                5000,
            )
        )

        print(
            "\n   PASS: Unknown LEAVE was ignored "
            "without creating a neighbor."
        )

    def test_12_repeated_join_same_id(self):
        print("\n12. TEST REPEATED JOIN WITH SAME JOIN ID")

        module = NeighborModule()

        print("   Local Node: Cluster Head")
        print("   First JOIN:")
        print("   Sensor: 172.20.10.4:5000")
        print("   Join ID: 15")

        module.process_join(
            ip_address="172.20.10.4",
            port=5000,
            join_id=15,
            model_id=2,
            current_time=6000.0,
            neighbor_timeout=30.0,
        )

        print("\n   Neighbor Table after first JOIN:")
        print(module.get_neighbors())

        print("\n   Second JOIN:")
        print("   Sensor: 172.20.10.4:5000")
        print("   Join ID: 15")
        print("   Expected: Existing entry refreshed")

        module.process_join(
            ip_address="172.20.10.4",
            port=5000,
            join_id=15,
            model_id=2,
            current_time=6010.0,
            neighbor_timeout=30.0,
        )

        neighbor = module.get_neighbor(
            "172.20.10.4",
            5000,
        )

        print("\n   Neighbor Table after repeated JOIN:")
        print(f"   Number of neighbors: {len(module.get_neighbors())}")

        print(f"   IP Address: {neighbor.ip_address}")
        print(f"   Port: {neighbor.port}")
        print(f"   Role: {neighbor.node_role.value}")
        print(f"   State: {neighbor.association_state.value}")
        print(f"   Last Heard: {neighbor.last_heard}")
        print(f"   Expiration: {neighbor.expiration_time}")
        print(f"   Join ID: {neighbor.join_id}")

        self.assertEqual(
            len(module.get_neighbors()),
            1,
        )

        self.assertEqual(
            neighbor.association_state,
            AssociationState.JOIN_PENDING,
        )

        self.assertEqual(
            neighbor.join_id,
            15,
        )

        self.assertEqual(
            neighbor.last_heard,
            6010.0,
        )

        self.assertEqual(
            neighbor.expiration_time,
            6040.0,
        )

        print(
            "\n   PASS: Repeated JOIN refreshed the existing "
            "neighbor without creating a duplicate."
        )

    def test_13_neighbor_timeout(self):
        print("\n13. TEST NEIGHBOR TIMEOUT")

        module = NeighborModule()

        print("   Adding Sensor Node:")
        print("   IP Address: 172.20.10.4")
        print("   Port: 5000")
        print("   State: ASSOCIATED")
        print("   Last Heard: 7000.0")
        print("   Expiration: 7030.0")

        module.add_neighbor(
            ip_address="172.20.10.4",
            port=5000,
            node_role=NodeRole.SENSOR_NODE,
            association_state=AssociationState.ASSOCIATED,
            last_heard=7000.0,
            expiration_time=7030.0,
            model_id=2,
            join_id=15,
        )

        print("\n   Checking at time 7020.0...")
        
        expired = module.check_expired_neighbors(
            current_time=7020.0,
        )

        neighbor = module.get_neighbor(
            "172.20.10.4",
            5000,
        )

        print(f"   Expired neighbors: {expired}")
        print(f"   Current State: {neighbor.association_state.value}")

        self.assertEqual(
            len(expired),
            0,
        )

        self.assertEqual(
            neighbor.association_state,
            AssociationState.ASSOCIATED,
        )

        print("   PASS: Neighbor remained active before expiration.")

        print("\n   Checking at time 7030.0...")

        expired = module.check_expired_neighbors(
            current_time=7030.0,
        )

        neighbor = module.get_neighbor(
            "172.20.10.4",
            5000,
        )

        print(f"   Expired neighbors: {expired}")
        print(f"   Current State: {neighbor.association_state.value}")

        self.assertEqual(
            len(expired),
            1,
        )

        self.assertEqual(
            neighbor.association_state,
            AssociationState.INACTIVE,
        )

        print(
            "   PASS: Neighbor was marked INACTIVE "
            "after expiration."
        )

    def test_14_membership_refresh(self):
        print("\n14. TEST MEMBERSHIP REFRESH")

        module = NeighborModule()

        print("   Adding Sensor Node:")
        print("   IP Address: 172.20.10.4")
        print("   Port: 5000")
        print("   State: ASSOCIATED")
        print("   Last Heard: 8000.0")
        print("   Expiration: 8030.0")

        module.add_neighbor(
            ip_address="172.20.10.4",
            port=5000,
            node_role=NodeRole.SENSOR_NODE,
            association_state=AssociationState.ASSOCIATED,
            last_heard=8000.0,
            expiration_time=8030.0,
            model_id=2,
            join_id=15,
        )

        print("\n   Receiving valid message at time 8020.0...")
        print("   Expected: Membership lifetime refreshed")

        result = module.refresh_neighbor(
            ip_address="172.20.10.4",
            port=5000,
            current_time=8020.0,
            neighbor_timeout=30.0,
        )

        neighbor = module.get_neighbor(
            "172.20.10.4",
            5000,
        )

        print("\n   Refresh Result:")
        print(f"   {result}")

        print("\n   Neighbor Table Entry:")
        print(f"   IP Address: {neighbor.ip_address}")
        print(f"   Port: {neighbor.port}")
        print(f"   Role: {neighbor.node_role.value}")
        print(f"   State: {neighbor.association_state.value}")
        print(f"   Last Heard: {neighbor.last_heard}")
        print(f"   Expiration: {neighbor.expiration_time}")
        print(f"   Join ID: {neighbor.join_id}")

        self.assertTrue(result)

        self.assertEqual(
            neighbor.association_state,
            AssociationState.ASSOCIATED,
        )

        self.assertEqual(
            neighbor.last_heard,
            8020.0,
        )

        self.assertEqual(
            neighbor.expiration_time,
            8050.0,
        )

        print(
            "\n   PASS: Membership lifetime was refreshed."
        )

    def test_15_refresh_unknown_neighbor(self):
        print("\n15. TEST REFRESH UNKNOWN NEIGHBOR")

        module = NeighborModule()

        print("   Local Node: Cluster Head")
        print("   Incoming Activity: Unknown Sensor Node")
        print("   Sender: 172.20.10.99:5000")

        result = module.refresh_neighbor(
            ip_address="172.20.10.99",
            port=5000,
            current_time=9000.0,
            neighbor_timeout=30.0,
        )

        print("\n   Refresh Result:")
        print(f"   {result}")

        print("\n   Neighbor Table:")
        print(module.get_neighbors())

        self.assertFalse(result)

        self.assertFalse(
            module.has_neighbor(
                "172.20.10.99",
                5000,
            )
        )

        self.assertEqual(
            len(module.get_neighbors()),
            0,
        )

        print(
            "\n   PASS: Unknown neighbor was not created "
            "by membership refresh."
        )

    def test_16_refresh_inactive_neighbor(self):
        print("\n16. TEST REFRESH INACTIVE NEIGHBOR")

        module = NeighborModule()

        print("   Adding inactive Sensor Node:")
        print("   IP Address: 172.20.10.4")
        print("   Port: 5000")
        print("   State: INACTIVE")

        module.add_neighbor(
            ip_address="172.20.10.4",
            port=5000,
            node_role=NodeRole.SENSOR_NODE,
            association_state=AssociationState.INACTIVE,
            last_heard=9000.0,
            expiration_time=9000.0,
            model_id=2,
            join_id=15,
        )

        print("\n   Attempting membership refresh at time 9010.0...")

        result = module.refresh_neighbor(
            ip_address="172.20.10.4",
            port=5000,
            current_time=9010.0,
            neighbor_timeout=30.0,
        )

        neighbor = module.get_neighbor(
            "172.20.10.4",
            5000,
        )

        print("\n   Refresh Result:")
        print(f"   {result}")

        print("\n   Neighbor Table Entry:")
        print(f"   IP Address: {neighbor.ip_address}")
        print(f"   Port: {neighbor.port}")
        print(f"   State: {neighbor.association_state.value}")
        print(f"   Last Heard: {neighbor.last_heard}")
        print(f"   Expiration: {neighbor.expiration_time}")

        self.assertFalse(result)

        self.assertEqual(
            neighbor.association_state,
            AssociationState.INACTIVE,
        )

        self.assertEqual(
            neighbor.last_heard,
            9000.0,
        )

        self.assertEqual(
            neighbor.expiration_time,
            9000.0,
        )

        print(
            "\n   PASS: INACTIVE neighbor was not revived "
            "by generic membership refresh."
        )

    def test_17_sensor_data_establishes_association(self):
        neighbor_module = NeighborModule()

        neighbor_module.add_neighbor(
            ip_address="172.20.10.4",
            port=5000,
            node_role=NodeRole.SENSOR_NODE,
            association_state=AssociationState.JOIN_PENDING,
            last_heard=10000,
            expiration_time=10030,
            model_id=2,
            join_id=15,
        )

        result = neighbor_module.process_sensor_data(
            ip_address="172.20.10.4",
            port=5000,
            current_time=10010,
            neighbor_timeout=30,
        )

        neighbor = neighbor_module.get_neighbor(
            "172.20.10.4",
            5000,
        )

        self.assertTrue(result)
        self.assertEqual(
            neighbor.association_state,
            AssociationState.ASSOCIATED,
        )
        self.assertEqual(neighbor.last_heard, 10010)
        self.assertEqual(neighbor.expiration_time, 10040)

        print("Sensor data established association:", neighbor)

    def test_18_rejected_join_accept_returns_to_discovered(self):
        neighbor_module = NeighborModule()

        neighbor_module.add_neighbor(
            ip_address="172.20.10.3",
            port=5000,
            node_role=NodeRole.CLUSTER_HEAD,
            association_state=AssociationState.JOIN_PENDING,
            last_heard=11000,
            expiration_time=11030,
            model_id=2,
            join_id=15,
        )

        result = neighbor_module.process_join_accept(
            ip_address="172.20.10.3",
            port=5000,
            join_id=15,
            rejected=1,
            current_time=11010,
            neighbor_timeout=30,
        )
        
        neighbor = neighbor_module.get_neighbor(
            "172.20.10.3",
            5000,
        )

        self.assertFalse(result)
        self.assertEqual(
            neighbor.association_state,
            AssociationState.DISCOVERED,
        )
        self.assertEqual(neighbor.last_heard, 11010)
        self.assertEqual(neighbor.expiration_time, 11040)
        self.assertIsNone(neighbor.join_id)

        print("Rejected JOIN_ACCEPT returned neighbor to DISCOVERED.")

    def test_19_sensor_node_starts_join(self):
        neighbor_module = NeighborModule()

        neighbor_module.start_join(
            ip_address="172.20.10.3",
            port=5000,
            join_id=15,
            model_id=2,
            current_time=11000.0,
            neighbor_timeout=30.0,
        )

        neighbor = neighbor_module.get_neighbor(
            "172.20.10.3",
            5000,
        )

        print("Sensor Node started JOIN:")
        print("Cluster Head:", "172.20.10.3:5000")
        print("Join ID:", 15)
        print("Model ID:", 2)
        print("State:", neighbor.association_state)

        self.assertIsNotNone(neighbor)
        self.assertEqual(
            neighbor.node_role,
            NodeRole.CLUSTER_HEAD,
        )
        self.assertEqual(
            neighbor.association_state,
            AssociationState.JOIN_PENDING,
        )
        self.assertEqual(
            neighbor.join_id,
            15,
        )
        self.assertEqual(
            neighbor.model_id,
            2,
        )

    def test_20_expired_neighbor_becomes_inactive(self):
        neighbor_module = NeighborModule()

        neighbor_module.process_hello(
            ip_address="10.0.0.5",
            port=5000,
            node_role=NodeRole.CLUSTER_HEAD,
            current_time=100.0,
            neighbor_timeout=30.0,
        )

        neighbor = neighbor_module.get_neighbor(
            "10.0.0.5",
            5000,
        )

        self.assertEqual(
            neighbor.association_state,
            AssociationState.DISCOVERED,
        )

        expired = neighbor_module.check_expired_neighbors(
            current_time=130.0,
        )

        self.assertEqual(
            len(expired),
            1,
        )

        self.assertEqual(
            expired[0].ip_address,
            "10.0.0.5",
        )

        self.assertEqual(
            expired[0].association_state,
            AssociationState.INACTIVE,
        )

    def test_21_associated_join_refresh_preserves_membership(self):
        neighbor_module = NeighborModule()

        neighbor_module.add_neighbor(
            ip_address="172.20.10.4",
            port=5000,
            node_role=NodeRole.SENSOR_NODE,
            association_state=AssociationState.ASSOCIATED,
            last_heard=12000.0,
            expiration_time=12030.0,
            model_id=2,
            join_id=15,
        )

        neighbor_module.process_join(
            ip_address="172.20.10.4",
            port=5000,
            join_id=16,
            model_id=2,
            current_time=12010.0,
            neighbor_timeout=30.0,
        )

        neighbor = neighbor_module.get_neighbor("172.20.10.4", 5000)

        self.assertEqual(
            neighbor.association_state,
            AssociationState.ASSOCIATED,
        )
        self.assertEqual(neighbor.last_heard, 12010.0)
        self.assertEqual(neighbor.expiration_time, 12040.0)
        self.assertEqual(neighbor.join_id, 16)

    def test_22_associated_sensor_refresh_accept_remains_associated(self):
        neighbor_module = NeighborModule()

        neighbor_module.add_neighbor(
            ip_address="172.20.10.3",
            port=5000,
            node_role=NodeRole.CLUSTER_HEAD,
            association_state=AssociationState.ASSOCIATED,
            last_heard=13000.0,
            expiration_time=13030.0,
            model_id=2,
            join_id=15,
        )

        neighbor_module.start_join(
            ip_address="172.20.10.3",
            port=5000,
            join_id=16,
            model_id=2,
            current_time=13010.0,
            neighbor_timeout=30.0,
        )

        self.assertEqual(
            neighbor_module.get_neighbor("172.20.10.3", 5000).association_state,
            AssociationState.ASSOCIATED,
        )

        accepted = neighbor_module.process_join_accept(
            ip_address="172.20.10.3",
            port=5000,
            join_id=16,
            current_time=13010.0,
            neighbor_timeout=30.0,
        )

        neighbor = neighbor_module.get_neighbor("172.20.10.3", 5000)

        self.assertTrue(accepted)
        self.assertEqual(
            neighbor.association_state,
            AssociationState.ASSOCIATED,
        )
        self.assertEqual(neighbor.expiration_time, 13040.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
