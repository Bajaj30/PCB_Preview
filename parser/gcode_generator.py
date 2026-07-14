#!/usr/bin/env python3
"""
gcode_generator.py — Phase 4: G-Code Generator

Reads toolpath.json and generates standard G-code for the PCB plotter.

Output: output/output.gcode

Supported G-code commands:
  G21        — units: millimeters
  G90        — absolute positioning
  G28        — home all axes
  G0 Xn Yn   — rapid move (laser off)
  G1 Xn Yn Fn — linear draw (laser on) at feed rate F
  M3 Sn      — laser on at power level n (0-1000)
  M5         — laser off
  G4 Pn      — dwell n milliseconds (pad exposure)
  M2         — program end

Feed rate:
  G0 (rapid): maximum speed, no F needed
  G1 (draw):  configurable, default 1000 mm/min

Laser control:
  M5 is emitted before every rapid move to prevent burning during travel.
  M3 is emitted before every draw sequence with S value scaled to trace width.
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── Configuration ──────────────────────────────────────────────────
FEED_RATE = 1000        # mm/min for drawing moves (G1)
RAPID_FEED = 3000       # mm/min for rapid moves (some controllers need this)
PAD_DWELL_MS = 200       # milliseconds to dwell on pad locations
LASER_POWER = 1000       # default S value for trace drawing (0-1000)
PAD_LASER_POWER = 1000   # S value for pad exposure (matches LASER_POWER)


def generate_gcode(input_path: str = None, output_path: str = None,
                   feed_rate: int = FEED_RATE, scale: int = 1):
    """
    Generate G-code from toolpath.

    Args:
        input_path:  Path to toolpath.json
        output_path: Path for output G-code file
        feed_rate:   Driawing feed rate in mm/min
        scale:       Scale factor for all coordinates (1, 2, 5, 10)
    """
    if input_path is None:
        input_path = str(PROJECT_ROOT / "output" / "toolpath.json")
    if output_path is None:
        output_path = str(PROJECT_ROOT / "output" / "output.gcode")

    print(f"[gcode] Loading: {input_path}")

    with open(input_path, 'r') as f:
        data = json.load(f)

    commands = data.get('commands', [])
    stats = data.get('statistics', {})
    work_area = data.get('work_area', {})
    mode = data.get('mode', 'trace')  # 'trace' or 'raster'

    scaled_w = round(work_area.get('width', 0) * scale, 2)
    scaled_h = round(work_area.get('height', 0) * scale, 2)

    print(f"[gcode] Processing {len(commands)} toolpath commands (scale: {scale}x, mode: {mode})...")

    # Build G-code lines
    gcode = []

    # Header
    if mode == 'raster':
        raster_settings = data.get('raster_settings', {})
        line_sp = raster_settings.get('line_spacing', 0.1)
        px_per_mm = round(1.0 / line_sp) if line_sp > 0 else 10
        gcode.append(f"; PCB Plotter Raster G-Code")
        gcode.append(f"; Resolution: {px_per_mm} px/mm ({line_sp}mm step)")
        gcode.append(f"; Work area: {scaled_w:.2f} x {scaled_h:.2f} mm")
    else:
        gcode.append(f"; PCB Plotter G-Code")
        gcode.append(f"; Source: {data.get('source', 'unknown')}")
        gcode.append(f"; Scale: {scale}x")
        gcode.append(f"; Work area: {scaled_w} x {scaled_h} mm (original: {work_area.get('width', 0)} x {work_area.get('height', 0)} mm)")
        gcode.append(f"; Draw moves: {stats.get('draw_moves', 0)}")
        gcode.append(f"; Rapid moves: {stats.get('rapid_moves', 0)}")
        gcode.append(f"; Pad marks: {stats.get('pad_marks', 0)}")
        gcode.append(f"; Feed rate: {feed_rate} mm/min")
        gcode.append(f"; Laser power: S{LASER_POWER} (trace), S{PAD_LASER_POWER} (pad)")
        gcode.append(f";")
    gcode.append(f"")

    # Initialization
    gcode.append("G21          ; Units: millimeters")
    gcode.append("G90          ; Absolute positioning")
    gcode.append("G28          ; Home all axes")
    gcode.append("M5           ; Ensure laser is OFF")
    gcode.append(f"G0 F{feed_rate}   ; Set rapid feed")
    gcode.append(f"G1 F{feed_rate}   ; Set draw feed")
    gcode.append("")

    # Track state to avoid redundant laser on/off commands
    laser_on = False
    line_count = 0

    for cmd in commands:
        cmd_type = cmd['type']
        x = cmd.get('x', 0) * scale
        y = cmd.get('y', 0) * scale

        if cmd_type == 'rapid':
            # ── LASER OFF before rapid repositioning ──
            if laser_on:
                gcode.append("M5           ; Laser off")
                laser_on = False
            gcode.append(f"G0 X{x:.3f} Y{y:.3f}")

        elif cmd_type == 'draw':
            # ── LASER ON before first draw in a sequence ──
            if not laser_on:
                if mode == 'raster':
                    # Raster mode: constant full power (like reference file)
                    s_val = LASER_POWER
                else:
                    # Vector trace mode: scale power based on trace width
                    #   0.20mm → S640,  0.25mm → S800,  0.50mm → S1000
                    width = cmd.get('width', 0.25)
                    s_val = int(min(width / 0.25 * LASER_POWER, 1000))
                gcode.append(f"M3 S{s_val}")
                laser_on = True
            gcode.append(f"G1 X{x:.3f} Y{y:.3f} F{feed_rate}")
            line_count += 1

        elif cmd_type == 'pad':
            # ── Pad flash: rapid to location, laser on, dwell, laser off ──
            if laser_on:
                gcode.append("M5           ; Laser off")
                laser_on = False
            gcode.append(f"G0 X{x:.3f} Y{y:.3f}")
            gcode.append(f"M3 S{PAD_LASER_POWER}      ; Laser on for pad")
            gcode.append(f"G4 P{PAD_DWELL_MS}     ; Pad dwell {PAD_DWELL_MS}ms (D={cmd.get('diameter', 0):.2f}mm)")
            gcode.append("M5           ; Laser off")
            laser_on = False

    # Footer — ensure laser is off before returning
    gcode.append("")
    gcode.append("M5           ; Laser off (safety)")
    gcode.append("G0 X0 Y0     ; Return to origin")
    gcode.append("M2           ; Program end")

    # Write file
    with open(output_path, 'w') as f:
        f.write('\n'.join(gcode))
        f.write('\n')

    total_lines = len(gcode)
    print(f"[gcode] Generated successfully!")
    print(f"  G-code lines:  {total_lines}")
    print(f"  Draw commands:  {line_count}")
    print(f"  Scale:         {scale}x")
    print(f"  Work area:     {scaled_w} x {scaled_h} mm")
    print(f"  Feed rate:     {feed_rate} mm/min")
    print(f"  Laser power:   S{LASER_POWER} (trace), S{PAD_LASER_POWER} (pad)")
    print(f"  Output:        {output_path}")

    return output_path


if __name__ == "__main__":
    generate_gcode()
