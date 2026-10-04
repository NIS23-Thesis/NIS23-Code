import csv
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional


class NodeRole(Enum):
    SENSOR_NODE = "SENSOR_NODE"
    CLUSTER_HEAD = "CLUSTER_HEAD"


class AssociationState(Enum):
    DISCOVERED = "DISCOVERED"
    JOIN_PENDING = "JOIN_PENDING"
    ASSOCIATED = "ASSOCIATED"
    INACTIVE = "INACTIVE"


@dataclass
class Neighbor:
    ip_address: str
    port: int
    node_role: NodeRole
    association_state: AssociationState
    last_heard: float
    expiration_time: float
    model_id: Optional[int] = None
    join_id: Optional[int] = None


class NeighborModule:
    CSV_COLUMNS = (
        "IP Address",
        "Port",
        "Node Type",
        "Association State",
        "Last Heard",
        "Expiration Time",
        "Model ID",
    )

    def __init__(self):
        self.neighbor_table: Dict[tuple[str, int], Neighbor] = {}

    def add_neighbor(
        self,
        ip_address: str,
        port: int,
        node_role: NodeRole,
        association_state: AssociationState,
        last_heard: float,
        expiration_time: float,
        model_id: Optional[int] = None,
        join_id: Optional[int] = None,
    ) -> None:
        key = (ip_address, port)

        self.neighbor_table[key] = Neighbor(
            ip_address=ip_address,
            port=port,
            node_role=node_role,
            association_state=association_state,
            last_heard=last_heard,
            expiration_time=expiration_time,
            model_id=model_id,
            join_id=join_id,
        )

    def get_neighbor(
        self,
        ip_address: str,
        port: int,
    ) -> Optional[Neighbor]:
        return self.neighbor_table.get((ip_address, port))

    def has_neighbor(
        self,
        ip_address: str,
        port: int,
    ) -> bool:
        return (ip_address, port) in self.neighbor_table

    def remove_neighbor(
        self,
        ip_address: str,
        port: int,
    ) -> None:
        self.neighbor_table.pop((ip_address, port), None)

    def get_neighbors(self) -> List[Neighbor]:
        return list(self.neighbor_table.values())

    @staticmethod
    def _format_time(timestamp: float) -> str:
        return time.strftime("%H:%M:%S", time.localtime(timestamp))

    @staticmethod
    def _node_type_label(node_role: NodeRole) -> str:
        if node_role == NodeRole.SENSOR_NODE:
            return "Sensor Node"
        if node_role == NodeRole.CLUSTER_HEAD:
            return "Cluster Head"
        return node_role.value

    def export_csv(
        self,
        path: str | Path,
        *,
        current_time: Optional[float] = None,
    ) -> Path:
        """Export the current neighbor table, including inactive entries.

        Expiration is checked immediately before writing so the resulting
        file reflects the final association state at shutdown.  Entries stay
        in ``neighbor_table``; this is an export of the existing state, not a
        second neighbor database.
        """
        if current_time is None:
            current_time = time.time()
        self.check_expired_neighbors(current_time=current_time)

        output_path = Path(path)
        with output_path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=self.CSV_COLUMNS)
            writer.writeheader()
            for neighbor in self.get_neighbors():
                writer.writerow(
                    {
                        "IP Address": neighbor.ip_address,
                        "Port": neighbor.port,
                        "Node Type": self._node_type_label(neighbor.node_role),
                        "Association State": neighbor.association_state.value,
                        "Last Heard": self._format_time(neighbor.last_heard),
                        "Expiration Time": self._format_time(neighbor.expiration_time),
                        "Model ID": (
                            "N/A" if neighbor.model_id is None else neighbor.model_id
                        ),
                    }
                )
        return output_path

    def process_hello(
        self,
        ip_address: str,
        port: int,
        node_role: NodeRole,
        model_id: Optional[int] = None,
        current_time: Optional[float] = None,
        neighbor_timeout: float = 30.0,
    ) -> None:
        """
        Process a HELLO message and create or update a neighbor entry.
        """

        if current_time is None:
            current_time = time.time()

        expiration_time = current_time + neighbor_timeout

        existing_neighbor = self.get_neighbor(ip_address, port)

        if existing_neighbor is None:
            self.add_neighbor(
                ip_address=ip_address,
                port=port,
                node_role=node_role,
                association_state=AssociationState.DISCOVERED,
                last_heard=current_time,
                expiration_time=expiration_time,
                model_id=model_id,
            )

        else:
            existing_neighbor.node_role = node_role
            existing_neighbor.last_heard = current_time
            existing_neighbor.expiration_time = expiration_time
            existing_neighbor.model_id = model_id

            if existing_neighbor.association_state == AssociationState.INACTIVE:
                existing_neighbor.association_state = AssociationState.DISCOVERED

    def process_join(
        self,
        ip_address: str,
        port: int,
        join_id: int,
        model_id: Optional[int] = None,
        current_time: Optional[float] = None,
        neighbor_timeout: float = 30.0,
    ) -> None:
        """
        Process a JOIN message from a Sensor Node.

        A valid JOIN creates or updates a provisional neighbor
        entry in JOIN_PENDING state.  A JOIN from an already-associated
        Sensor Node is a membership refresh and keeps the existing
        ASSOCIATED state while extending its expiration time.
        """

        if current_time is None:
            current_time = time.time()

        expiration_time = current_time + neighbor_timeout

        existing_neighbor = self.get_neighbor(
            ip_address,
            port,
        )

        if existing_neighbor is None:
            self.add_neighbor(
                ip_address=ip_address,
                port=port,
                node_role=NodeRole.SENSOR_NODE,
                association_state=AssociationState.JOIN_PENDING,
                last_heard=current_time,
                expiration_time=expiration_time,
                model_id=model_id,
            )

            neighbor = self.get_neighbor(
                ip_address,
                port,
            )

            neighbor.join_id = join_id

        else:
            existing_neighbor.node_role = NodeRole.SENSOR_NODE
            if existing_neighbor.association_state != AssociationState.ASSOCIATED:
                existing_neighbor.association_state = AssociationState.JOIN_PENDING
            existing_neighbor.last_heard = current_time
            existing_neighbor.expiration_time = expiration_time
            existing_neighbor.model_id = model_id
            existing_neighbor.join_id = join_id

    def start_join(
        self,
        ip_address: str,
        port: int,
        join_id: int,
        model_id: Optional[int] = None,
        current_time: Optional[float] = None,
        neighbor_timeout: float = 30.0,
    ) -> None:
        """
        Record a JOIN request sent by this Sensor Node.

        The selected Cluster Head is stored as a known neighbor
        in JOIN_PENDING state until a matching JOIN_ACCEPT is received.
        If the Sensor Node is already associated, the JOIN is a membership
        refresh and the existing ASSOCIATED state is retained.
        """

        if current_time is None:
            current_time = time.time()

        expiration_time = current_time + neighbor_timeout

        existing_neighbor = self.get_neighbor(
            ip_address,
            port,
        )

        if existing_neighbor is None:
            self.add_neighbor(
                ip_address=ip_address,
                port=port,
                node_role=NodeRole.CLUSTER_HEAD,
                association_state=AssociationState.JOIN_PENDING,
                last_heard=current_time,
                expiration_time=expiration_time,
                model_id=model_id,
                join_id=join_id,
            )
        else:
            existing_neighbor.node_role = NodeRole.CLUSTER_HEAD
            if existing_neighbor.association_state != AssociationState.ASSOCIATED:
                existing_neighbor.association_state = AssociationState.JOIN_PENDING
            existing_neighbor.last_heard = current_time
            existing_neighbor.expiration_time = expiration_time
            existing_neighbor.model_id = model_id
            existing_neighbor.join_id = join_id

    def process_join_accept(
        self,
        ip_address: str,
        port: int,
        join_id: int,
        rejected: int = 0,
        current_time: Optional[float] = None,
        neighbor_timeout: float = 30.0,
    ) -> bool:
        if current_time is None:
            current_time = time.time()

        neighbor = self.get_neighbor(ip_address, port)

        if neighbor is None:
            return False

        if neighbor.association_state not in (
            AssociationState.JOIN_PENDING,
            AssociationState.ASSOCIATED,
        ):
            return False

        if neighbor.join_id != join_id:
            return False

        if rejected:
            neighbor.association_state = AssociationState.DISCOVERED
            neighbor.last_heard = current_time
            neighbor.expiration_time = current_time + neighbor_timeout
            neighbor.join_id = None
            return False

        neighbor.association_state = AssociationState.ASSOCIATED
        neighbor.last_heard = current_time
        neighbor.expiration_time = current_time + neighbor_timeout

        return True

    def process_leave(
        self,
        ip_address: str,
        port: int,
        current_time: Optional[float] = None,
    ) -> bool:
        """
        Process a LEAVE message from a neighbor.

        A known neighbor is marked INACTIVE rather than
        immediately removed from the Neighbor Table.
        """

        if current_time is None:
            current_time = time.time()

        neighbor = self.get_neighbor(
            ip_address,
            port,
        )

        if neighbor is None:
            return False

        neighbor.association_state = AssociationState.INACTIVE
        neighbor.last_heard = current_time
        neighbor.expiration_time = current_time

        return True

    def process_sensor_data(
        self,
        ip_address: str,
        port: int,
        current_time: Optional[float] = None,
        neighbor_timeout: float = 30.0,
    ) -> bool:
        if current_time is None:
            current_time = time.time()

        neighbor = self.get_neighbor(ip_address, port)

        if neighbor is None:
            return False

        if neighbor.node_role != NodeRole.SENSOR_NODE:
            return False

        if neighbor.association_state == AssociationState.JOIN_PENDING:
            neighbor.association_state = AssociationState.ASSOCIATED
            neighbor.last_heard = current_time
            neighbor.expiration_time = current_time + neighbor_timeout
            return True

        if neighbor.association_state == AssociationState.ASSOCIATED:
            neighbor.last_heard = current_time
            neighbor.expiration_time = current_time + neighbor_timeout
            return True

        return False

    def check_expired_neighbors(
        self,
        current_time: Optional[float] = None,
    ) -> List[Neighbor]:
        """
        Check all neighbors for expiration.

        Neighbors whose expiration time has been reached
        are marked INACTIVE.
        """

        if current_time is None:
            current_time = time.time()

        expired_neighbors = []

        for neighbor in self.neighbor_table.values():
            if (
                neighbor.association_state != AssociationState.INACTIVE
                and current_time >= neighbor.expiration_time
            ):
                neighbor.association_state = AssociationState.INACTIVE
                expired_neighbors.append(neighbor)

        return expired_neighbors

    def refresh_neighbor(
        self,
        ip_address: str,
        port: int,
        current_time: Optional[float] = None,
        neighbor_timeout: float = 30.0,
    ) -> bool:
        """
        Refresh the membership lifetime of a known neighbor.
        """

        if current_time is None:
            current_time = time.time()

        neighbor = self.get_neighbor(
            ip_address,
            port,
        )

        if neighbor is None:
            return False

        if neighbor.association_state == AssociationState.INACTIVE:
            return False

        neighbor.last_heard = current_time
        neighbor.expiration_time = (
            current_time + neighbor_timeout
        )

        return True
