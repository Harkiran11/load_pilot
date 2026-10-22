export interface ClockSample {
  timeMs: number
  speed: number
  receivedAtMs: number
}

export function createClockSample(time: string | null, speed: string | number | null, receivedAtMs: number): ClockSample | null {
  const timeMs = time ? Date.parse(time) : NaN
  const multiplier = speed === null ? NaN : Number(speed)
  if (!Number.isFinite(timeMs) || !Number.isFinite(multiplier) || multiplier < 0) return null
  return { timeMs, speed: multiplier, receivedAtMs }
}

export function simulationTime(sample: ClockSample | null, nowMs: number): number | null {
  if (!sample) return null
  // Monotonic elapsed time avoids jumps when the computer's wall clock changes.
  const time = sample.timeMs + Math.max(0, nowMs - sample.receivedAtMs) * sample.speed
  return Number.isFinite(time) && Math.abs(time) <= 8.64e15 ? time : null
}
