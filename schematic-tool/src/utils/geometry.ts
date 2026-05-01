import type { Point, Rotation } from "../types";

export function pointsEqual(a: Point, b: Point): boolean {
  return a.x === b.x && a.y === b.y;
}

export function transformPoint(
  p: Point,
  origin: Point,
  rotation: Rotation,
  mirror = false,
  mirrorX = false,
): Point {
  let { x, y } = p;
  if (mirror)  x = -x;
  if (mirrorX) y = -y;
  switch (rotation) {
    case 90:
      [x, y] = [y, -x];
      break;
    case 180:
      [x, y] = [-x, -y];
      break;
    case 270:
      [x, y] = [-y, x];
      break;
  }
  return { x: x + origin.x, y: y + origin.y };
}

// More geometry helpers as needed.
