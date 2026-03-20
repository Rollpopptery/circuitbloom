#!/usr/bin/env python3
"""Send a command to the dpcb viewer API with automatic diagnostic flash.

After any state-changing command (route, unroute, via, move, load, save,
unroute_seg, route_tap, pushout, clearkeepouts), automatically runs
diagnostic commands and prints a compact board snapshot.

Usage:
    python3 utilities/cmd.py <command>
    python3 utilities/cmd.py status
    python3 utilities/cmd.py route +5V 9.85,4.05 9.5,11.75 auto margin=3

Environment variables:
    DPCB_HOST  — viewer host (default: 172.17.0.1, Docker gateway)
    DPCB_PORT  — viewer port (default: 9876)
    DPCB_FLASH — set to 0 to disable diagnostic flash (default: 1)
"""
import json, os, socket, sys

HOST = os.environ.get('DPCB_HOST', '172.17.0.1')
PORT = int(os.environ.get('DPCB_PORT', '9876'))
FLASH = os.environ.get('DPCB_FLASH', '1') != '0'
FLASH_STATE = '/tmp/dpcb_flash_state.json'

STATE_CHANGING = {
    'route', 'unroute', 'unroute_seg', 'route_tap',
    'via', 'move', 'load', 'save',
    'pushout', 'clearkeepouts', 'keepouts',
}

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

DESTRUCTIVE = {'unroute', 'unroute_seg'}

def diff_set(label, cur_set, prev_set):
    """Print diff for a set of diagnostic lines. Returns lines printed."""
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

def run_flash(s, lite=False):
    """Run diagnostics, print only changes + compact summary.
    lite=True: only viacheck + crowding checks (for unroute commands).
    lite=False: full diagnostics (for constructive commands).
    """
    prev = load_prev_state()
    cur = {}

    # Status — always show (one line, cheap)
    status = send_cmd(s, 'status')
    print(f'\n--- FLASH{"(lite)" if lite else ""}: {status.replace("OK: ", "")}')

    # Vias — always
    vias = send_cmd(s, 'viacheck')
    cur_fails = {l.strip() for l in vias.strip().split('\n') if 'FAIL' in l}
    cur['via_fails'] = list(cur_fails)
    diff_set('VIAS fail', cur_fails, set(prev.get('via_fails', [])))

    # Crowding — always
    crowding = send_cmd(s, 'check_crowding 1.0')
    cur_crowded = {l.strip() for l in crowding.strip().split('\n') if 'CROWDED' in l}
    cur['crowded'] = list(cur_crowded)
    diff_set('CROWDING', cur_crowded, set(prev.get('crowded', [])))

    # Pad crowding — always
    padcrowd = send_cmd(s, 'check_crowding_pads 1.5')
    cur_padcrowd = {l.strip() for l in padcrowd.strip().split('\n') if 'CROWDED' in l}
    cur['pad_crowded'] = list(cur_padcrowd)
    diff_set('PAD CROWDING', cur_padcrowd, set(prev.get('pad_crowded', [])))

    if not lite:
        # Transitions — only on constructive commands
        trans = send_cmd(s, 'get_transitions')
        missing = [l.strip() for l in trans.strip().split('\n') if 'MISSING' in l]
        prev_missing = prev.get('missing_vias', [])
        if missing != prev_missing:
            print(f'MISSING VIAS: {len(missing)}')
            for m in missing:
                print(f'  {m}')
        else:
            print(f'MISSING VIAS: {len(missing)}  (no change)')
        cur['missing_vias'] = missing
    else:
        cur['missing_vias'] = prev.get('missing_vias', [])

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
        run_flash(s, lite=(cmd_verb in DESTRUCTIVE))
    except Exception as e:
        print(f'\n--- FLASH ERROR: {e} ---')

s.close()
