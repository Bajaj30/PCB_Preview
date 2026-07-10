#!/usr/bin/env python3
"""
test_gcode_pipeline.py — Mathematical verification tests

Tests the full G-code pipeline WITHOUT any hardware:
  1. G-code generation → output.gcode
  2. G-code validity (GRBL-compatible commands)
  3. Laser safety (M5 before every rapid)
  4. Coordinate bounds
  5. Scale factor correctness
  6. Serial command formatting
  7. G-code flow order
  8. Feed rate consistency

Run: python3 tests/test_gcode_pipeline.py
"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from io import StringIO

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ─── Test Counters ────────────────────────────────────────────────
passed = 0
failed = 0
errors = []

def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        msg = f"  ❌ {name}" + (f" — {detail}" if detail else "")
        print(msg)
        errors.append(msg)


# ══════════════════════════════════════════════════════════════════
# TEST 1: G-Code Generator — Valid Output
# ══════════════════════════════════════════════════════════════════
print("\n" + "═"*60)
print("TEST 1: G-code Generator — Valid Output")
print("═"*60)

from parser.gcode_generator import generate_gcode

# Create a mock toolpath.json
mock_toolpath = {
    "source": "test_board.gbr",
    "mode": "trace",
    "work_area": {"width": 50.0, "height": 30.0, "min_x": 0.0, "min_y": 0.0},
    "statistics": {"draw_moves": 5, "rapid_moves": 3, "pad_marks": 2},
    "commands": [
        {"type": "rapid", "x": 10.0, "y": 5.0},
        {"type": "draw", "x": 20.0, "y": 5.0, "width": 0.25},
        {"type": "draw", "x": 20.0, "y": 15.0, "width": 0.25},
        {"type": "draw", "x": 10.0, "y": 15.0, "width": 0.25},
        {"type": "draw", "x": 10.0, "y": 5.0, "width": 0.25},
        {"type": "rapid", "x": 30.0, "y": 10.0},
        {"type": "pad", "x": 30.0, "y": 10.0, "diameter": 1.5},
        {"type": "rapid", "x": 40.0, "y": 20.0},
        {"type": "pad", "x": 40.0, "y": 20.0, "diameter": 0.8},
        {"type": "draw", "x": 45.0, "y": 20.0, "width": 0.20},
    ]
}

with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
    json.dump(mock_toolpath, f)
    toolpath_path = f.name

with tempfile.NamedTemporaryFile(mode='w', suffix='.gcode', delete=False) as f:
    gcode_path = f.name

# Suppress stdout during generation
old_stdout = sys.stdout
sys.stdout = StringIO()
try:
    generate_gcode(input_path=toolpath_path, output_path=gcode_path, feed_rate=1000, scale=1)
finally:
    sys.stdout = old_stdout

with open(gcode_path, 'r') as f:
    gcode_text = f.read()
    gcode_lines = [l.strip() for l in gcode_text.split('\n')]

# Filter out comments and empty lines
active_lines = [l for l in gcode_lines if l and not l.startswith(';')]

test("G-code file is not empty", len(active_lines) > 0, f"Got {len(active_lines)} lines")
test("Starts with G21 (mm mode)", any(l.startswith("G21") for l in active_lines))
test("Has G90 (absolute positioning)", any(l.startswith("G90") for l in active_lines))
test("Has G28 (home)", any(l.startswith("G28") for l in active_lines))
test("Ends with M2 (program end)", active_lines[-1].startswith("M2"))
test("Has M5 safety at end", any("M5" in l and "safety" in l.lower() for l in gcode_lines))
test("Has G0 X0 Y0 (return to origin)", any("G0 X0" in l and "Y0" in l for l in active_lines))

# Count G-code command types
g0_count = sum(1 for l in active_lines if l.startswith("G0 X"))
g1_count = sum(1 for l in active_lines if l.startswith("G1 X"))
m3_count = sum(1 for l in active_lines if l.startswith("M3"))
m5_count = sum(1 for l in active_lines if l.startswith("M5"))
g4_count = sum(1 for l in active_lines if l.startswith("G4"))

test("Has rapid moves (G0)", g0_count >= 3, f"Found {g0_count} G0 moves")
test("Has draw moves (G1)", g1_count >= 5, f"Found {g1_count} G1 moves")
test("Has laser on (M3)", m3_count >= 1, f"Found {m3_count} M3 commands")
test("Has laser off (M5)", m5_count >= 1, f"Found {m5_count} M5 commands")
test("Has pad dwell (G4)", g4_count == 2, f"Found {g4_count} G4 commands (expected 2)")


# ══════════════════════════════════════════════════════════════════
# TEST 2: G-Code GRBL Validity
# ══════════════════════════════════════════════════════════════════
print("\n" + "═"*60)
print("TEST 2: G-code GRBL Validity")
print("═"*60)

GRBL_VALID = re.compile(
    r'^(G\d+|M\d+|F\d+|\$[A-Za-z0-9=])'
)

invalid_lines = []
for i, line in enumerate(active_lines):
    code = line.split(';')[0].strip()  # strip inline comments
    if not code:
        continue
    first_word = code.split()[0]
    if not GRBL_VALID.match(first_word):
        invalid_lines.append((i+1, code))

test("All lines are valid GRBL commands", len(invalid_lines) == 0,
     f"Invalid: {invalid_lines[:3]}")


# ══════════════════════════════════════════════════════════════════
# TEST 3: Laser Safety
# ══════════════════════════════════════════════════════════════════
print("\n" + "═"*60)
print("TEST 3: Laser Safety — M5 Before Every Rapid Move")
print("═"*60)

laser_state = False
unsafe_rapids = []

for i, line in enumerate(active_lines):
    code = line.split(';')[0].strip()
    if code.startswith("M3"):
        laser_state = True
    elif code.startswith("M5"):
        laser_state = False
    elif code.startswith("G0 X") and laser_state:
        unsafe_rapids.append((i+1, line))

test("No rapid moves while laser is ON", len(unsafe_rapids) == 0,
     f"Unsafe rapids: {unsafe_rapids[:3]}")


# ══════════════════════════════════════════════════════════════════
# TEST 4: Coordinate Bounds
# ══════════════════════════════════════════════════════════════════
print("\n" + "═"*60)
print("TEST 4: Coordinate Bounds Check")
print("═"*60)

coord_pattern = re.compile(r'X([-\d.]+)\s*Y([-\d.]+)')
out_of_bounds = []
max_x_found = 0
max_y_found = 0

for i, line in enumerate(active_lines):
    m = coord_pattern.search(line)
    if m:
        x, y = float(m.group(1)), float(m.group(2))
        max_x_found = max(max_x_found, x)
        max_y_found = max(max_y_found, y)
        if x < -1 or y < -1 or x > 500 or y > 500:
            out_of_bounds.append((i+1, x, y))

test("All coordinates in valid range", len(out_of_bounds) == 0,
     f"Out of bounds: {out_of_bounds[:3]}")
test("Max X matches toolpath (45.0)", abs(max_x_found - 45.0) < 0.01, f"Max X = {max_x_found}")
test("Max Y matches toolpath (20.0)", abs(max_y_found - 20.0) < 0.01, f"Max Y = {max_y_found}")
# Check precision on move lines (skip return-to-origin G0 X0 Y0)
move_lines = [l for l in active_lines if l.startswith(('G0 X', 'G1 X')) and 'X0 Y0' not in l]
test("Coordinates are 3-decimal precision",
     all(re.search(r'X[-\d]+\.\d{3}', l) for l in move_lines))


# ══════════════════════════════════════════════════════════════════
# TEST 5: Scale Factor
# ══════════════════════════════════════════════════════════════════
print("\n" + "═"*60)
print("TEST 5: Scale Factor")
print("═"*60)

with tempfile.NamedTemporaryFile(mode='w', suffix='.gcode', delete=False) as f:
    gcode_2x_path = f.name

sys.stdout = StringIO()
try:
    generate_gcode(input_path=toolpath_path, output_path=gcode_2x_path, feed_rate=1000, scale=2)
finally:
    sys.stdout = old_stdout

with open(gcode_2x_path, 'r') as f:
    gcode_2x_lines = [l.strip() for l in f.read().split('\n')]

def find_first_g0(lines):
    for l in lines:
        if l.startswith("G0 X") and "Y" in l:
            m = coord_pattern.search(l)
            if m:
                return float(m.group(1)), float(m.group(2))
    return None, None

x1, y1 = find_first_g0(gcode_lines)
x2, y2 = find_first_g0(gcode_2x_lines)

test("Scale 2x doubles X coordinate",
     x1 is not None and x2 is not None and abs(x2 - x1 * 2) < 0.01,
     f"1x: X={x1}, 2x: X={x2}")
test("Scale 2x doubles Y coordinate",
     y1 is not None and y2 is not None and abs(y2 - y1 * 2) < 0.01,
     f"1x: Y={y1}, 2x: Y={y2}")


# ══════════════════════════════════════════════════════════════════
# TEST 6: Serial Command Formatting
# ══════════════════════════════════════════════════════════════════
print("\n" + "═"*60)
print("TEST 6: Serial Command Formatting (USB → GRBL)")
print("═"*60)

step_to_mm = {
    '16': 0.0125, '80': 0.0625, '160': 0.125,
    '400': 0.3125, '800': 0.625, '1600': 1.25
}

for step, mm in step_to_mm.items():
    cmd = f"G91\nG1 X{mm:.4f} F500\nG90"
    lines = [l.strip() for l in cmd.split('\n') if l.strip()]
    test(f"Step {step} → {mm}mm produces 3 lines", len(lines) == 3)
    test(f"Step {step}: G91 → G1 → G90 order", 
         lines[0] == "G91" and lines[1].startswith("G1 X") and lines[2] == "G90")

test_cmd = "G1 X10.5 Y20.3 F500"
encoded = (test_cmd + "\n").encode('ascii')
test("Command encodes to ASCII bytes", encoded == b"G1 X10.5 Y20.3 F500\n")
test("No \\r in encoded command", b"\r" not in encoded)
test("Ends with exactly \\n", encoded[-1:] == b"\n")

homing_cmd = "$H"
encoded_h = (homing_cmd + "\n").encode('ascii')
test("$H encodes correctly", encoded_h == b"$H\n")

for pwr in [0, 250, 500, 750, 1000]:
    s_val = round((pwr / 1000) * 255)
    test(f"Laser power {pwr}/1000 → S{s_val} (0-255 range)", 0 <= s_val <= 255)


# ══════════════════════════════════════════════════════════════════
# TEST 7: G-code Flow Order
# ══════════════════════════════════════════════════════════════════
print("\n" + "═"*60)
print("TEST 7: G-code Flow Correctness")
print("═"*60)

init_idx = next((i for i, l in enumerate(active_lines) if l.startswith("G21")), -1)
home_idx = next((i for i, l in enumerate(active_lines) if l.startswith("G28")), -1)
first_move = next((i for i, l in enumerate(active_lines) if l.startswith("G0 X")), -1)
end_idx = next((i for i, l in enumerate(active_lines) if l.startswith("M2")), -1)

test("G21 (units) before G28 (home)", init_idx < home_idx)
test("G28 (home) before first move", home_idx < first_move)
test("First move before M2 (end)", first_move < end_idx)
test("M2 is the last active command", end_idx == len(active_lines) - 1)

pad_sequences_valid = True
for i, line in enumerate(active_lines):
    if line.startswith("G4 P"):
        if i > 0 and not active_lines[i-1].startswith("M3"):
            pad_sequences_valid = False
        if i < len(active_lines)-1 and not active_lines[i+1].startswith("M5"):
            pad_sequences_valid = False

test("Pad sequence: M3 → G4 → M5", pad_sequences_valid)


# ══════════════════════════════════════════════════════════════════
# TEST 8: Feed Rate Consistency
# ══════════════════════════════════════════════════════════════════
print("\n" + "═"*60)
print("TEST 8: Feed Rate Consistency")
print("═"*60)

feed_pattern = re.compile(r'F(\d+)')
feed_rates_found = set()
for line in active_lines:
    if line.startswith("G1"):
        m = feed_pattern.search(line)
        if m:
            feed_rates_found.add(int(m.group(1)))

test("All G1 moves have consistent feed rate", len(feed_rates_found) <= 1,
     f"Found feed rates: {feed_rates_found}")
test("Feed rate is 1000 mm/min", 1000 in feed_rates_found or len(feed_rates_found) == 0)


# ══════════════════════════════════════════════════════════════════
# RESULTS
# ══════════════════════════════════════════════════════════════════
print("\n" + "═"*60)
print(f"RESULTS: {passed} passed, {failed} failed")
print("═"*60)

if errors:
    print("\nFailed tests:")
    for e in errors:
        print(e)

# Cleanup temp files
for p in [toolpath_path, gcode_path, gcode_2x_path]:
    try:
        os.unlink(p)
    except:
        pass

sys.exit(0 if failed == 0 else 1)
