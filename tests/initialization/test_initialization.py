import unittest

from flame.initialization import NodeConfig, InitializationModule


class TestInitializationModule(unittest.TestCase):

    def test_1_sensor_node_initialization(self):
        print("\n1. Test Sensor Node initialization")

        config = NodeConfig(
            role=0,
            host="172.20.10.2",
            port=5001,
        )

        initializer = InitializationModule(config)
        node_config = initializer.initialize()

        self.assertTrue(initializer.initialized)
        self.assertEqual(node_config.role, 0)
        self.assertEqual(node_config.host, "172.20.10.2")
        self.assertEqual(node_config.port, 5001)

        print("   PASS: Sensor Node configuration initialized successfully.")

    def test_2_cluster_head_initialization(self):
        print("\n2. Test Cluster Head initialization")

        config = NodeConfig(
            role=1,
            host="172.20.10.3",
            port=5000,
        )

        initializer = InitializationModule(config)
        node_config = initializer.initialize()

        self.assertTrue(initializer.initialized)
        self.assertEqual(node_config.role, 1)
        self.assertEqual(node_config.host, "172.20.10.3")
        self.assertEqual(node_config.port, 5000)

        print("   PASS: Cluster Head configuration initialized successfully.")

    def test_3_invalid_role(self):
        print("\n3. Test invalid node role")

        config = NodeConfig(role=2)
        initializer = InitializationModule(config)

        with self.assertRaises(ValueError):
            initializer.initialize()

        print("   PASS: Invalid role was rejected.")

    def test_4_invalid_version(self):
        print("\n4. Test invalid protocol version")

        config = NodeConfig(
            role=0,
            version=16,
        )

        initializer = InitializationModule(config)

        with self.assertRaises(ValueError):
            initializer.initialize()

        print("   PASS: Invalid protocol version was rejected.")

    def test_5_invalid_port(self):
        print("\n5. Test invalid network port")

        config = NodeConfig(
            role=0,
            port=70000,
        )

        initializer = InitializationModule(config)

        with self.assertRaises(ValueError):
            initializer.initialize()

        print("   PASS: Invalid port was rejected.")

    def test_6_invalid_window_size(self):
        print("\n6. Test invalid reliability window size")

        config = NodeConfig(
            role=0,
            initial_window_size=8,
            max_window_size=4,
        )

        initializer = InitializationModule(config)

        with self.assertRaises(ValueError):
            initializer.initialize()

        print("   PASS: Invalid window size configuration was rejected.")


if __name__ == "__main__":
    unittest.main(verbosity=2)