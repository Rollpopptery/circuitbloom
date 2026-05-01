// Netlist types. Output of extraction, input to simulation.
// Stub — fleshed out when we build the extractor.

export interface NetlistComponent {
  refdes: string;
  symbolId: string;
  value: string;
  pinNets: Record<string, string>; // pinId -> netName
}

export interface Net {
  name: string;
  pins: Array<{ refdes: string; pinId: string }>;
}

export interface Netlist {
  components: NetlistComponent[];
  nets: Net[];
}
