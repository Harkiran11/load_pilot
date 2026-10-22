import type { TerminalStatus } from '../api/terminals'

export function terminalPosition(terminal: Pick<TerminalStatus, 'latitude' | 'longitude'>): [number, number] | null {
  if (terminal.latitude === null || terminal.longitude === null || !String(terminal.latitude).trim() || !String(terminal.longitude).trim()) return null
  const latitude = Number(terminal.latitude)
  const longitude = Number(terminal.longitude)
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude) || Math.abs(latitude) > 90 || Math.abs(longitude) > 180) return null
  return [latitude, longitude]
}

export const GLOBE_WIDTH = 1259
export const GLOBE_HEIGHT = 477
export const WORLD_VIEW = { x: 0, y: 0, width: GLOBE_WIDTH, height: GLOBE_HEIGHT }

// Must match the equirectangular projection used by svg-globe-path.ts.
export function terminalGlobePoint(terminal: Pick<TerminalStatus, 'latitude' | 'longitude'>) {
  const position = terminalPosition(terminal)
  if (!position) return null
  const [latitude, longitude] = position
  return { x: (longitude + 180) / 360 * GLOBE_WIDTH, y: (90 - latitude) / 180 * GLOBE_HEIGHT }
}

export function terminalGlobeBounds(points: Array<{ x: number; y: number }>, aspectRatio = 2) {
  if (!points.length) return WORLD_VIEW
  const xs = points.map((point) => point.x)
  const ys = points.map((point) => point.y)
  const left = Math.min(...xs), right = Math.max(...xs)
  const top = Math.min(...ys), bottom = Math.max(...ys)
  // Focus on the terminal region, retaining room for labels around its edges.
  let width = Math.max(100, (right - left) * 1.2 + 30)
  let height = Math.max(50, (bottom - top) * 1.2 + 20)
  width = Math.max(width, height * aspectRatio)
  height = Math.max(height, width / aspectRatio)
  if (width >= GLOBE_WIDTH || height >= GLOBE_HEIGHT) return WORLD_VIEW
  return {
    x: Math.max(0, Math.min(GLOBE_WIDTH - width, (left + right) / 2 - width / 2)),
    y: Math.max(0, Math.min(GLOBE_HEIGHT - height, (top + bottom) / 2 - height / 2)),
    width,
    height,
  }
}

export function terminalMapTone(terminal: TerminalStatus): 'available' | 'unavailable' | 'attention' | 'unknown' {
  if (terminal.status === 'attention' || terminal.consistent === false) return 'attention'
  if (terminal.status === 'unknown' || terminal.available_units === null) return 'unknown'
  return terminal.available_units > 0 ? 'available' : 'unavailable'
}

export const TERMINAL_MARKER_WIDTH = 88
export const TERMINAL_MARKER_HEIGHT = 46
const MARKER_LABEL_OFFSET_Y = 22 // Label center sits above its fixed geographic circle.
const MARKER_SPACING_X = TERMINAL_MARKER_WIDTH + 8
const MARKER_SPACING_Y = TERMINAL_MARKER_HEIGHT + 8
const MARKER_INSET_X = TERMINAL_MARKER_WIDTH / 2 + 8
const MARKER_INSET_Y = TERMINAL_MARKER_HEIGHT / 2 + 8

/** Place independent screen-sized buttons; displaced markers retain geographic anchors. */
export function layoutTerminalMarkers<T extends { id: string; x: number; y: number }>(
  points: T[],
  bounds: { x: number; y: number; width: number; height: number },
  viewport: { width: number; height: number },
) {
  const unit = Math.max(bounds.width / viewport.width, bounds.height / viewport.height)
  const offsetX = (viewport.width - bounds.width / unit) / 2
  const offsetY = (viewport.height - bounds.height / unit) / 2
  const placed: Array<T & { screenX: number; screenY: number; labelX: number; labelY: number; displaced: boolean }> = []
  const overflow: T[] = []
  const circles = points.map((point) => ({
    x: (point.x - bounds.x) / unit + offsetX,
    y: (point.y - bounds.y) / unit + offsetY,
  }))
  const maxX = viewport.width - MARKER_INSET_X
  const maxY = viewport.height - MARKER_INSET_Y
  const free = (x: number, y: number) => placed.every((marker) =>
    Math.abs(marker.screenX - x) >= MARKER_SPACING_X || Math.abs(marker.screenY - y) >= MARKER_SPACING_Y,
  ) && circles.every((circle) =>
    Math.abs(circle.x - x) >= TERMINAL_MARKER_WIDTH / 2 + 8 || Math.abs(circle.y - y) >= 18,
  )

  // Stable ordering prevents capacity snapshots or API ordering from shuffling labels.
  for (const point of [...points].sort((a, b) => a.id.localeCompare(b.id))) {
    const anchorX = (point.x - bounds.x) / unit + offsetX
    const anchorY = (point.y - bounds.y) / unit + offsetY - MARKER_LABEL_OFFSET_Y
    const x = Math.max(MARKER_INSET_X, Math.min(maxX, anchorX))
    const y = Math.max(MARKER_INSET_Y, Math.min(maxY, anchorY))
    let candidate: { x: number; y: number } | null = null
    if (maxX >= MARKER_INSET_X && maxY >= MARKER_INSET_Y && free(x, y)) {
      candidate = { x, y }
    } else {
      let nearest = Infinity
      // Search for the nearest free slot, rather than shifting every terminal.
      for (let gridY = MARKER_INSET_Y; gridY <= maxY; gridY += 8) {
        for (let gridX = MARKER_INSET_X; gridX <= maxX; gridX += 8) {
          const distance = (gridX - anchorX) ** 2 + (gridY - anchorY) ** 2
          if (distance < nearest && free(gridX, gridY)) {
            candidate = { x: gridX, y: gridY }
            nearest = distance
          }
        }
      }
    }
    // Extremely dense views keep remaining terminals accessible in a separate row.
    if (!candidate) { overflow.push(point); continue }
    placed.push({
      ...point,
      screenX: candidate.x,
      screenY: candidate.y,
      labelX: (candidate.x - offsetX) * unit + bounds.x,
      labelY: (candidate.y - offsetY) * unit + bounds.y,
      displaced: Math.hypot(candidate.x - anchorX, candidate.y - anchorY) > 1,
    })
  }
  return { placed, overflow }
}

/** Projects a geographic point into the same screen space as the placed markers. */
export function projectToScreen(
  point: { x: number; y: number },
  bounds: { x: number; y: number; width: number; height: number },
  viewport: { width: number; height: number },
) {
  const unit = Math.max(bounds.width / viewport.width, bounds.height / viewport.height)
  const offsetX = (viewport.width - bounds.width / unit) / 2
  const offsetY = (viewport.height - bounds.height / unit) / 2
  return { x: (point.x - bounds.x) / unit + offsetX, y: (point.y - bounds.y) / unit + offsetY }
}

/** Inverse of {@link projectToScreen}, used to keep label offsets inside the drawing. */
export function projectToMap(
  point: { x: number; y: number },
  bounds: { x: number; y: number; width: number; height: number },
  viewport: { width: number; height: number },
) {
  const unit = Math.max(bounds.width / viewport.width, bounds.height / viewport.height)
  const offsetX = (viewport.width - bounds.width / unit) / 2
  const offsetY = (viewport.height - bounds.height / unit) / 2
  return { x: (point.x - offsetX) * unit + bounds.x, y: (point.y - offsetY) * unit + bounds.y }
}
