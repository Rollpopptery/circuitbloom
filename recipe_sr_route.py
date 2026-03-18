#!/usr/bin/env python3
"""
recipe_sr_route.py — Generate dpcb track/via lines for a B.Cu SR signal route.

Prints lines to stdout. Does NOT modify any file.
Agent reviews output and pastes into the dpcb.

Pattern:
  F.Cu stub:       src → (via1_x, src_y)
  VIA:             (via1_x, src_y)
  B.Cu horizontal: (via1_x, src_y) → (channel_x, src_y)
  B.Cu vertical:   (channel_x, src_y) → (channel_x, approach_y)
  VIA:             (channel_x, approach_y)
  F.Cu horizontal: (channel_x, approach_y) → (dst_x, approach_y)
  F.Cu stub:       (dst_x, approach_y) → dst

Usage:
  python3 utilities/recipe_sr_route.py \\
      --src 21.525,12.635 \\
      --dst 14.675,37.862 \\
      --net /sr1_qp1 \\
      --channel-x 12.0 \\
      --via1-x 18.0 \\
      --approach-y 39.5
"""

import argparse

def fmt(v):
    return f"{v:.3f}".rstrip('0').rstrip('.')

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--src',        required=True, help='Source pad x,y')
    p.add_argument('--dst',        required=True, help='Destination pad x,y')
    p.add_argument('--net',        required=True, help='Net name e.g. /sr1_qp1')
    p.add_argument('--channel-x',  required=True, type=float, help='B.Cu vertical channel x')
    p.add_argument('--via1-x',     required=True, type=float, help='First via x (F.Cu stub end)')
    p.add_argument('--approach-y', required=True, type=float, help='F.Cu approach horizontal y')
    p.add_argument('--width',      default=0.25,  type=float, help='Track width (default 0.25)')
    p.add_argument('--via-drill',  default=0.6,   type=float, help='Via drill (default 0.6)')
    p.add_argument('--via-ann',    default=0.3,   type=float, help='Via annular (default 0.3)')
    args = p.parse_args()

    sx, sy   = (float(v) for v in args.src.split(','))
    dx, dy   = (float(v) for v in args.dst.split(','))
    net      = args.net
    ch_x     = args.channel_x
    v1_x     = args.via1_x
    app_y    = args.approach_y
    w        = args.width
    vd       = args.via_drill
    va       = args.via_ann

    via_spec = f"{vd}/{va}"

    lines = [
        f"TRK:({fmt(sx)},{fmt(sy)})->({fmt(v1_x)},{fmt(sy)}):{w}:F.Cu:{net}",
        f"VIA:({fmt(v1_x)},{fmt(sy)}):{via_spec}:{net}",
        f"TRK:({fmt(v1_x)},{fmt(sy)})->({fmt(ch_x)},{fmt(sy)}):{w}:B.Cu:{net}",
        f"TRK:({fmt(ch_x)},{fmt(sy)})->({fmt(ch_x)},{fmt(app_y)}):{w}:B.Cu:{net}",
        f"VIA:({fmt(ch_x)},{fmt(app_y)}):{via_spec}:{net}",
        f"TRK:({fmt(ch_x)},{fmt(app_y)})->({fmt(dx)},{fmt(app_y)}):{w}:F.Cu:{net}",
        f"TRK:({fmt(dx)},{fmt(app_y)})->({fmt(dx)},{fmt(dy)}):{w}:F.Cu:{net}",
    ]

    for line in lines:
        print(line)

if __name__ == '__main__':
    main()
