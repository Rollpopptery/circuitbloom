#!/usr/bin/env python3
"""Highlight each net in turn with a delay between them."""

import json
import time
import urllib.request

SERVER = "http://localhost:8084"


def highlight(net, color="#ffff00"):
    data = json.dumps({"action": "highlight", "net": net, "color": color}).encode()
    req = urllib.request.Request(SERVER + "/", data=data,
                                headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req)


def get_nets():
    with urllib.request.urlopen(SERVER + "/nets") as r:
        return list(json.loads(r.read()).keys())


if __name__ == "__main__":
    nets = get_nets()
    print(f"{len(nets)} nets")
    for net in nets:
        print(f"  {net}")
        highlight(net)
        time.sleep(0.2)
    highlight("")  # clear
    print("done")
