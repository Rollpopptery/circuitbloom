import json
import urllib.request
import time
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from get_unrouted import get_unrouted, get_route_order

unrouted  = get_unrouted()
nets_data = json.loads(urllib.request.urlopen('http://localhost:8085/nets').read())
pairs     = get_route_order(unrouted, nets_data)

print(f'Attempting {len(pairs)} unrouted connections...')

ok_count = fail_count = 0
t0 = time.perf_counter()
for net, from_pad, to_pad, obs in pairs:
    url = f'http://localhost:8085/place?from={from_pad}&to={to_pad}&net={net}&n=5000'
    r = json.loads(urllib.request.urlopen(url).read())
    if r.get('ok'):
        ok_count += 1
        board = r.get('board', '?')
        scale = r.get('scale', 0)
        print(f'OK   {net:20s} {from_pad:15s} -> {to_pad:15s} [{board}] scale={scale:.3f}')
    else:
        fail_count += 1
        print(f'FAIL {net:20s} {from_pad:15s} -> {to_pad}')
elapsed = time.perf_counter() - t0
print(f'\nRouted: {ok_count}  Failed: {fail_count}  Time: {elapsed:.1f}s')