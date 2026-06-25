#!/usr/bin/env python3
"""
raster_generator.py — Raster Etch Toolpath Generator

Generates horizontal scan-line toolpath that etches EVERYTHING EXCEPT
copper trace/pad regions. Used for etch-resist removal on PCB stencils.

Physical setup (3-layer stencil):
  ┌─────────────────┐
  │  Etch Resist     │ ← Laser burns this AWAY where there's no copper
  ├─────────────────┤
  │  Copper          │ ← Acid bath dissolves exposed copper
  ├─────────────────┤
  │  Insulator       │
  └─────────────────┘

Algorithm:
  1. Load parsed geometry (tracks as thick line segments, pads as circles)
  2. For each horizontal scan line at Y = y_min, y_min + spacing, ..., y_max:
     a. Compute copper coverage X-intervals (capsules + circles)
     b. Merge overlapping intervals
     c. Compute complement → burn segments (areas to etch)
  3. Emit rapid/draw commands for burn segments
  4. Bidirectional scanning (L→R on even lines, R→L on odd) for efficiency

Output: Same toolpath.json format as toolpath_generator.py, so
        gcode_generator.py works unchanged.
"""

import json
import math
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── Default Configuration ─────────────────────────────────────────
DEFAULT_LINE_SPACING = 0.1    # mm between scan lines
DEFAULT_MARGIN = 1.0          # mm extra etch area around board bounds
DEFAULT_SAFETY_GAP = 0.15    # mm extra clearance around copper features
DEFAULT_LASER_DIAMETER = 0.2 # mm physical laser spot size


def polygon_interior_x_intervals(vertices, scan_y):
    """
    Compute X-intervals where a horizontal scan line at Y = scan_y
    intersects the interior of a polygon defined by vertices.
    Using standard ray-casting/scanline intersection.
    """
    if len(vertices) < 3:
        return []

    x_crossings = []
    n = len(vertices)
    for i in range(n):
        x1, y1 = vertices[i]
        x2, y2 = vertices[(i + 1) % n]

        # Check if the edge crosses the scan line Y = scan_y
        # To avoid double-counting vertices, we use a half-open interval for Y
        if (y1 <= scan_y < y2) or (y2 <= scan_y < y1):
            if abs(y2 - y1) > 1e-12:
                # Compute X coordinate of intersection
                t = (scan_y - y1) / (y2 - y1)
                x_intersect = x1 + t * (x2 - x1)
                x_crossings.append(x_intersect)

    x_crossings.sort()

    # Pair them up
    intervals = []
    for i in range(0, len(x_crossings) - 1, 2):
        intervals.append((x_crossings[i], x_crossings[i+1]))

    return intervals


def capsule_x_intervals(x1, y1, x2, y2, r, scan_y):
    """
    Compute X-intervals where a capsule (thick line segment) intersects
    a horizontal scan line at Y = scan_y.

    A capsule = Minkowski sum of line segment P1P2 and disk of radius r.
    It consists of:
      - Circle endcap at P1 (radius r)
      - Circle endcap at P2 (radius r)
      - Rectangular body connecting them

    Returns: list of (x_start, x_end) tuples (may overlap — caller merges).
    """
    intervals = []

    # ── Endcap circle at P1 ──
    dy1 = scan_y - y1
    if abs(dy1) <= r:
        dx_range = math.sqrt(max(0, r * r - dy1 * dy1))
        intervals.append((x1 - dx_range, x1 + dx_range))

    # ── Endcap circle at P2 ──
    dy2 = scan_y - y2
    if abs(dy2) <= r:
        dx_range = math.sqrt(max(0, r * r - dy2 * dy2))
        intervals.append((x2 - dx_range, x2 + dx_range))

    # ── Body rectangle ──
    dx = x2 - x1
    dy = y2 - y1
    L = math.sqrt(dx * dx + dy * dy)

    if L > 1e-9:
        # Unit normal perpendicular to the segment
        nx = -dy / L
        ny = dx / L

        # Four corners of the body rectangle
        corners = [
            (x1 + r * nx, y1 + r * ny),
            (x2 + r * nx, y2 + r * ny),
            (x2 - r * nx, y2 - r * ny),
            (x1 - r * nx, y1 - r * ny),
        ]

        # Intersect horizontal line scan_y with convex polygon edges
        x_crossings = []
        n = len(corners)
        for i in range(n):
            cx1, cy1 = corners[i]
            cx2, cy2 = corners[(i + 1) % n]

            edge_dy = cy2 - cy1
            if abs(edge_dy) < 1e-12:
                # Horizontal edge — check if it's at scan_y
                if abs(cy1 - scan_y) < 1e-6:
                    x_crossings.extend([cx1, cx2])
            else:
                t = (scan_y - cy1) / edge_dy
                if 0 <= t <= 1:
                    x_cross = cx1 + t * (cx2 - cx1)
                    x_crossings.append(x_cross)

        if len(x_crossings) >= 2:
            intervals.append((min(x_crossings), max(x_crossings)))

    return intervals


def circle_x_interval(cx, cy, r, scan_y):
    """
    Compute X-interval where circle at (cx, cy) with radius r
    intersects a horizontal scan line at Y = scan_y.

    Returns: (x_start, x_end) or None if no intersection.
    """
    dy = scan_y - cy
    if abs(dy) > r:
        return None
    dx_range = math.sqrt(max(0, r * r - dy * dy))
    return (cx - dx_range, cx + dx_range)


def merge_intervals(intervals):
    """Merge overlapping/touching intervals. Returns sorted, non-overlapping list."""
    if not intervals:
        return []
    sorted_iv = sorted(intervals)
    merged = [list(sorted_iv[0])]
    for start, end in sorted_iv[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [tuple(iv) for iv in merged]


def complement_intervals(intervals, x_min, x_max):
    """
    Return intervals within [x_min, x_max] NOT covered by the input intervals.
    Input must be sorted and non-overlapping (output of merge_intervals).
    """
    result = []
    current = x_min
    for start, end in intervals:
        # Clamp to etch area
        start = max(start, x_min)
        end = min(end, x_max)
        if start > current:
            result.append((current, start))
        current = max(current, end)
    if current < x_max:
        result.append((current, x_max))
    return result


# ── Main Generator ────────────────────────────────────────────────

def generate_raster_toolpath(input_path=None, output_path=None,
                              line_spacing=DEFAULT_LINE_SPACING,
                              margin=DEFAULT_MARGIN,
                              safety_gap=None,
                              laser_diameter=DEFAULT_LASER_DIAMETER):
    """
    Generate a raster etch toolpath from parsed Gerber geometry.

    The toolpath covers the entire board area with horizontal scan lines,
    SKIPPING over copper trace and pad regions (leaving etch resist intact
    on those areas).

    The safety gap around copper features is computed from the laser spot
    size: safety_gap = laser_diameter/2 + 0.05mm margin. This ensures the
    laser beam edge doesn't overlap the copper protection zone.

    Args:
        input_path:      Path to parsed_tracks.json
        output_path:     Path for output toolpath.json
        line_spacing:    Distance between scan lines in mm (default: 0.1)
        margin:          Extra etch area around board bounds in mm (default: 1.0)
        safety_gap:      Override clearance around copper (default: auto from laser_diameter)
        laser_diameter:  Physical laser spot size in mm (default: 0.2)

    Returns:
        dict — the toolpath data (also written to output_path)
    """
    if input_path is None:
        input_path = str(PROJECT_ROOT / "output" / "parsed_tracks.json")
    if output_path is None:
        output_path = str(PROJECT_ROOT / "output" / "toolpath.json")

    # Auto-calculate safety gap from laser spot size if not explicitly set
    # safety_gap = laser_diameter ensures the beam EDGE clears copper by laser_radius
    if safety_gap is None:
        safety_gap = laser_diameter  # full diameter → beam edge clears by radius

    print(f"[raster] Loading: {input_path}")
    print(f"[raster] Laser diameter: {laser_diameter} mm → safety gap: {safety_gap:.2f} mm")

    with open(input_path, 'r') as f:
        data = json.load(f)

    tracks = data.get('tracks', [])
    pads = data.get('pads', [])
    regions = data.get('regions', [])
    bounds = data.get('bounds', {})

    # ── Normalize coordinates (shift so board min = 0,0) ──
    offset_x = bounds.get('min_x', 0)
    offset_y = bounds.get('min_y', 0)
    board_w = bounds.get('width', 0)
    board_h = bounds.get('height', 0)

    norm_tracks = []
    for t in tracks:
        norm_tracks.append({
            'x1': t['x1'] - offset_x,
            'y1': t['y1'] - offset_y,
            'x2': t['x2'] - offset_x,
            'y2': t['y2'] - offset_y,
            'width': t['width'],
        })

    norm_pads = []
    for p in pads:
        norm_pads.append({
            'x': p['x'] - offset_x,
            'y': p['y'] - offset_y,
            'diameter': p['diameter'],
        })

    norm_regions = []
    for r in regions:
        norm_outline = [(pt[0] - offset_x, pt[1] - offset_y) for pt in r['outline']]
        norm_regions.append({
            'outline': norm_outline,
            'polarity': r.get('polarity', True)
        })

    # ── Etch area bounds (board + margin on far side, start at 0,0) ──
    x_min = 0.0
    x_max = board_w + margin
    y_min = 0.0
    y_max = board_h + margin

    num_scan_lines = int(math.ceil((y_max - y_min) / line_spacing)) + 1

    print(f"[raster] Board:        {board_w:.2f} x {board_h:.2f} mm")
    print(f"[raster] Etch area:    {x_max - x_min:.2f} x {y_max - y_min:.2f} mm")
    print(f"[raster] Line spacing: {line_spacing} mm")
    print(f"[raster] Scan lines:   {num_scan_lines}")
    print(f"[raster] Safety gap:   {safety_gap} mm")
    print(f"[raster] Copper:       {len(norm_tracks)} tracks, {len(norm_pads)} pads, {len(norm_regions)} regions")

    # ── Generate scan line commands ──
    commands = []
    total_rapid_dist = 0.0
    total_draw_dist = 0.0
    current_x, current_y = 0.0, 0.0

    scan_y = y_min
    line_num = 0

    while scan_y <= y_max + 1e-9:
        # ── Collect all copper X-intervals at this scan_y ──
        copper_intervals = []

        for t in norm_tracks:
            r = t['width'] / 2.0 + safety_gap
            ivs = capsule_x_intervals(
                t['x1'], t['y1'], t['x2'], t['y2'], r, scan_y
            )
            copper_intervals.extend(ivs)

        for p in norm_pads:
            r = p['diameter'] / 2.0 + safety_gap
            iv = circle_x_interval(p['x'], p['y'], r, scan_y)
            if iv:
                copper_intervals.append(iv)

        for reg in norm_regions:
            # 1. Add capsule boundary edges with safety_gap/clearance
            vertices = reg['outline']
            n = len(vertices)
            for i in range(n):
                x1, y1 = vertices[i]
                x2, y2 = vertices[(i + 1) % n]
                ivs = capsule_x_intervals(x1, y1, x2, y2, safety_gap, scan_y)
                copper_intervals.extend(ivs)

            # 2. Add interior intervals if region is copper (dark polarity)
            if reg['polarity']:
                ivs = polygon_interior_x_intervals(vertices, scan_y)
                copper_intervals.extend(ivs)

        # Merge overlapping copper zones
        merged_copper = merge_intervals(copper_intervals)

        # Compute burn segments (complement: everything NOT copper)
        burn_segments = complement_intervals(merged_copper, x_min, x_max)

        # Filter out tiny segments (< 0.05mm not worth burning)
        burn_segments = [(s, e) for s, e in burn_segments if (e - s) > 0.05]

        if burn_segments:
            # ── Bidirectional scanning ──
            if line_num % 2 == 0:
                # Even lines: left → right
                for seg_start, seg_end in burn_segments:
                    rapid_dist = math.sqrt(
                        (seg_start - current_x) ** 2 +
                        (scan_y - current_y) ** 2
                    )
                    total_rapid_dist += rapid_dist
                    commands.append({
                        'type': 'rapid',
                        'x': round(seg_start, 4),
                        'y': round(scan_y, 4),
                    })
                    current_x, current_y = seg_start, scan_y

                    draw_dist = abs(seg_end - seg_start)
                    total_draw_dist += draw_dist
                    commands.append({
                        'type': 'draw',
                        'x': round(seg_end, 4),
                        'y': round(scan_y, 4),
                        'width': line_spacing,
                    })
                    current_x = seg_end
            else:
                # Odd lines: right → left
                for seg_start, seg_end in reversed(burn_segments):
                    rapid_dist = math.sqrt(
                        (seg_end - current_x) ** 2 +
                        (scan_y - current_y) ** 2
                    )
                    total_rapid_dist += rapid_dist
                    commands.append({
                        'type': 'rapid',
                        'x': round(seg_end, 4),
                        'y': round(scan_y, 4),
                    })
                    current_x, current_y = seg_end, scan_y

                    draw_dist = abs(seg_end - seg_start)
                    total_draw_dist += draw_dist
                    commands.append({
                        'type': 'draw',
                        'x': round(seg_start, 4),
                        'y': round(scan_y, 4),
                        'width': line_spacing,
                    })
                    current_x = seg_start

        scan_y += line_spacing
        line_num += 1

    # Return to origin
    commands.append({'type': 'rapid', 'x': 0, 'y': 0})
    total_rapid_dist += math.sqrt(current_x ** 2 + current_y ** 2)

    # ── Build output (same format as toolpath_generator.py) ──
    result = {
        'source': data.get('source_file', 'unknown'),
        'units': 'mm',
        'mode': 'raster',
        'raster_settings': {
            'line_spacing': line_spacing,
            'margin': margin,
            'safety_gap': safety_gap,
        },
        'offset_applied': {
            'x': round(offset_x, 4),
            'y': round(offset_y, 4),
        },
        'work_area': {
            'width': round(board_w, 4),
            'height': round(board_h, 4),
        },
        'statistics': {
            'total_commands': len(commands),
            'rapid_moves': sum(1 for c in commands if c['type'] == 'rapid'),
            'draw_moves': sum(1 for c in commands if c['type'] == 'draw'),
            'pad_marks': 0,
            'scan_lines': line_num,
            'total_rapid_distance_mm': round(total_rapid_dist, 2),
            'total_draw_distance_mm': round(total_draw_dist, 2),
        },
        'commands': commands,
    }

    # Write output
    with open(output_path, 'w') as f:
        json.dump(result, f, indent=2)

    print(f"[raster] Generated successfully!")
    print(f"  Commands:       {len(commands)}")
    print(f"  Scan lines:     {line_num}")
    print(f"  Rapid moves:    {result['statistics']['rapid_moves']}")
    print(f"  Draw moves:     {result['statistics']['draw_moves']}")
    print(f"  Rapid travel:   {total_rapid_dist:.2f} mm")
    print(f"  Draw distance:  {total_draw_dist:.2f} mm")
    print(f"  Work area:      {board_w:.2f} x {board_h:.2f} mm")
    print(f"  Output:         {output_path}")

    return result


if __name__ == "__main__":
    generate_raster_toolpath()
