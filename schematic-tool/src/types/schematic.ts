// Core schematic data model. Single source of truth for what a schematic is.

export interface Point {
  x: number;
  y: number;
}

export type Justify =
  | "left"
  | "right"
  | "center"
  | "top"
  | "bottom"
  | "left top"
  | "left bottom"
  | "right top"
  | "right bottom";

export interface TextItem {
  content: string;
  position: Point; // absolute coordinates in the parent's space
  rotation: number; // degrees, 0 = no rotation
  fontSize: number; // in same units as positions (mm for KiCad)
  justify?: Justify;
  hidden?: boolean;
}

export type Rotation = 0 | 90 | 180 | 270;

export interface Pin {
  id: string; // unique within the symbol, e.g. "1", "2", "VCC"
  name: string; // human label, e.g. "VCC", "OUT"
  position: Point; // in symbol-local coordinates
}

export type SymbolGraphic =
  | { type: "line"; points: Point[]; stroke?: string; strokeWidth?: number }
  | {
      type: "rectangle";
      min: Point;
      max: Point;
      fill?: string;
      stroke?: string;
      strokeWidth?: number;
    }
  | {
      type: "circle";
      center: Point;
      radius: number;
      fill?: string;
      stroke?: string;
      strokeWidth?: number;
    }
  | {
      type: "arc";
      center: Point;
      radius: number;
      startAngle: number;
      endAngle: number;
      stroke?: string;
      strokeWidth?: number;
    }
  | {
      type: "polyline";
      points: Point[];
      fill?: string;
      stroke?: string;
      strokeWidth?: number;
    }
  | {
      type: "text";
      position: Point;
      content: string;
      fontSize?: number;
      fill?: string;
    };

export interface Symbol {
  id: string; // unique within file, e.g. "Device:R"
  name: string; // display name
  pins: Pin[];
  graphics: SymbolGraphic[];
  bbox?: { min: Point; max: Point };
}

export interface ComponentInstance {
  refdes: string; // "R1", "U3"
  symbolId: string; // references Symbol or Subcircuit
  position: Point;
  rotation: Rotation;
  mirror?: boolean;  // (mirror y) — flip left-right, negates X in local space
  mirrorX?: boolean; // (mirror x) — flip upside-down, negates Y in local space
  value: string; // "10k", "100nF"
  properties: Record<string, string>;
  texts?: Record<string, TextItem>; // keyed by lowercased property name
}

export interface Wire {
  id: string;
  points: Point[]; // polyline; for now treat as segments via consecutive pairs
}

export interface NetLabel {
  position: Point;
  name: string; // "VCC", "GND", or a signal name
  isPower?: boolean; // true for global power nets
  rotation?: number; // degrees
  fontSize?: number;
  justify?: Justify;
}

export interface Junction {
  position: Point;
}

export interface Port {
  // External connection of a subcircuit when used as a hierarchical component.
  id: string;
  name: string;
  position: Point;
}

export interface Subcircuit {
  id: string;
  name: string;
  ports: Port[];
  components: ComponentInstance[];
  wires: Wire[];
  junctions: Junction[];
  labels: NetLabel[];
}

export interface Schematic {
  version: string;
  symbols: Record<string, Symbol>;
  subcircuits: Record<string, Subcircuit>;
  topLevel: string; // id of the top subcircuit
}
