import sys
import threading
import time
import unittest
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flame.communication.communication_module import CommunicationModule
from flame.communication.file_manifest import (
    FileBatchManifest,
    FileMetadata,
    build_file_metadata,
    parse_file_payload,
    parse_manifest,
    serialize_file_payload,
    serialize_manifest,
)


PNG = b"\x89PNG\r\n\x1a\n"
JPEG = b"\xff\xd8\xff"


def wait_until(predicate, timeout=3.0, interval=0.005):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class FileManifestTests(unittest.TestCase):
    def test_manifest_parsing_identifies_files_and_actual_lengths(self):
        png_bytes = PNG + b"P" * 120
        jpeg_bytes = JPEG + b"J" * 80 + b"\xff\xd9"
        manifest = FileBatchManifest(
            files=(
                build_file_metadata(
                    data_id=3,
                    logical_name="capture.png",
                    file_bytes=png_bytes,
                    encoding="png",
                ),
                build_file_metadata(
                    data_id=4,
                    logical_name="preview.jpg",
                    file_bytes=jpeg_bytes,
                    encoding="jpeg",
                ),
            ),
            batch_id="batch-test",
        )

        parsed_manifest = parse_manifest(serialize_manifest(manifest))
        self.assertEqual(parsed_manifest.file_count, 2)
        self.assertEqual(parsed_manifest.entry_for(3).encoding, "png")
        self.assertEqual(parsed_manifest.entry_for(4).logical_name, "preview.jpg")
        self.assertEqual(parsed_manifest.entry_for(3).byte_length, len(png_bytes))

        payload = serialize_file_payload(manifest, 3, png_bytes)
        parsed_file = parse_file_payload(payload, expected_data_id=3)
        self.assertEqual(parsed_file["metadata"].file_type, "image")
        self.assertEqual(parsed_file["metadata"].encoding, "png")
        self.assertEqual(parsed_file["file_bytes"], png_bytes)

    def test_file_length_is_validated_after_reassembly(self):
        file_bytes = PNG + b"P" * 40
        metadata = FileMetadata(
            data_id=8,
            logical_name="declared.png",
            file_type="image",
            encoding="png",
            byte_length=len(file_bytes),
        )
        manifest = FileBatchManifest(files=(metadata,), batch_id="length-test")
        payload = serialize_file_payload(manifest, 8, file_bytes)

        with self.assertRaisesRegex(ValueError, "length mismatch"):
            parse_file_payload(payload[:-1], expected_data_id=8)

        with self.assertRaisesRegex(ValueError, "declares"):
            serialize_file_payload(manifest, 8, file_bytes + b"x")

    def test_cluster_head_accepts_only_png_and_jpeg_file_payloads(self):
        raw_bytes = b"\x00" * 16
        raw_metadata = build_file_metadata(
            data_id=9,
            logical_name="raw-array.bin",
            file_bytes=raw_bytes,
            encoding="raw",
            width=4,
            height=4,
            dtype="uint8",
        )
        manifest = FileBatchManifest(files=(raw_metadata,), batch_id="raw-test")
        parsed_manifest = parse_manifest(serialize_manifest(manifest))
        self.assertEqual(parsed_manifest.entry_for(9).width, 4)
        self.assertEqual(parsed_manifest.entry_for(9).height, 4)
        self.assertEqual(parsed_manifest.entry_for(9).dtype, "uint8")
        payload = serialize_file_payload(manifest, 9, raw_bytes)

        with self.assertRaisesRegex(ValueError, "PNG and JPEG"):
            parse_file_payload(payload, expected_data_id=9)

    def test_encoded_signature_is_checked(self):
        bad_png = b"not-a-png"
        metadata = FileMetadata(
            data_id=10,
            logical_name="bad.png",
            file_type="image",
            encoding="png",
            byte_length=len(bad_png),
        )
        manifest = FileBatchManifest(files=(metadata,), batch_id="signature-test")
        payload = serialize_file_payload(manifest, 10, bad_png)

        with self.assertRaisesRegex(ValueError, "PNG signature"):
            parse_file_payload(payload, expected_data_id=10)


class MemoryNetwork:
    def __init__(self):
        self.sockets = {}

    def socket(self, address):
        socket = MemorySocket(self, address)
        self.sockets[address] = socket
        return socket

    def send(self, source, destination, payload):
        self.sockets[destination].enqueue(payload, source)


class MemorySocket:
    def __init__(self, network, address):
        self.network = network
        self.address = address
        self.incoming = deque()
        self.lock = threading.Lock()

    def enqueue(self, payload, source):
        with self.lock:
            self.incoming.append((payload, source))

    def sendto(self, payload, destination):
        self.network.send(self.address, destination, payload)
        return len(payload)

    def recvfrom(self):
        with self.lock:
            if not self.incoming:
                return None
            return self.incoming.popleft()

    def close(self):
        pass


class MultipleSensorFileTransferTests(unittest.TestCase):
    def setUp(self):
        self.network = MemoryNetwork()
        self.cluster_socket = self.network.socket(("cluster", 12000))
        self.sensor_one_socket = self.network.socket(("sensor-one", 12001))
        self.sensor_two_socket = self.network.socket(("sensor-two", 12002))
        options = {
            "auto_start": True,
            "poll_interval": 0.001,
            "ack_timeout": 0.08,
            "ack_delay": 0.015,
            "reassembly_timeout": 0.5,
            "completed_retention": 0.5,
            "max_retries": 2,
        }
        self.cluster = CommunicationModule(self.cluster_socket, role=1, **options)
        self.sensor_one = CommunicationModule(self.sensor_one_socket, role=0, **options)
        self.sensor_two = CommunicationModule(self.sensor_two_socket, role=0, **options)

    def tearDown(self):
        self.sensor_one.stop()
        self.sensor_two.stop()
        self.cluster.stop()

    def test_communication_module_assigns_data_ids_when_omitted(self):
        files = [
            {
                "logical_name": "automatic-one.png",
                "file_bytes": PNG + b"1" * 120,
                "encoding": "png",
            },
            {
                "logical_name": "automatic-two.jpg",
                "file_bytes": JPEG + b"2" * 100 + b"\xff\xd9",
                "encoding": "jpeg",
            },
        ]

        self.sensor_one.send_file_batch(
            files,
            destination=self.cluster_socket.address,
            sensor_type=1,
            batch_id="automatic-data-id-batch",
        )

        self.assertTrue(wait_until(lambda: len(self.cluster.sensor_buffer) == 2))
        received = self.cluster.pop_sensor_messages()
        self.assertEqual({message["data_id"] for message in received}, {1, 2})
        self.assertEqual(
            {message["logical_name"] for message in received},
            {"automatic-one.png", "automatic-two.jpg"},
        )

    def test_successful_windows_emit_aimd_growth(self):
        self.sensor_one.send_file_batch(
            [
                {
                    "logical_name": "window-growth.png",
                    "file_bytes": PNG + b"w" * 10000,
                    "encoding": "png",
                }
            ],
            destination=self.cluster_socket.address,
            sensor_type=1,
        )

        self.assertTrue(wait_until(lambda: len(self.cluster.sensor_buffer) == 1))
        window_events = [
            event
            for event in self.sensor_one.event_buffer.items
            if event["type"] == "window_advanced"
        ]
        window_sizes = [event["window_size"] for event in window_events]
        self.assertGreaterEqual(len(window_sizes), 2)
        self.assertEqual(window_sizes[:2], [3, 4])

    def test_two_sensors_send_independent_batches_with_separate_file_ids(self):
        files_one = [
            {
                "data_id": 1,
                "logical_name": "one-large.png",
                "file_bytes": PNG + b"1" * 2200,
                "encoding": "png",
            },
            {
                "data_id": 2,
                "logical_name": "one-small.jpg",
                "file_bytes": JPEG + b"2" * 600 + b"\xff\xd9",
                "encoding": "jpeg",
            },
        ]
        files_two = [
            {
                "data_id": 1,
                "logical_name": "two-large.png",
                "file_bytes": PNG + b"A" * 1700,
                "encoding": "png",
            },
            {
                "data_id": 2,
                "logical_name": "two-small.jpg",
                "file_bytes": JPEG + b"B" * 500 + b"\xff\xd9",
                "encoding": "jpeg",
            },
        ]

        senders = [
            threading.Thread(
                target=self.sensor_one.send_file_batch,
                kwargs={
                    "files": files_one,
                    "destination": self.cluster_socket.address,
                    "sensor_type": 1,
                    "batch_id": "sensor-one-batch",
                },
            ),
            threading.Thread(
                target=self.sensor_two.send_file_batch,
                kwargs={
                    "files": files_two,
                    "destination": self.cluster_socket.address,
                    "sensor_type": 2,
                    "batch_id": "sensor-two-batch",
                },
            ),
        ]
        for sender in senders:
            sender.start()
        for sender in senders:
            sender.join(timeout=1.0)

        self.assertTrue(wait_until(lambda: len(self.cluster.sensor_buffer) == 4))
        received = self.cluster.pop_sensor_messages()
        received_by_source_and_id = {
            (message["source_address"], message["data_id"]): message
            for message in received
        }

        expected = {
            (self.sensor_one_socket.address, 1): files_one[0],
            (self.sensor_one_socket.address, 2): files_one[1],
            (self.sensor_two_socket.address, 1): files_two[0],
            (self.sensor_two_socket.address, 2): files_two[1],
        }
        self.assertEqual(set(received_by_source_and_id), set(expected))
        for key, expected_file in expected.items():
            message = received_by_source_and_id[key]
            self.assertEqual(message["payload"], expected_file["file_bytes"])
            self.assertEqual(message["file_length"], len(expected_file["file_bytes"]))
            self.assertEqual(message["logical_name"], expected_file["logical_name"])
            self.assertEqual(message["file_manifest"]["file_count"], 2)

        for sensor, data_id in (
            (self.sensor_one, 1),
            (self.sensor_one, 2),
            (self.sensor_two, 1),
            (self.sensor_two, 2),
        ):
            self.assertTrue(
                wait_until(
                    lambda: any(
                        event["type"] == "transfer_complete"
                        and event["message_id"] == data_id
                        for event in sensor.event_buffer.items
                    )
                )
            )
            sent_events = [
                event
                for event in sensor.event_buffer.items
                if event["type"] == "fragment_sent"
                and event["message_id"] == data_id
            ]
            self.assertEqual(
                len({event["sequence_number"] for event in sent_events}),
                1,
            )

        time.sleep(0.05)
        self.assertEqual(len(self.cluster.sensor_buffer), 0)


if __name__ == "__main__":
    unittest.main()
