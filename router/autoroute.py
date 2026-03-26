#!/usr/bin/env python3
"""
autoroute.py — Route all multi-pad nets in one pass.

Reads pad positions from the route server, builds a routing plan
(shortest nets first), routes them all, reports results.

Usage:
    python3 autoroute.py                  # route all unrouted nets
    python3 autoroute.py --all            # unroute everything first, then route all
    python3 autoroute.py --order short    # shortest nets first (default)
    python3 autoroute.py --order long     # longest nets first
    python3 autoroute.py --layer auto     # layer mode (default: auto)
    python3 autoroute.py --margin 3       # routing margin (default: 3)

Environment:
    ROUTE_HOST  — server host (default: 172.17.0.1)
    ROUTE_PORT  — agent port (default: 8084)
"""

import json
import math
import os
import sys
import time
import urllib.request

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


def get_net_plan(state):
    """Build routing plan from board state.

    Returns list of nets, each with pad positions and nearest-neighbour chain.
    Only multi-pad nets included.
    """
    pads = state['pads']
    tracks = state['tracks']

    net_pads = {}
    for p in pads:
        if not p['net']:
            continue
        net_pads.setdefault(p['net'], []).append(p)

    routed_nets = {t['net'] for t in tracks}

    plan = []
    for net, pad_list in net_pads.items():
        if len(pad_list) < 2:
            continue

        # Nearest-neighbour chain
        total_dist = 0
        remaining = list(pad_list)
        current = remaining.pop(0)
        chain = [current]
        while remaining:
            best_d = float('inf')
            best_i = 0
            for i, p in enumerate(remaining):
                d = math.hypot(p['x'] - current['x'], p['y'] - current['y'])
                if d < best_d:
                    best_d = d
                    best_i = i
            total_dist += best_d
            current = remaining.pop(best_i)
            chain.append(current)

        plan.append({
            'net': net,
            'pads': pad_list,
            'chain': chain,
            'est_length': total_dist,
            'routed': net in routed_nets,
        })

    return plan


def _find_furthest_pair(pads):
    """Find the two pads with the greatest distance — the spine endpoints."""
    best_d = -1
    best_i, best_j = 0, 1
    for i in range(len(pads)):
        for j in range(i + 1, len(pads)):
            d = math.hypot(pads[i]['x'] - pads[j]['x'],
                           pads[i]['y'] - pads[j]['y'])
            if d > best_d:
                best_d = d
                best_i, best_j = i, j
    return best_i, best_j


def route_net(net_info, layer, margin, use8=False):
    """Route a single net: spine between furthest pads, then tap the rest.

    Returns list of route results.
    """
    pads = net_info['pads']
    net = net_info['net']
    results = []

    base_cmd = {'action': 'route', 'net': net, 'layer': layer, 'margin': margin}
    if use8:
        base_cmd['use8'] = True

    if len(pads) == 2:
        r = api_post({**base_cmd,
            'from': [pads[0]['x'], pads[0]['y']],
            'to': [pads[1]['x'], pads[1]['y']],
        })
        results.append(r)
        return results

    # 3+ pads: route spine (furthest pair), then tap remaining
    si, sj = _find_furthest_pair(pads)
    spine_a = pads[si]
    spine_b = pads[sj]

    r = api_post({**base_cmd,
        'from': [spine_a['x'], spine_a['y']],
        'to': [spine_b['x'], spine_b['y']],
    })
    results.append(r)

    # Tap remaining pads into the spine
    tap_cmd = {'action': 'route_tap', 'net': net, 'layer': layer, 'margin': margin}
    for i, p in enumerate(pads):
        if i == si or i == sj:
            continue
        r = api_post({**tap_cmd, 'from': [p['x'], p['y']]})
        if not r.get('ok'):
            da = math.hypot(p['x'] - spine_a['x'], p['y'] - spine_a['y'])
            db = math.hypot(p['x'] - spine_b['x'], p['y'] - spine_b['y'])
            target = spine_a if da < db else spine_b
            r = api_post({**base_cmd,
                'from': [p['x'], p['y']],
                'to': [target['x'], target['y']],
            })
        results.append(r)

    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Autoroute all nets')
    parser.add_argument('--all', action='store_true', help='Unroute everything first')
    parser.add_argument('--order', default='short', choices=['short', 'long'],
                        help='Routing order (default: short)')
    parser.add_argument('--layer', default='auto', help='Layer mode (default: auto)')
    parser.add_argument('--margin', type=int, default=3, help='Routing margin (default: 3)')
    parser.add_argument('--use8', action='store_true', help='Use 8-direction (diagonal) routing')
    args = parser.parse_args()

    state = api_get('/')
    plan = get_net_plan(state)

    if args.order == 'short':
        plan.sort(key=lambda n: n['est_length'])
    else:
        plan.sort(key=lambda n: -n['est_length'])

    if args.all:
        for net_info in plan:
            api_post({'action': 'unroute', 'net': net_info['net']})
        to_route = plan
    else:
        to_route = [n for n in plan if not n['routed']]

    if not to_route:
        print('Nothing to route.')
        return

    print(f'Routing {len(to_route)} net(s), order={args.order}, '
          f'layer={args.layer}, margin={args.margin}')
    print()

    t0 = time.perf_counter()
    successes = 0
    failures = 0
    total_length = 0
    total_vias = 0
    total_segs = 0
    failed_nets = []

    for net_info in to_route:
        net = net_info['net']
        n_pads = len(net_info['pads'])

        results = route_net(net_info, args.layer, args.margin, args.use8)

        all_ok = all(r.get('ok') for r in results)
        net_length = sum(r.get('length', 0) for r in results)
        net_vias = sum(r.get('vias', 0) for r in results)
        net_segs = sum(r.get('segments', 0) for r in results)

        if all_ok:
            successes += 1
            total_length += net_length
            total_vias += net_vias
            total_segs += net_segs
            print(f'  OK   {net:15s}  {n_pads}p  {net_length:6.1f}mm  {net_segs}seg  {net_vias}via')
        else:
            failures += 1
            failed_nets.append(net)
            ok_count = sum(1 for r in results if r.get('ok'))
            print(f'  FAIL {net:15s}  {n_pads}p  ({ok_count}/{len(results)} legs)')
            for r in results:
                if not r.get('ok'):
                    print(f'         {r.get("message", "?")}')

    elapsed = time.perf_counter() - t0

    print()
    print(f'Done in {elapsed*1000:.0f}ms')
    print(f'  Routed: {successes}/{successes + failures}  '
          f'Length: {total_length:.1f}mm  Segs: {total_segs}  Vias: {total_vias}')
    if failed_nets:
        print(f'  Failed: {", ".join(failed_nets)}')


if __name__ == '__main__':
    main()
