from flame.neighbor.neighbor_module import (
    AssociationState,
    NeighborModule,
    NodeRole,
)


SENSOR_IP = "192.168.1.10"
SENSOR_PORT = 5001

CH_IP = "192.168.1.20"
CH_PORT = 5000

MODEL_ID = 1
JOIN_ID = 100


def print_header(step, title):
    print("\n" + "=" * 70)
    print(f"STEP {step} - {title}")
    print("=" * 70)


def print_neighbor(label, neighbor):
    print(f"\n{label}")
    print("-" * 50)

    if neighbor is None:
        print("No neighbor entry.")
        return

    print(f"IP Address : {neighbor.ip_address}")
    print(f"Port       : {neighbor.port}")
    print(f"Role       : {neighbor.node_role.value}")
    print(f"State      : {neighbor.association_state.value}")

    if neighbor.join_id is not None:
        print(f"Join ID    : {neighbor.join_id}")

    if neighbor.model_id is not None:
        print(f"Model ID   : {neighbor.model_id}")


def print_final_table(neighbor_module):
    print("\n" + "=" * 70)
    print("FINAL CLUSTER HEAD NEIGHBOR TABLE")
    print("=" * 70)

    print(
        f"{'IP Address':<18}"
        f"{'Port':<8}"
        f"{'Role':<16}"
        f"{'State':<16}"
        f"{'Join ID':<10}"
        f"{'Model ID':<10}"
    )

    print("-" * 70)

    for neighbor in neighbor_module.get_neighbors():
        print(
            f"{neighbor.ip_address:<18}"
            f"{neighbor.port:<8}"
            f"{neighbor.node_role.value:<16}"
            f"{neighbor.association_state.value:<16}"
            f"{str(neighbor.join_id):<10}"
            f"{str(neighbor.model_id):<10}"
        )

    print("-" * 70)


def main():
    sensor = NeighborModule()
    cluster_head = NeighborModule()

    current_time = 1000.0

    # =========================================================
    # STEP 1 - HELLO
    # =========================================================

    print_header(1, "HELLO / DISCOVERY")

    print("MESSAGE")
    print("CH → Sensor: HELLO")

    sensor.process_hello(
        ip_address=CH_IP,
        port=CH_PORT,
        node_role=NodeRole.CLUSTER_HEAD,
        model_id=MODEL_ID,
        current_time=current_time,
    )

    sensor_neighbor = sensor.get_neighbor(
        CH_IP,
        CH_PORT,
    )

    print("\nRESULT")
    print("Sensor discovers the Cluster Head.")

    print_neighbor(
        "SENSOR'S VIEW",
        sensor_neighbor,
    )

    # =========================================================
    # STEP 2 - JOIN
    # =========================================================

    current_time += 1

    print_header(2, "JOIN REQUEST")

    print("MESSAGE")
    print(f"Sensor → CH: JOIN (Join ID: {JOIN_ID})")

    sensor.start_join(
        ip_address=CH_IP,
        port=CH_PORT,
        join_id=JOIN_ID,
        model_id=MODEL_ID,
        current_time=current_time,
    )

    cluster_head.process_join(
        ip_address=SENSOR_IP,
        port=SENSOR_PORT,
        join_id=JOIN_ID,
        model_id=MODEL_ID,
        current_time=current_time,
    )

    cluster_neighbor = cluster_head.get_neighbor(
        SENSOR_IP,
        SENSOR_PORT,
    )

    print("\nRESULT")
    print("The Cluster Head records the Sensor as JOIN_PENDING.")

    print_neighbor(
        "CLUSTER HEAD'S VIEW",
        cluster_neighbor,
    )

    # =========================================================
    # STEP 3 - JOIN_ACCEPT
    # =========================================================

    current_time += 1

    print_header(3, "JOIN ACCEPT")

    print("MESSAGE")
    print("CH → Sensor: JOIN_ACCEPT")

    accepted = sensor.process_join_accept(
        ip_address=CH_IP,
        port=CH_PORT,
        join_id=JOIN_ID,
        rejected=0,
        current_time=current_time,
    )

    sensor_neighbor = sensor.get_neighbor(
        CH_IP,
        CH_PORT,
    )

    cluster_neighbor = cluster_head.get_neighbor(
        SENSOR_IP,
        SENSOR_PORT,
    )

    print("\nRESULT")
    print(f"JOIN accepted: {accepted}")

    print("\nSensor:")
    print(
        f"  {AssociationState.JOIN_PENDING.value}"
        f" → "
        f"{sensor_neighbor.association_state.value}"
    )

    print("\nCluster Head:")
    print(
        f"  remains {cluster_neighbor.association_state.value}"
    )

    print(
        "\nReason: the Cluster Head has accepted the JOIN, "
        "but has not yet received SENSOR_DATA."
    )

    # =========================================================
    # STEP 4 - SENSOR_DATA
    # =========================================================

    current_time += 1

    print_header(4, "SENSOR_DATA / ASSOCIATION CONFIRMATION")

    print("MESSAGE")
    print("Sensor → CH: SENSOR_DATA")

    associated = cluster_head.process_sensor_data(
        ip_address=SENSOR_IP,
        port=SENSOR_PORT,
        current_time=current_time,
    )

    cluster_neighbor = cluster_head.get_neighbor(
        SENSOR_IP,
        SENSOR_PORT,
    )

    print("\nRESULT")
    print(f"Association confirmed: {associated}")

    print("\nCluster Head:")
    print(
        f"  {AssociationState.JOIN_PENDING.value}"
        f" → "
        f"{cluster_neighbor.association_state.value}"
    )

    # =========================================================
    # STEP 5 - MEMBERSHIP REFRESH
    # =========================================================

    current_time += 10

    print_header(5, "MEMBERSHIP REFRESH")

    print("MESSAGE")
    print("Sensor → CH: SENSOR_DATA")

    cluster_neighbor = cluster_head.get_neighbor(
        SENSOR_IP,
        SENSOR_PORT,
    )

    previous_last_heard = cluster_neighbor.last_heard
    previous_expiration = cluster_neighbor.expiration_time

    timeout = 30.0

    refreshed = cluster_head.process_sensor_data(
        ip_address=SENSOR_IP,
        port=SENSOR_PORT,
        current_time=current_time,
        neighbor_timeout=timeout,
    )

    cluster_neighbor = cluster_head.get_neighbor(
        SENSOR_IP,
        SENSOR_PORT,
    )

    print("\nRESULT")
    print(f"Neighbor refreshed: {refreshed}")

    print(
        "The CH receives another valid SENSOR_DATA message "
        "and resets the membership timeout."
    )

    print("\nTIMEOUT CALCULATION")

    print(
        f"\nPrevious expiration:"
        f"\n  Last heard   = {previous_last_heard:.1f}"
        f"\n  Timeout      = {timeout:.0f} seconds"
        f"\n  Expiration   = {previous_last_heard:.1f} + {timeout:.0f}"
        f"\n               = {previous_expiration:.1f}"
    )

    print(
        f"\nAnother SENSOR_DATA arrives at:"
        f"\n  Current time = {current_time:.1f}"
    )

    print(
        f"\nNew expiration:"
        f"\n  Current time = {current_time:.1f}"
        f"\n  Timeout      = {timeout:.0f} seconds"
        f"\n  Expiration   = {current_time:.1f} + {timeout:.0f}"
        f"\n               = {cluster_neighbor.expiration_time:.1f}"
    )

    print(
        f"\nTherefore:"
        f"\n  {previous_expiration:.1f} → "
        f"{cluster_neighbor.expiration_time:.1f}"
    )

    print(
        "\nThe Sensor remains ASSOCIATED as long as valid "
        "activity is received before the expiration time."
    )

    # =========================================================
    # STEP 6 - GRACEFUL DISCONNECTION
    # =========================================================

    current_time += 1

    print_header(6, "GRACEFUL DISCONNECTION")

    print("SCENARIO")
    print(
        "The Sensor has finished its task and intentionally "
        "disconnects from the Cluster Head."
    )

    print("\nMESSAGE")
    print("Sensor → CH: LEAVE")

    left = cluster_head.process_leave(
        ip_address=SENSOR_IP,
        port=SENSOR_PORT,
        current_time=current_time,
    )

    cluster_neighbor = cluster_head.get_neighbor(
        SENSOR_IP,
        SENSOR_PORT,
    )

    print("\nRESULT")
    print(f"Leave processed: {left}")

    print("\nCluster Head:")
    print(
        f"  {AssociationState.ASSOCIATED.value}"
        f" → "
        f"{cluster_neighbor.association_state.value}"
    )

    print(
        "\nThe CH immediately marks the Sensor as INACTIVE "
        "instead of waiting for the membership timeout."
    )

    print(
        "\nWHY LEAVE?"
        "\n  LEAVE handles intentional disconnections."
        "\n  Timeout handles unexpected disconnections."
    )

    # =========================================================
    # FINAL TABLE
    # =========================================================

    print_final_table(cluster_head)

    # =========================================================
    # SEPARATE TIMEOUT DEMONSTRATION
    # =========================================================

    print("\n" + "=" * 70)
    print("SEPARATE TIMEOUT DEMONSTRATION")
    print("=" * 70)

    timeout_sensor_ip = "192.168.1.11"
    timeout_sensor_port = 5002

    print("\nSCENARIO")
    print(
        "A second Sensor is discovered but unexpectedly "
        "stops communicating."
    )

    print("\n1. CH discovers the Sensor.")

    cluster_head.process_hello(
        ip_address=timeout_sensor_ip,
        port=timeout_sensor_port,
        node_role=NodeRole.SENSOR_NODE,
        model_id=2,
        current_time=2000.0,
        neighbor_timeout=30.0,
    )

    timeout_neighbor = cluster_head.get_neighbor(
        timeout_sensor_ip,
        timeout_sensor_port,
    )

    print(
        f"   State: {timeout_neighbor.association_state.value}"
    )

    print("\n2. No further messages are received.")

    print("\n3. Timeout calculation:")
    print("   Last heard       = 2000")
    print("   Neighbor timeout = 30 seconds")
    print("   Expiration       = 2000 + 30")
    print("                     = 2030")

    print("\n4. The expiration time is reached.")

    expired = cluster_head.check_expired_neighbors(
        current_time=2031.0,
    )

    timeout_neighbor = cluster_head.get_neighbor(
        timeout_sensor_ip,
        timeout_sensor_port,
    )

    print(f"   Expired neighbors: {len(expired)}")
    print(
        f"   State: "
        f"{AssociationState.DISCOVERED.value}"
        f" → "
        f"{timeout_neighbor.association_state.value}"
    )

    print("\n" + "=" * 70)
    print("NEIGHBOR MODULE DEMONSTRATION COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()