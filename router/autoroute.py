#!/usr/bin/env python3
"""
autoroute.py — Route all nets on a loaded board via the TCP API.

Computes pad positions from the .dpcb file, then sends route commands
in priority order: local nets first, signals, power last.

Usage:
    python3 autoroute.py [host] [port]
    python3 autoroute.py                   # defaults: 172.17.0.1:9876
    python3 autoroute.py localhost 9876    # direct host access
"""

import socket
import sys
import re
import math
import time


# ============ DPCB PARSING (minimal, just for pad positions) ============

def rotate_pad(dx, dy, angle_deg):
    a = math.radians(angle_deg)
    cos_a = round(math.cos(a), 6)
    sin_a = round(math.sin(a), 6)
    return round(dx * cos_a - dy * sin_a, 4), round(dx * sin_a + dy * cos_a, 4)


def parse_board(path):
    """Parse .dpcb, return dict of ref.pin -> (x, y) and net definitions."""
    with open(path) as f:
        text = f.read()

    footprints = []   # (ref, lib, footprint, x, y, rotation)
    pad_defs = {}     # footprint_name -> [(pin, dx, dy), ...]
    nets = []         # (net_name, [(ref, pin), ...])

    for line in text.split('\n'):
        line = re.sub(r'#.*$', '', line).strip()
        if not line:
            continue

        m = re.match(r'^FP:([^:]+):([^:]+):([^@]+)@\(([^,]+),([^)]+)\)(?::r(\d+))?', line)
        if m:
            footprints.append((
                m.group(1), m.group(2), m.group(3),
                float(m.group(4)), float(m.group(5)),
                int(m.group(6)) if m.group(6) else 0
            ))
            continue

        m = re.match(r'^PADS:([^:]+):([^:]+):(.+)$', line)
        if m:
            key = m.group(2)
            pads = []
            for pm in re.finditer(r'(\d+)@\(([^,]+),([^)]+)\)', m.group(3)):
                pads.append((int(pm.group(1)), float(pm.group(2)), float(pm.group(3))))
            pad_defs[key] = pads
            continue

        m = re.match(r'^NET:([^:]+):(.+)$', line)
        if m:
            pad_refs = []
            for p in m.group(2).split(','):
                p = p.strip()
                ref, pin = p.rsplit('.', 1)
                pad_refs.append((ref, int(pin)))
            nets.append((m.group(1), pad_refs))

    # Compute absolute pad positions
    pad_pos = {}  # "ref.pin" -> (x, y)
    for ref, lib, fp_name, fx, fy, rot in footprints:
        for pin, dx, dy in pad_defs.get(fp_name, []):
            rx, ry = rotate_pad(dx, dy, rot)
            pad_pos[f"{ref}.{pin}"] = (round(fx + rx, 4), round(fy + ry, 4))

    return pad_pos, nets


# ============ TCP CLIENT ============

class ApiClient:
    def __init__(self, host, port):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((host, port))
        self.sock.settimeout(30)
        self.buf = ""

    def cmd(self, command):
        self.sock.sendall((command + '\n').encode())
        # Read until newline
        while '\n' not in self.buf:
            data = self.sock.recv(8192).decode()
            if not data:
                break
            self.buf += data
        if '\n' in self.buf:
            line, self.buf = self.buf.split('\n', 1)
            return line.strip()
        return self.buf.strip()

    def close(self):
        self.sock.close()


# ============ ROUTING ============

def route_net(client, net_name, pads, pad_pos, layer='auto'):
    """Route a net by chaining pad-to-pad segments."""
    # Get positions for all pads
    positions = []
    for ref, pin in pads:
        key = f"{ref}.{pin}"
        if key in pad_pos:
            positions.append((key, pad_pos[key]))
        else:
            print(f"  WARNING: no position for {key}")

    if len(positions) < 2:
        return True  # single-pad net, nothing to route

    # Chain: route p0->p1, p1->p2, etc.
    all_ok = True
    for i in range(len(positions) - 1):
        name_a, (x1, y1) = positions[i]
        name_b, (x2, y2) = positions[i + 1]
        cmd = f"route {net_name} {x1},{y1} {x2},{y2} {layer}"
        resp = client.cmd(cmd)
        ok = resp.startswith('OK')
        status = "OK" if ok else "FAIL"
        print(f"  {name_a} -> {name_b}: {resp}")
        if not ok:
            all_ok = False
    return all_ok


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else '172.17.0.1'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9876

    # Parse board file for pad positions
    board_path = '/workspace/demo_2_7seg/test2.dpcb'
    print(f"Parsing {board_path}...")
    pad_pos, nets = parse_board(board_path)
    print(f"  {len(pad_pos)} pads, {len(nets)} nets")

    # Connect
    print(f"\nConnecting to {host}:{port}...")
    client = ApiClient(host, port)
    print(f"  Status: {client.cmd('status')}")

    # Classify nets
    local_nets = []      # 2-pad short nets (ILIM, VM)
    signal_nets = []     # SR outputs, data, clk, etc.
    power_nets = []      # VCC, GND
    single_nets = []     # single-pad (nothing to route)

    net_dict = {name: pads for name, pads in nets}

    for name, pads in nets:
        if len(pads) < 2:
            single_nets.append(name)
        elif name in ('VCC', 'GND'):
            power_nets.append(name)
        elif name.startswith('Net-('):
            local_nets.append(name)
        else:
            signal_nets.append(name)

    print(f"\nRouting plan:")
    print(f"  Local (short):  {len(local_nets)} nets")
    print(f"  Signal:         {len(signal_nets)} nets")
    print(f"  Power:          {len(power_nets)} nets")
    print(f"  Single-pad:     {len(single_nets)} (skip)")

    # 1. Local nets first
    print(f"\n{'='*50}")
    print(f"PHASE 1: Local nets")
    print(f"{'='*50}")
    for name in local_nets:
        print(f"\n[{name}]")
        route_net(client, name, net_dict[name], pad_pos)

    # 2. Signal nets
    print(f"\n{'='*50}")
    print(f"PHASE 2: Signal nets")
    print(f"{'='*50}")
    for name in signal_nets:
        print(f"\n[{name}]")
        route_net(client, name, net_dict[name], pad_pos)

    # 3. Power nets
    print(f"\n{'='*50}")
    print(f"PHASE 3: Power nets")
    print(f"{'='*50}")
    for name in power_nets:
        print(f"\n[{name}]")
        route_net(client, name, net_dict[name], pad_pos)

    # Final status
    print(f"\n{'='*50}")
    print(f"DONE")
    print(f"{'='*50}")
    print(client.cmd('status'))

    # Save
    save_path = '/workspace/demo_2_7seg/test2_autorouted.dpcb'
    print(f"\nSaving to {save_path}...")
    print(client.cmd(f'save {save_path}'))

    client.close()


if __name__ == '__main__':
    main()
