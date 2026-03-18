import json

keep = [
    "Timer:NE555",
    "Device:R",
    "Device:C",
    "power:GND",
    "power:VCC",
]

atlas = json.load(open("kicad_pin_atlas.json"))
subset = {k: v for k, v in atlas.items() if k in keep}
print(f"Found {len(subset)}/{len(keep)} symbols")
json.dump(subset, open("pin_atlas_555_osc.json", "w"), indent=1)