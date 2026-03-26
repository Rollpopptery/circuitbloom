#!/usr/bin/env python3
"""Send a command to the route server API with automatic diagnostic flash.

After any state-changing command (route, route_tap, unroute, save),
automatically runs diagnostics and prints changes since the last command.

Usage:
    python3 cmd_route.py status
    python3 cmd_route.py nets
    python3 cmd_route.py pads VCC_3V3
    python3 cmd_route.py route VCC_3V3 10.55,8.60 19.73,8.55 auto margin=3
    python3 cmd_route.py route_tap GND 11.63,13.65 margin=3
    python3 cmd_route.py unroute VCC_3V3
    python3 cmd_route.py save
    python3 cmd_route.py reload

Environment variables:
    ROUTE_HOST  — server host (default: localhost)
    ROUTE_PORT  — agent port (default: 8084)
    ROUTE_FLASH — set to 0 to disable diagnostic flash (default: 1)
"""
import json, os, sys, urllib.request

HOST = os.environ.get('ROUTE_HOST', '172.17.0.1')
PORT = int(os.environ.get('ROUTE_PORT', '8084'))
BASE = f'http://{HOST}:{PORT}'


def api_get(path):
    with urllib.request.urlopen(f'{BASE}{path}', timeout=10) as resp:
        return json.loads(resp.read())


def api_post(data):
    req = urllib.request.Request(
        BASE, data=json.dumps(data).encode(),
        headers={'Content-Type': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())




def parse_route_args(args):
    """Parse: <net> <x1,y1> <x2,y2> [layer] [margin=N]"""
    net = args[0]
    x1, y1 = [float(v) for v in args[1].split(',')]
    x2, y2 = [float(v) for v in args[2].split(',')]
    layer = 'auto'
    margin = 3
    for a in args[3:]:
        if a in ('F.Cu', 'B.Cu', 'auto'):
            layer = a
        elif a.startswith('margin='):
            margin = int(a.split('=')[1])
    return net, x1, y1, x2, y2, layer, margin


def parse_tap_args(args):
    """Parse: <net> <x,y> [layer] [margin=N]"""
    net = args[0]
    x, y = [float(v) for v in args[1].split(',')]
    layer = 'auto'
    margin = 3
    for a in args[2:]:
        if a in ('F.Cu', 'B.Cu', 'auto'):
            layer = a
        elif a.startswith('margin='):
            margin = int(a.split('=')[1])
    return net, x, y, layer, margin


# ============================================================
# MAIN
# ============================================================

if len(sys.argv) < 2:
    print(__doc__.strip())
    sys.exit(1)

cmd_verb = sys.argv[1].lower()
args = sys.argv[2:]

# Dispatch command
if cmd_verb == 'status':
    s = api_get('/status')
    print(f"OK: {s['grid_size']} pads={s['pads']} nets={s['nets']} "
          f"trk={s['tracks']} via={s['vias']} "
          f"F.Cu={s['pct_fcu']} B.Cu={s['pct_bcu']}")

elif cmd_verb == 'nets':
    state = api_get('/')
    net_pads = {}
    for p in state['pads']:
        if p['net']:
            net_pads.setdefault(p['net'], []).append(f"{p['ref']}.{p['pin']}")
    net_tracks = {}
    for t in state['tracks']:
        net_tracks[t['net']] = net_tracks.get(t['net'], 0) + 1
    for net in sorted(net_pads):
        plist = net_pads[net]
        trk = net_tracks.get(net, 0)
        tag = f'{trk}trk' if trk else 'unrouted'
        print(f"  {net:15s}  {len(plist):2d} pads  [{tag}]  {', '.join(plist)}")

elif cmd_verb == 'pads':
    if not args:
        print('Usage: pads <net>')
        sys.exit(1)
    pads = api_get(f'/pads/{args[0]}')
    if not pads:
        print(f'No pads for net {args[0]}')
    else:
        print(f'OK: {args[0]} pads:')
        for p in pads:
            typ = 'SMD' if p['smd'] else 'TH'
            print(f"  {p['ref']}.{p['pin']} ({p['name']}) @ ({p['x']:.3f},{p['y']:.3f}) {typ}")

elif cmd_verb == 'route':
    if len(args) < 3:
        print('Usage: route <net> <x1,y1> <x2,y2> [layer] [margin=N]')
        sys.exit(1)
    net, x1, y1, x2, y2, layer, margin = parse_route_args(args)
    result = api_post({
        'action': 'route', 'net': net,
        'from': [x1, y1], 'to': [x2, y2],
        'layer': layer, 'margin': margin
    })
    ok = 'OK' if result.get('ok') else 'FAIL'
    print(f'{ok}: {result.get("message", "")}')

elif cmd_verb == 'route_tap':
    if len(args) < 2:
        print('Usage: route_tap <net> <x,y> [layer] [margin=N]')
        sys.exit(1)
    net, x, y, layer, margin = parse_tap_args(args)
    result = api_post({
        'action': 'route_tap', 'net': net,
        'from': [x, y], 'layer': layer, 'margin': margin
    })
    ok = 'OK' if result.get('ok') else 'FAIL'
    msg = result.get('message', '')
    if result.get('tap_point'):
        tp = result['tap_point']
        msg += f'  tapped at ({tp[0]:.1f},{tp[1]:.1f})'
    print(f'{ok}: {msg}')

elif cmd_verb == 'unroute':
    if not args:
        print('Usage: unroute <net>')
        sys.exit(1)
    result = api_post({'action': 'unroute', 'net': args[0]})
    print(f"OK: unrouted {result.get('removed', 0)} segment(s) for {args[0]}")

elif cmd_verb == 'save':
    result = api_post({'action': 'save'})
    ok = 'OK' if result.get('ok') else 'FAIL'
    print(f"{ok}: {result.get('message', '')}")

elif cmd_verb == 'reload':
    result = api_post({'action': 'reload'})
    print(f"OK: reloaded (v{result.get('v', '?')})")

elif cmd_verb == 'get_transitions':
    result = api_get('/get_transitions')
    total = result.get('total', 0)
    missing = result.get('missing', 0)
    print(f'OK: {total} transition(s), {missing} missing via(s)')
    for t in result.get('transitions', []):
        print(f"  {t['status']:7s} ({t['x']:.1f},{t['y']:.1f}) {t['net']}")

elif cmd_verb == 'get_vias':
    vias = api_get('/get_vias')
    print(f'OK: {len(vias)} via(s)')
    for v in vias:
        print(f"  ({v['x']:.1f},{v['y']:.1f}) {v['net']}")

else:
    print(f'Unknown command: {cmd_verb}')
    sys.exit(1)

