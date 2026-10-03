// Frame-driven motion primitives, after Remotion's: everything is a pure function of the frame number,
// so any frame renders the same way every time and in any order.

/** A CSS-style cubic-bezier easing function. */
export function bezier(x1, y1, x2, y2) {
  const curve = (a, b, t) => ((1 - 3 * b + 3 * a) * t + (3 * b - 6 * a)) * t * t + 3 * a * t;
  const slope = (a, b, t) => 3 * (1 - 3 * b + 3 * a) * t * t + 2 * (3 * b - 6 * a) * t + 3 * a;
  return (x) => {
    if (x <= 0 || x >= 1) return Math.min(1, Math.max(0, x));
    let t = x;
    for (let i = 0; i < 8; i++) {
      const error = curve(x1, x2, t) - x;
      const d = slope(x1, x2, t);
      if (Math.abs(error) < 1e-6) break;
      if (Math.abs(d) < 1e-6) break;
      t -= error / d;
    }
    let low = 0, high = 1;
    for (let i = 0; i < 30 && Math.abs(curve(x1, x2, t) - x) > 1e-6; i++) {
      if (curve(x1, x2, t) < x) low = t; else high = t;
      t = (low + high) / 2;
    }
    return curve(y1, y2, t);
  };
}

/**
 * Map `value` from `input` stops to `output` stops. Always clamped at both ends, and an easing is
 * required: linear interpolation is not allowed in this video.
 */
export function interpolate(value, input, output, easing) {
  if (typeof easing !== "function") throw new Error("interpolate needs an easing curve");
  if (value <= input[0]) return output[0];
  if (value >= input[input.length - 1]) return output[output.length - 1];
  let i = 1;
  while (value > input[i]) i++;
  const progress = easing((value - input[i - 1]) / (input[i] - input[i - 1]));
  return output[i - 1] + (output[i] - output[i - 1]) * progress;
}

/** A damped spring from 0 to 1 that starts at `frame` 0, solved analytically (same physics as Remotion). */
export function spring(frame, fps, { damping, stiffness, mass }) {
  if (frame <= 0) return 0;
  const t = frame / fps;
  const omega = Math.sqrt(stiffness / mass);
  const zeta = damping / (2 * Math.sqrt(stiffness * mass));
  let offset, envelope;
  if (zeta < 1) {
    const omegaD = omega * Math.sqrt(1 - zeta * zeta);
    const decay = Math.exp(-zeta * omega * t);
    offset = decay * (-Math.cos(omegaD * t) - ((zeta * omega) / omegaD) * Math.sin(omegaD * t));
    envelope = decay * Math.hypot(1, (zeta * omega) / omegaD);
  } else if (zeta > 1) {
    const root = Math.sqrt(zeta * zeta - 1);
    const slow = -omega * (zeta - root), fast = -omega * (zeta + root);
    const c1 = fast / (slow - fast);
    offset = c1 * Math.exp(slow * t) + (-1 - c1) * Math.exp(fast * t);
    envelope = Math.abs(offset);
  } else {
    offset = -Math.exp(-omega * t) * (1 + omega * t);
    envelope = Math.abs(offset);
  }
  // Snap to rest once the oscillation can no longer move a pixel, so held frames are identical.
  return envelope < 0.002 ? 1 : 1 + offset;
}

export const mix = (from, to, p) => from + (to - from) * p;
