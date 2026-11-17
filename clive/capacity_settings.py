"""CLIVE-only, per-role resource controls; no FLAME implementation changes.

All capacities are counts except radio-queue bytes. ``None`` means inherit the
preexisting global configuration. FLAME internal receive histories and reassembly state are NOT replaced or resized by CLIVE.
"""

OVERRIDES = {
    "sn_send_buffer_capacity": ("sn", "send", 128),
    "ch_send_buffer_capacity": ("ch", "send", 128),
    "sn_datagram_buffer_capacity": ("sn", "datagram", 128),
    "ch_datagram_buffer_capacity": ("ch", "datagram", 128),
    "sn_sample_buffer_capacity": ("sn", "sample", 100),
    "sn_offline_buffer_capacity": ("sn", "offline", 64),
    "ch_dataset_capacity": ("ch", "dataset", 512),
    "sn_radio_queue_packets": ("sn", "radio_packets", 4096),
    "ch_radio_queue_packets": ("ch", "radio_packets", 4096),
    "sn_radio_queue_bytes": ("sn", "radio_bytes", 8 * 1024 * 1024),
    "ch_radio_queue_bytes": ("ch", "radio_bytes", 8 * 1024 * 1024),
}

def check_overrides(settings):
    for key in OVERRIDES:
        value = settings.get(key)
        if value is not None:
            if type(value) is not int or value <= 0:
                raise ValueError(f"flame.{key} must be a positive integer or null (inherit)")
            limit = (1 << 30) if key.endswith("_bytes") else 1_000_000
            if value > limit:
                raise ValueError(f"flame.{key} exceeds the configuration safety bound {limit}")

def effective(settings, role, component, fallback):
    prefix = "ch" if int(role) == 1 else "sn"
    key = next((k for k, (r, c, _) in OVERRIDES.items() if r == prefix and c == component), None)
    if key is None:
        return fallback
    value = settings.get(key)
    return fallback if value is None else value
