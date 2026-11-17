"use strict";
/* Shared helpers so every CLIVE chart labels its axes with exact, round values.
 * Previously ticks were max*i/4 rounded for display, so a 6 s run was labelled
 * 0,2,3,5,6 s at positions 0,1.5,3,4.5,6 s and a max of 5 packets was labelled
 * 0,1,3,4,5 at positions 0,1.25,2.5,3.75,5.
 */
window.CliveChart = {
  /** Round step (1, 2, 2.5, 5 × 10^n) so that ~`target` ticks cover [0, max]. */
  niceStep(max, target = 4, { integer = false } = {}) {
    if (!(max > 0) || !Number.isFinite(max)) return 1;
    const raw = max / Math.max(1, target);
    const power = Math.pow(10, Math.floor(Math.log10(raw)));
    const unit = raw / power;
    const factor = unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 2.5 ? 2.5 : unit <= 5 ? 5 : 10;
    let step = factor * power;
    if (integer && step < 1) step = 1;
    if (integer && !Number.isInteger(step)) step = Math.ceil(step);
    return step;
  },
  /** Tick values 0, step, 2·step … <= max (always includes 0). */
  ticks(max, target = 4, options = {}) {
    const step = this.niceStep(max, target, options);
    const out = [];
    for (let i = 0; i * step <= max * (1 + 1e-9); i++) out.push(+(i * step).toPrecision(12));
    return { step, values: out };
  },
  /** Format seconds without hiding the fraction that a tick actually has. */
  seconds(value, step = 1) {
    const decimals = step >= 1 ? 0 : Math.min(3, Math.ceil(-Math.log10(step)));
    return `${value.toFixed(decimals)}s`;
  },
};
