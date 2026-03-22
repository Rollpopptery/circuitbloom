#!/usr/bin/env python3
"""Send component placement commands to the dpcb viewer API.

Specialised for placement work. After each move, runs placement-relevant
diagnostics: pad crowding and ratsnest blockages.

Usage:
    python3 utilities/cmd_component.py move C1 15.0,10.0
    python3 utilities/cmd_component.py move U2 20.0,9.0 r90
    python3 utilities/cmd_component.py check_crowding_pads
    python3 utilities/cmd_component.py check_ratsnest
    python3 utilities/cmd_component.py force U2
    python3 utilities/cmd_component.py repulsion U2
    python3 utilities/cmd_component.py pads +5V
    python3 utilities/cmd_component.py status

Environment variables:
    DPCB_HOST  — viewer host (default: 172.17.0.1, Docker gateway)
    DPCB_PORT  — viewer port (default: 9876)
    DPCB_FLASH — set to 0 to disable diagnostic flash (default: 1)
"""
import json, os, socket, sys

HOST = os.environ.get('DPCB_HOST', '172.17.0.1')
PORT = int(os.environ.get('DPCB_PORT', '9876'))
FLASH = os.environ.get('DPCB_FLASH', '1') != '0'
FLASH_STATE = '/tmp/dpcb_component_flash_state.json'

STATE_CHANGING = {'move', 'load', 'save'}

def send_cmd(s, cmd):
    s.sendall((cmd + '\n').encode())
    buf = ''
    while True:
        buf += s.recv(4096).decode()
        if '\n.\n' in buf:
            return buf[:buf.index('\n.\n')]

def load_prev_state():
    try:
        with open(FLASH_STATE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_state(state):
    with open(FLASH_STATE, 'w') as f:
        json.dump(state, f)

def diff_set(label, cur_set, prev_set):
    """Print diff for a set of diagnostic lines."""
    new = cur_set - prev_set
    resolved = prev_set - cur_set
    n = len(cur_set)
    print(f'{label}: {n}', end='')
    if new:
        print(f'  NEW({len(new)}):')
        for item in sorted(new):
            print(f'  + {item}')
    if resolved:
        print(f'  FIXED({len(resolved)}):')
        for item in sorted(resolved):
            print(f'  - {item}')
    if not new and not resolved:
        print('  (no change)')

def run_flash(s):
    """Run placement-relevant diagnostics after a move."""
    prev = load_prev_state()
    cur = {}

    # Status
    status = send_cmd(s, 'status')
    print(f'\n--- PLACEMENT: {status.replace("OK: ", "")}')

    # Pad crowding — physical overlap between components
    padcrowd = send_cmd(s, 'check_crowding_pads 1.5')
    cur_padcrowd = {l.strip() for l in padcrowd.strip().split('\n') if 'CROWDED' in l}
    cur['pad_crowded'] = list(cur_padcrowd)
    diff_set('PAD CROWDING', cur_padcrowd, set(prev.get('pad_crowded', [])))

    # Ratsnest blockages — components sitting in routing corridors
    ratsnest = send_cmd(s, 'check_ratsnest 2.0')
    cur_blocked = {l.strip() for l in ratsnest.strip().split('\n') if 'BLOCKED' in l}
    cur['ratsnest_blocked'] = list(cur_blocked)
    diff_set('RATSNEST BLOCKED', cur_blocked, set(prev.get('ratsnest_blocked', [])))

    # Force — attraction toward connected pads
    force_out = send_cmd(s, 'force')
    force_lines = [l.strip() for l in force_out.strip().split('\n')
                   if l.strip().startswith(('C','D','J','R','S','U'))]
    print(f'FORCE:')
    for line in force_lines:
        print(f'  {line}')

    # Repulsion — push from foreign ratsnest lines
    repul_out = send_cmd(s, 'repulsion')
    repul_lines = [l.strip() for l in repul_out.strip().split('\n')
                   if l.strip().startswith(('C','D','J','R','S','U'))]
    print(f'REPULSION:')
    for line in repul_lines:
        print(f'  {line}')

    # Component repulsion — physical spacing pressure from all neighbours
    comp_repul_out = send_cmd(s, 'component_repulsion')
    comp_repul_lines = [l.strip() for l in comp_repul_out.strip().split('\n')
                        if l.strip().startswith(('C','D','J','R','S','U'))]
    print(f'COMPONENT REPULSION:')
    for line in comp_repul_lines:
        print(f'  {line}')

    print('---')
    save_state(cur)

if len(sys.argv) < 2:
    print(__doc__.strip())
    sys.exit(1)

cmd = ' '.join(sys.argv[1:])
cmd_verb = cmd.split()[0].lower()

s = socket.create_connection((HOST, PORT), timeout=10)
result = send_cmd(s, cmd)
print(result)

if FLASH and cmd_verb in STATE_CHANGING:
    if cmd_verb == 'load':
        import time; time.sleep(0.5)
    try:
        run_flash(s)
    except Exception as e:
        print(f'\n--- FLASH ERROR: {e} ---')

s.close()
