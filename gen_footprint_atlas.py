#!/usr/bin/env python3
"""
gen_footprint_atlas.py - Generate a footprint atlas from KiCad footprint libraries.

Scans KiCad footprint library directories (.pretty folders containing .kicad_mod files)
and builds a JSON index of all available footprints with pad count.

Output format: { "Library:Footprint": {"pads": N}, ... }

This allows validation that a footprint exists and has the expected pad count
before assigning it in component_mapping.json.

Usage:
    python gen_footprint_atlas.py                                  # Auto-detect KiCad libs
    python gen_footprint_atlas.py /usr/share/kicad/footprints/     # Scan directory
    python gen_footprint_atlas.py -o fp_atlas.json                 # Custom output
"""

import sys
import os
import json
import glob
import re
from datetime import datetime


def count_pads(filepath):
    """Count pads in a .kicad_mod file without full S-expression parsing.
    
    Counts top-level (pad ...) entries. This is fast and sufficient -
    we don't need to parse the full tree just to count pads.
    """
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except Exception:
        return 0

    # Count occurrences of (pad at the start of an S-expression
    # Pattern: opening paren, "pad", whitespace - must be a top-level-ish pad def
    count = len(re.findall(r'\(\s*pad\s+', content))
    return count


def scan_pretty_dir(pretty_path):
    """Scan a single .pretty directory, return {lib:footprint: {pads: N}}."""
    entries = {}
    lib_name = os.path.basename(pretty_path).replace('.pretty', '')

    mod_files = sorted(glob.glob(os.path.join(pretty_path, '*.kicad_mod')))
    for mod_file in mod_files:
        fp_name = os.path.splitext(os.path.basename(mod_file))[0]
        full_id = f"{lib_name}:{fp_name}"
        pads = count_pads(mod_file)
        entries[full_id] = {"pads": pads}

    return entries


def find_default_fp_paths():
    """Find KiCad footprint library paths on the system."""
    candidates = [
        '/usr/share/kicad/footprints',
        '/usr/local/share/kicad/footprints',
        os.path.expanduser('~/.local/share/kicad/footprints'),
        '/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints',
        'C:/Program Files/KiCad/share/kicad/footprints',
        'C:/Program Files/KiCad/9.0/share/kicad/footprints',
    ]
    for ver in ['8', '9', '10']:
        env_dir = os.environ.get(f'KICAD{ver}_FOOTPRINT_DIR')
        if env_dir:
            candidates.insert(0, env_dir)
    env_dir = os.environ.get('KICAD_FOOTPRINT_DIR')
    if env_dir:
        candidates.insert(0, env_dir)
    return [p for p in candidates if os.path.isdir(p)]


def generate_atlas(sources, output_path='kicad_footprint_atlas.json'):
    """Main generation function."""
    atlas = {}
    lib_paths_used = []
    dir_count = 0

    for source in sources:
        if os.path.isdir(source):
            # Check if this is a .pretty dir itself or a parent of .pretty dirs
            if source.endswith('.pretty'):
                pretty_dirs = [source]
            else:
                pretty_dirs = sorted(glob.glob(os.path.join(source, '*.pretty')))
                if not pretty_dirs:
                    # Maybe it's a flat dir of .pretty folders nested deeper
                    pretty_dirs = sorted(glob.glob(os.path.join(source, '**', '*.pretty'), recursive=True))

            if not pretty_dirs:
                print(f"  Warning: No .pretty directories in {source}")
                continue

            lib_paths_used.append(source)
            print(f"  Scanning: {source} ({len(pretty_dirs)} libraries)")

            for pd in pretty_dirs:
                lib_name = os.path.basename(pd).replace('.pretty', '')
                try:
                    entries = scan_pretty_dir(pd)
                    atlas.update(entries)
                    dir_count += 1
                    if entries:
                        print(f"    {lib_name}: {len(entries)} footprints")
                except Exception as e:
                    print(f"    {lib_name}: ERROR - {e}")
        else:
            print(f"  Skipping: {source}")

    if not atlas:
        print("\nNo footprints found.")
        return None

    output = {
        '_meta': {
            'generated_by': 'gen_footprint_atlas.py',
            'format': '{lib:footprint: {pads: N}}',
            'date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'sources': lib_paths_used,
            'libraries_processed': dir_count,
            'footprint_count': len(atlas),
        },
    }
    for key in sorted(atlas.keys()):
        output[key] = atlas[key]

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, separators=(',', ':'), ensure_ascii=False)

    size = os.path.getsize(output_path)
    print(f"\nAtlas: {output_path}")
    print(f"  Footprints: {len(atlas)}")
    print(f"  Size: {size:,} bytes ({size/1024/1024:.1f} MB)")

    return output_path


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description='Generate KiCad footprint atlas for AI schematic generation.')
    parser.add_argument('sources', nargs='*',
                        help='Footprint directories or .pretty folders')
    parser.add_argument('-o', '--output', default='kicad_footprint_atlas.json',
                        help='Output path (default: kicad_footprint_atlas.json)')
    args = parser.parse_args()

    sources = list(args.sources)
    if not sources:
        print("Searching for KiCad footprint libraries...")
        sources = find_default_fp_paths()
        if not sources:
            print("No KiCad footprint libraries found. Specify paths manually.")
            sys.exit(1)
        print(f"Found: {', '.join(sources)}")

    print(f"\nGenerating footprint atlas...")
    if not generate_atlas(sources, args.output):
        sys.exit(1)


if __name__ == '__main__':
    main()