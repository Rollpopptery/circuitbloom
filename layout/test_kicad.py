"""
test_kicad.py — Test KiCad IPC API connection and move U2

Requirements:
  pip install kicad-python
  KiCad 9+ running with API enabled
  /tmp/kicad/ owned by your user (not root)
"""

import kipy
from kipy.common_types import Vector2


def main():
    print("Connecting to KiCad...")
    kicad = kipy.KiCad()
    board = kicad.get_board()
    print("Connected.")

    # List all footprints (positions in nanometres, display in mm)
    print("\n--- Footprints ---")
    footprints = board.get_footprints()
    for fp in footprints:
        ref = fp.reference_field.text.value
        pos = fp.position
        print(f"  {ref:6s}  ({pos.x/1e6:.2f}, {pos.y/1e6:.2f}) mm")

    # Move U2 5mm to the right
    print("\n--- Moving U2 ---")
    for fp in footprints:
        if fp.reference_field.text.value == "U2":
            old = fp.position
            print(f"  Before: ({old.x/1e6:.2f}, {old.y/1e6:.2f}) mm")

            # 5mm = 5,000,000 nanometres
            fp.position = Vector2.from_xy(old.x + 5_000_000, old.y)
            board.update_items([fp])

            print(f"  After:  ({fp.position.x/1e6:.2f}, {fp.position.y/1e6:.2f}) mm")
            print("  U2 moved 5mm right. Check KiCad.")
            break
    else:
        print("  U2 not found!")

    print("\nDone.")


if __name__ == '__main__':
    main()