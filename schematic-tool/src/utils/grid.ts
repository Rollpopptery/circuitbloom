// Grid units and snapping. Stub — refine when interactive editing begins.

export const GRID_UNIT = 1; // internal coordinate unit
export const DEFAULT_GRID = 50; // default snap distance, tuned later

export function snap(value: number, grid = DEFAULT_GRID): number {
  return Math.round(value / grid) * grid;
}
