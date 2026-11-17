# CLIVE analytical radio-energy correction — 100 pJ with d^n

**Scope:** CLIVE-only patch applied after the earlier November 14 hardening. All `flame/` files remain identical to the baseline.

## Updated equation and coefficient

- **Keep:** `E_tx(k,d) = k * (E_elec + epsilon_n * d**n)` and `E_rx(k) = k*E_elec`.
- `E_elec = 50e-9 J/bit = 50 nJ/bit`.
- `epsilon_n = 100e-12 J/(bit*m**n) = 100 pJ/(bit*m**n)` **as the team's chosen nominal energy model**.
- `n = pathloss_exponent`, default `3` for the thesis radio scenarios.
- No change to RSSI, bandwidth, modeled losses, mobility, packet format, or transport behavior.

The original coefficient was **100e-9 J**, mistakenly using nanojoules instead of picojoules; this fix changes only the amplifier term to **100e-12 J**, 1000 times smaller. Circuit energy `E_elec` is unchanged. Total transmission energy is therefore not necessarily 1000 times smaller because the circuit term is still present.

### Important source limitation

The widely cited **100 pJ/(bit*m**2)** value comes from the **d**2 first-order wireless-sensor model. Applying the same *numerical value* to `d**3` with units **pJ/(bit*m**3)** is dimensionally consistent but **not an independently calibrated physical model**. The team must explicitly approve or calibrate this coefficient before claiming realistic absolute joule values. Do not silently substitute `d**2`; the thesis says `d**n`.

### Compatibility, old artifacts and review

- The config field is now `e_amp_j_per_bit_mn`. Explicit old `e_amp_j_per_bit_m2` configurations **fail with a migration message** rather than silently keeping stale 100 nJ settings. Convert these manually and choose the intended coefficient.
- Shipped GUI/all-options config examples use 100 pJ. New configurations written by CLIVE use the new field.
- Existing experimental metrics and ZIPs are unchanged. **Rerun experiments before comparing energy results**. Store build hash, seed, n, and coefficient with each result.
- `metrics.json` includes the exact formula, coefficient, path-loss exponent, unit string, and an uncalibrated-model warning.

### Example

For 1,024 bytes (=8,192 bits), 20 m and n=3: `E_tx = 8192*(50e-9 + 100e-12*20**3) = 0.0069632 J` (6.9632 mJ). The prior 100 nJ amplifier assumption produced 6.5540096 J for the same inputs.
