# Interactive FLAME simulation

This simulation uses the existing FLAME communication, reliability, neighbor,
and file-manifest modules. Run one Cluster Head and one or more Sensor Nodes
in separate terminals.

## 1. Start the Cluster Head

From the repository root:

```bash
cd /Users/adlerclarencestrebel/Documents/GitHub/NIS23-Code
python3 tests/simulation/run_node.py --role cluster-head
```

The default Cluster Head data port is `50000`. It sends broadcast HELLO
messages on discovery port `50010`. The default HELLO interval is defined by
`DEFAULT_HELLO_INTERVAL` in `run_node.py` and can be overridden at startup:

```bash
python3 tests/simulation/run_node.py \
  --role cluster-head \
  --hello-interval 2
```

## 2. Start Sensor Nodes

Open another terminal for each Sensor Node. Use a separate `--data-dir` so
each Sensor has its own test-image folder:

```bash
cd /Users/adlerclarencestrebel/Documents/GitHub/NIS23-Code
python3 tests/simulation/run_node.py \
  --role sensor \
  --data-dir tests/simulation/sensor_1
```

Start a second Sensor in another terminal with the same discovery port and a
different data directory:

```bash
cd /Users/adlerclarencestrebel/Documents/GitHub/NIS23-Code
python3 tests/simulation/run_node.py \
  --role sensor \
  --data-dir tests/simulation/sensor_2
```

The `--role` option is optional. If omitted, the terminal asks whether the
instance is a `sensor` or `cluster-head`.

After receiving HELLO, each Sensor automatically sends JOIN. A successful
association is shown as:

```text
[RECV] JOIN_ACCEPT ...
[ASSOCIATION] JOIN_ACCEPT ... state=ASSOCIATED
```

## 3. Send Images

Only PNG and JPEG files are accepted. Place an image in the Sensor's `send`
folder:

```bash
cp /path/to/image.png tests/simulation/sensor_1/send/
```

The simulation also includes sample images in `tests/simulation/images/`, such
as `1.png`, `2.png`, `3.png`, and `9.png`. The recommended test flow is:

```bash
cp tests/simulation/images/1.png tests/simulation/sensor_1/send/
```

At that Sensor's `flame>` prompt, send one image. FLAME assigns the
`data_id` automatically for the transfer:

```text
send 1.png
```

Because relative paths are resolved from the Sensor's `send` folder, an
absolute or repository-relative path can also be used directly:

```text
send tests/simulation/images/2.png
```

When a path outside the Sensor's `send` folder is used, the runner stages a
copy in that Sensor's `send` folder before transmission.

To send multiple files from one Sensor:

```text
send-batch image.png second.jpg
```

Each file in a batch receives a separate automatically assigned `data_id`.
The assigned values remain visible in the `[SEND]`, `[REASSEMBLED]`, and
`[TRANSFER]` terminal logs. Files larger than 1024 bytes are automatically
fragmented and reassembled by the existing FLAME communication and
reliability code.

## 4. Monitor Communication

The terminals display communication events while the simulation is running:

```text
[SEND] HELLO ...
[RECV] HELLO ...
[SEND] JOIN ...
[RECV] JOIN_ACCEPT ...
[SEND] SENSOR_DATA ...
[RECV] SENSOR_DATA ...
[SEND] SENSOR_DATA_ACK ...
[WINDOW] SENSOR_DATA ... advanced next=2-4 size=3
[REASSEMBLED] SENSOR_DATA ...
[TRANSFER] complete ...
```

The command prompt remains usable while background HELLO and transfer events
are displayed. The reliable data window starts at size `2` and increases to
`3`, `4`, and so on after successful windows, up to the configured maximum of
`8`. Loss or timeout causes AIMD to reduce the window and retransmit missing
blocks.

## 5. Inspect Files and Neighbor State

The simulation creates these folders:

```text
tests/simulation/cluster_head/send/
tests/simulation/cluster_head/received/
tests/simulation/sensor_1/send/
tests/simulation/sensor_2/send/
```

The Cluster Head saves each reassembled file in `received/`. Saved names
include the source endpoint and `data_id`, so transfers from different Sensors
do not overwrite one another.

Useful terminal commands are:

```text
status
neighbors
export [csv_path]
quit
```

When the Cluster Head exits, it exports the existing neighbor table to:

```text
tests/simulation/cluster_head/neighbor_state.csv
```

Use `--neighbor-csv /path/to/neighbors.csv` to choose another CSV path. Stop
Sensor Nodes with `quit` first, then stop the Cluster Head so its final
neighbor states can be exported.

## Configuration Defaults

| Setting | Default |
| --- | --- |
| Cluster Head data port | `50000` |
| HELLO discovery port | `50010` |
| Cluster Head bind host | `0.0.0.0` |
| Sensor data port | Ephemeral |
| Maximum application fragment payload | `1024` bytes |

For help with all options:

```bash
python3 tests/simulation/run_node.py --help
```
