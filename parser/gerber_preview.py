#!/usr/bin/env python3
"""
gerber_preview.py — Multi-layer Gerber PCB Preview Server (v3)

Uses gerbonara for rendering (replaces pygerber).
- Accurate copper fill / thermal relief rendering
- SVG output (vector, scales perfectly, no bitmap artifacts)
- Unified bounding box via force_bounds (all layers pixel-aligned)
- No Cairo/cairosvg dependency needed

Usage:
    cd Test/
    uvicorn parser.gerber_preview:app --reload --port 5050
"""

from pathlib import Path
from typing import List

# pyrefly: ignore [missing-import]
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse
from fastapi.templating import Jinja2Templates
from gerbonara import GerberFile
import json as _json

import sys
import threading
import time
import serial
import serial.tools.list_ports

# ── Dummy Serial Port for Testing ──────────────────────────────────
# Params stored at module level so they persist across reconnections
_DUMMY_GRBL_PARAMS = {
    0: 10, 1: 25, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0, 10: 3,
    11: 0.010, 12: 0.002, 13: 0, 20: 0, 21: 1, 22: 1,
    23: 4, 24: 25.000, 25: 500.000, 27: 5.000, 30: 1000,
    31: 0, 32: 1, 100: 180.077, 101: 165.148, 102: 250.000,
    110: 1000.000, 111: 1000.000, 112: 500.000, 120: 200.000,
    121: 100.000, 122: 10.000, 130: 250.000, 131: 250.000,
    132: 200.000
}

class DummySerial:
    def __init__(self):
        self.is_open = True
        self.timeout = 1.0
        self._buffer = b""
        self._params = _DUMMY_GRBL_PARAMS  # shared reference — persists across reconnections
    
    @property
    def in_waiting(self):
        return len(self._buffer)
        
    def write(self, data):
        cmd = data.decode('ascii', errors='ignore').strip()
        if not cmd:
            pass
        elif cmd.startswith('$') and '=' in cmd:
            try:
                parts = cmd[1:].split('=')
                self._params[int(parts[0])] = float(parts[1])
                self._buffer += b"ok\r\n"
            except:
                self._buffer += b"error: bad format\r\n"
        elif cmd == "$$":
            for k, v in self._params.items():
                self._buffer += f"${k}={v}\r\n".encode('ascii')
            self._buffer += b"ok\r\n"
        elif cmd == "?":
            self._buffer += b"<Idle|MPos:0.000,0.000,0.000|Bf:15,127|FS:0,0>\r\n"
        elif cmd == "$X":
            self._buffer += b"[MSG:'$H'|'$X' to unlock]\r\nok\r\n"
        else:
            self._buffer += b"ok\r\n"
        
    def readline(self):
        if b'\n' in self._buffer:
            idx = self._buffer.find(b'\n')
            line = self._buffer[:idx+1]
            self._buffer = self._buffer[idx+1:]
            return line
        elif self._buffer:
            line = self._buffer
            self._buffer = b""
            return line
        return b""
        
    def reset_input_buffer(self):
        self._buffer = b""
        
    def close(self):
        self.is_open = False


# ── Serial Manager ─────────────────────────────────────────────────
class SerialManager:
    def __init__(self):
        self.serial_port = None
        self.streaming = False
        self.abort_flag = False
        self.stream_thread = None
        self.total_lines = 0
        self.sent_lines = 0
        self.machine_pos = {"x": "0.00", "y": "0.00"}
        self._sse_queues: set = set()  # SSE subscriber queues for live preview sync
        self._lock = threading.Lock()  # Protects serial port from concurrent access

    def _handle_disconnect(self, reason: str = "USB disconnected"):
        """Cleanly tear down the serial connection and notify SSE subscribers."""
        print(f"[SERIAL] Auto-disconnect: {reason}")
        self.abort_flag = True
        self.streaming = False
        if self.serial_port:
            try:
                self.serial_port.close()
            except Exception:
                pass
            self.serial_port = None
        # Push a disconnected event so the browser reacts immediately
        self._push_event({"type": "disconnected", "reason": reason})
        print("[SERIAL] Disconnected")

    def connect(self, port: str, baud: int = 115200):
        if self.serial_port and self.serial_port.is_open:
            self.disconnect()
        if port == "Virtual GRBL (Dummy)":
            self.serial_port = DummySerial()
            self.serial_port._buffer += b"Grbl 1.1h ['$' for help]\r\n"
            time.sleep(0.5)
        else:
            self.serial_port = serial.Serial(port, baud, timeout=2)
            time.sleep(2)  # Wait for GRBL to initialize after DTR reset
        # Wake up GRBL with empty lines
        self.serial_port.write(b"\r\n\r\n")
        time.sleep(1)
        # Drain all startup text from GRBL (e.g. "Grbl 1.1h ['$' for help]")
        startup_msgs = []
        while self.serial_port.in_waiting:
            line = self.serial_port.readline().decode(errors='replace').strip()
            if line:
                startup_msgs.append(line)
        print(f"[SERIAL] Connected to {port} @ {baud}")
        for msg in startup_msgs:
            print(f"[SERIAL] Startup: {msg}")
        self.serial_port.reset_input_buffer()

        # Auto-unlock: Send $X to clear alarm state on initial connection.
        # GRBL boots into Alarm mode and won't accept motion commands until unlocked.
        # NOTE: After a $H homing cycle, GRBL may re-enter alarm if limits are hit;
        # that's handled separately — this $X only runs once on connect.
        print("[SERIAL] Sending $X to unlock GRBL...")
        self.serial_port.write(b"$X\n")
        time.sleep(0.5)
        while self.serial_port.in_waiting:
            line = self.serial_port.readline().decode(errors='replace').strip()
            if line:
                print(f"[SERIAL] $X response: {line}")

    def disconnect(self):
        self.abort_flag = True
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.close()
            except Exception:
                pass
            self.serial_port = None
        print("[SERIAL] Disconnected")

    def send_command(self, cmd: str) -> str:
        if not self.serial_port or not self.serial_port.is_open:
            raise Exception("Serial port not connected")

        with self._lock:
            # Drain any stale data in the buffer first
            drained = 0
            while self.serial_port.in_waiting:
                self.serial_port.readline()
                drained += 1
            if drained:
                print(f"[SERIAL] Drained {drained} stale line(s) before sending")

            # Support multi-line commands (split on \n)
            # e.g. "G91\nG1 X0.125 F500\nG90" → 3 separate lines
            lines = [l.strip() for l in cmd.strip().split('\n') if l.strip()]
            all_responses = []

            for line in lines:
                # Determine if this is a long-running GRBL command
                is_homing = line.upper() in ('$H', '$H\r')
                is_grbl_cmd = line.startswith('$')

                # Temporarily increase timeout for homing ($H takes 10-30s)
                original_timeout = self.serial_port.timeout
                if is_homing:
                    self.serial_port.timeout = 30
                    print(f"[SERIAL] >>> {line}  (homing — timeout set to 30s)")
                else:
                    print(f"[SERIAL] >>> {line}")

                # Send the command with exactly \n (not \r\n)
                self.serial_port.write((line + "\n").encode('ascii'))

                # Read responses until we get 'ok' or 'error' or timeout
                while True:
                    try:
                        resp = self.serial_port.readline().decode(errors='replace').strip()
                    except Exception as e:
                        print(f"[SERIAL] Read error: {e}")
                        resp = ""

                    if not resp:
                        print(f"[SERIAL] <<< (timeout, no response)")
                        break  # timeout — no more data
                    print(f"[SERIAL] <<< {resp}")
                    all_responses.append(resp)

                    # For standard G-code, stop on 'ok' or 'error'
                    if resp == 'ok' or resp.startswith('error'):
                        break
                    # For GRBL $ commands, they may send multi-line data then 'ok'
                    # Keep reading until we hit 'ok'

                # Restore original timeout
                if is_homing:
                    self.serial_port.timeout = original_timeout

            result = "; ".join(all_responses) if all_responses else "no response"
            return result

    def _stream_task(self, lines: list[str]):
        self.total_lines = len(lines)
        self.sent_lines = 0
        self.streaming = True
        self.abort_flag = False
        print(f"[SERIAL] Starting G-code stream: {self.total_lines} lines")

        for line in lines:
            if self.abort_flag:
                # Send feed hold to stop motion immediately
                if self.serial_port and self.serial_port.is_open:
                    self.serial_port.write(b"!")
                print("[SERIAL] Stream ABORTED by user")
                # Push abort event to SSE subscribers
                self._push_event({"type": "abort", "sent": self.sent_lines, "total": self.total_lines})
                break
            line = line.strip()
            if not line or line.startswith(';'):
                self.sent_lines += 1
                continue

            # Send and wait for 'ok'
            if self.serial_port and self.serial_port.is_open:
                with self._lock:
                    try:
                        self.serial_port.write((line + "\n").encode('ascii'))
                    except serial.SerialException as e:
                        print(f"[SERIAL] Stream write error: {e}")
                        self._handle_disconnect(str(e))
                        return
                    while True:
                        try:
                            resp = self.serial_port.readline().decode(errors='replace').strip()
                        except serial.SerialException as e:
                            print(f"[SERIAL] Stream read error: {e}")
                            self._handle_disconnect(str(e))
                            return
                        except Exception as e:
                            print(f"[SERIAL] Stream read error: {e}")
                            resp = ""
                            break
                        if resp == 'ok' or resp.startswith('error'):
                            if resp.startswith('error'):
                                print(f"[SERIAL] Stream error on line {self.sent_lines}: {resp} (cmd: {line})")
                            break
                        if not resp:
                            print(f"[SERIAL] Stream timeout on line {self.sent_lines} (cmd: {line})")
                            break

            self.sent_lines += 1
            # Push progress event to SSE subscribers
            self._push_event({
                "type": "line",
                "line": line,
                "sent": self.sent_lines,
                "total": self.total_lines
            })

        self.streaming = False
        print(f"[SERIAL] Stream complete: {self.sent_lines}/{self.total_lines} lines sent")
        # Push done event
        self._push_event({"type": "done", "sent": self.sent_lines, "total": self.total_lines})

    def _push_event(self, data: dict):
        """Push an event to all active SSE subscriber queues."""
        import json
        dead = []
        for q in self._sse_queues:
            try:
                q.put_nowait(json.dumps(data))
            except Exception:
                dead.append(q)
        for q in dead:
            self._sse_queues.discard(q)

    def subscribe_sse(self):
        """Register a new SSE subscriber queue. Returns the queue."""
        import queue
        q = queue.Queue(maxsize=500)
        self._sse_queues.add(q)
        return q

    def unsubscribe_sse(self, q):
        """Remove an SSE subscriber queue."""
        self._sse_queues.discard(q)

    def start_stream(self, lines: list[str]):
        if self.streaming:
            raise Exception("Already streaming")
        self.stream_thread = threading.Thread(target=self._stream_task, args=(lines,))
        self.stream_thread.start()

serial_mgr = SerialManager()

# ── Paths ──────────────────────────────────────────────────────────
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    BUNDLE_DIR = Path(sys._MEIPASS)
else:
    BUNDLE_DIR = Path(__file__).resolve().parent.parent

# Read-only assets bundled with the executable
TEMPLATE_DIR = BUNDLE_DIR / "templates"

# Mutable user data paths created in the folder where the .exe is launched
USER_DIR = Path.cwd()
GERBER_DIR = USER_DIR / "gerbers"
OUTPUT_DIR = USER_DIR / "output"

# Ensure directories exist
GERBER_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Layer colors (fg = feature color, bg = background) ─────────────
# All layers use bg='none' (transparent) so they overlay cleanly
LAYER_COLORS = {
    "Cu":          {"fg": "#1a1a1a",    "bg": "none"},    # dark traces
    "Mask":        {"fg": "#2d8c3c88",  "bg": "none"},    # semi-transparent green
    "Paste":       {"fg": "#99999966",  "bg": "none"},    # semi-transparent grey
    "Silkscreen":  {"fg": "#e0e0e0",    "bg": "none"},    # white text
}

# Board background color (shown behind all layers)
BOARD_BG_COLOR = "#88c563"


def detect_layer_type(filename: str) -> str:
    """Detect the Gerber layer type from the filename (fallback method)."""
    name = filename.lower()
    if "cu" in name:
        return "Cu"
    elif "mask" in name:
        return "Mask"
    elif "paste" in name:
        return "Paste"
    elif "silk" in name:
        return "Silkscreen"
    return "Unknown"


def detect_layer_type_by_content(filepath: str) -> str:
    """
    Detect the Gerber layer type by reading the file's X2 attributes.
    Uses %TF.FileFunction header (Gerber X2 standard), falls back to filename.

    Returns: 'Cu', 'Mask', 'Paste', 'Silkscreen', or 'Unknown'
    """
    try:
        gf = GerberFile.open(filepath)
        file_attrs = getattr(gf, 'file_attrs', {})
        file_func = file_attrs.get('.FileFunction', ())

        if file_func:
            func_name = file_func[0].lower()
            if func_name == 'copper':
                return 'Cu'
            elif func_name == 'soldermask':
                return 'Mask'
            elif func_name == 'paste':
                return 'Paste'
            elif func_name in ('legend', 'silkscreen'):
                return 'Silkscreen'
            elif func_name == 'profile':
                return 'EdgeCuts'
    except Exception:
        pass

    # Fallback: detect by filename
    return detect_layer_type(Path(filepath).name)


def find_copper_layer() -> str | None:
    """
    Scan gerbers/ directory and find the copper layer file.
    Uses content-based detection first, filename fallback second.
    Returns the filename of the copper layer, or None.
    """
    if not GERBER_DIR.exists():
        return None

    for gbr in sorted(GERBER_DIR.glob("*.gbr")):
        layer_type = detect_layer_type_by_content(str(gbr))
        if layer_type == 'Cu':
            return gbr.name

    return None


def get_layer_bounds(filepath: str):
    """Get bounding box using gerbonara. Returns (min_x, min_y, max_x, max_y) or None."""
    try:
        gf = GerberFile.open(filepath)
        bb = gf.bounding_box()
        if bb is None or bb == (None, None):
            return None
        min_x, min_y = float(bb[0][0]), float(bb[0][1])
        max_x, max_y = float(bb[1][0]), float(bb[1][1])
        return (min_x, min_y, max_x, max_y)
    except Exception:
        return None


def compute_union_bounds():
    """Compute union bounding box across all .gbr layers."""
    layer_bounds = {}
    for gbr in sorted(GERBER_DIR.glob("*.gbr")):
        bounds = get_layer_bounds(str(gbr))
        if bounds:
            layer_bounds[gbr.name] = bounds

    if not layer_bounds:
        return None, {}

    union = (
        min(b[0] for b in layer_bounds.values()),
        min(b[1] for b in layer_bounds.values()),
        max(b[2] for b in layer_bounds.values()),
        max(b[3] for b in layer_bounds.values()),
    )
    return union, layer_bounds


# ── App ────────────────────────────────────────────────────────────
app = FastAPI(
    title="Antigravity PCB Preview",
    description="Multi-layer Gerber preview (gerbonara SVG rendering)",
    version="0.4.0",
)

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# ── Caches ─────────────────────────────────────────────────────────
_render_cache: dict[str, str] = {}    # filename -> SVG string
_union_bounds = None
_layer_bounds: dict[str, tuple] = {}
_board_dims = None


def _reset_caches():
    """Clear all caches — call after uploading/deleting files."""
    global _union_bounds, _layer_bounds, _board_dims, _render_cache
    _render_cache = {}
    _union_bounds = None
    _layer_bounds = {}
    _board_dims = None


def _ensure_bounds():
    """Lazily compute union bounds on first request."""
    global _union_bounds, _layer_bounds, _board_dims
    if _union_bounds is None:
        _union_bounds, _layer_bounds = compute_union_bounds()
        if _union_bounds:
            w = _union_bounds[2] - _union_bounds[0]
            h = _union_bounds[3] - _union_bounds[1]
            _board_dims = {
                "width_mm": round(w, 2),
                "height_mm": round(h, 2),
                "min_x": round(_union_bounds[0], 4),
                "min_y": round(_union_bounds[1], 4),
                "max_x": round(_union_bounds[2], 4),
                "max_y": round(_union_bounds[3], 4),
            }


# ── Routes ─────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Main preview page."""
    if not GERBER_DIR.exists():
        raise HTTPException(status_code=404, detail="gerbers/ folder not found")

    _ensure_bounds()

    files = sorted([f.name for f in GERBER_DIR.iterdir() if f.suffix == ".gbr"])
    renderable = [f for f in files if f in _layer_bounds]

    # Detect copper layer by content
    copper_file = find_copper_layer()

    # Load toolpath mapping data if available
    toolpath_offset = None
    toolpath_path = OUTPUT_DIR / "toolpath.json"
    if toolpath_path.exists():
        try:
            with open(toolpath_path) as f:
                tp_data = _json.load(f)
            toolpath_offset = tp_data.get("offset_applied", {})
        except Exception:
            pass

    return templates.TemplateResponse(
        request,
        "preview.html",
        {
            "files": renderable,
            "board": _board_dims or {},
            "board_bg": BOARD_BG_COLOR,
            "copper_file": copper_file,
            "toolpath_offset": toolpath_offset,
        },
    )


@app.get("/render/{filename}")
async def render_layer(filename: str):
    """
    Render a .gbr file to SVG using gerbonara.
    All layers share the same viewBox via force_bounds.
    Returns image/svg+xml.
    """
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    filepath = GERBER_DIR / filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    if filepath.suffix != ".gbr":
        raise HTTPException(status_code=400, detail="Only .gbr files")

    # Check cache
    if filename in _render_cache:
        return Response(content=_render_cache[filename], media_type="image/svg+xml")

    _ensure_bounds()

    if filename not in _layer_bounds:
        raise HTTPException(status_code=400, detail=f"{filename} has no geometry")

    try:
        # Detect layer type for color
        layer_type = detect_layer_type(filename)
        colors = LAYER_COLORS.get(layer_type, LAYER_COLORS["Cu"])

        # Render with gerbonara, using union bounds for alignment
        gf = GerberFile.open(str(filepath))
        force = (
            (_union_bounds[0], _union_bounds[1]),
            (_union_bounds[2], _union_bounds[3]),
        )
        svg = gf.to_svg(
            fg=colors["fg"],
            bg=colors["bg"],
            force_bounds=force,
        )
        svg_str = str(svg)

        # Cache
        _render_cache[filename] = svg_str

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Render failed: {str(e)}")

    return Response(content=svg_str, media_type="image/svg+xml")


@app.get("/layers")
async def list_layers():
    """JSON list of available layers."""
    _ensure_bounds()

    if not GERBER_DIR.exists():
        raise HTTPException(status_code=404, detail="gerbers/ folder not found")

    files = sorted([f.name for f in GERBER_DIR.iterdir() if f.suffix == ".gbr"])
    layers = []
    for f in files:
        # Use content-based detection
        lt = detect_layer_type_by_content(str(GERBER_DIR / f))
        has_geometry = f in _layer_bounds
        layers.append({"filename": f, "type": lt, "has_geometry": has_geometry})

    return {"layers": layers, "count": len(layers), "board": _board_dims}


@app.get("/board-info")
async def board_info():
    """Returns PCB board dimensions."""
    _ensure_bounds()
    if not _board_dims:
        raise HTTPException(status_code=404, detail="No board data")
    return _board_dims


@app.get("/toolpath")
async def get_toolpath():
    """Return toolpath.json data for animation."""
    toolpath_path = OUTPUT_DIR / "toolpath.json"
    if not toolpath_path.exists():
        raise HTTPException(status_code=404, detail="No toolpath. Run conversion first.")

    with open(toolpath_path) as f:
        return _json.load(f)


@app.post("/upload")
async def upload_gerbers(files: List[UploadFile] = File(...)):
    """
    Upload one or more .gbr files.
    Saves to gerbers/ directory and resets all caches.
    """
    GERBER_DIR.mkdir(parents=True, exist_ok=True)

    uploaded = []
    rejected = []

    for file in files:
        # Only accept .gbr files
        if not file.filename or not file.filename.lower().endswith(".gbr"):
            rejected.append(file.filename or "unknown")
            continue

        # Security: strip path components, keep only the filename
        safe_name = Path(file.filename).name
        if not safe_name or safe_name.startswith("."):
            rejected.append(file.filename)
            continue

        # Read content and save
        content = await file.read()
        dest = GERBER_DIR / safe_name
        dest.write_bytes(content)
        uploaded.append(safe_name)

    # Reset caches so next request recomputes bounds + renders
    _reset_caches()

    return JSONResponse(content={
        "uploaded": uploaded,
        "rejected": rejected,
        "total": len(uploaded),
    })


@app.delete("/clear")
async def clear_gerbers():
    """
    Delete all .gbr files from gerbers/ directory.
    Resets all caches.
    """
    removed = []
    if GERBER_DIR.exists():
        for gbr in GERBER_DIR.glob("*.gbr"):
            gbr.unlink()
            removed.append(gbr.name)

    _reset_caches()

    return JSONResponse(content={
        "removed": removed,
        "total": len(removed),
    })


@app.post("/convert-gcode")
async def convert_gcode(scale: int = 1, layers: str = "",
                        mode: str = "trace", line_spacing: float = 0.1,
                        laser_diameter: float = 0.2,
                        orientation: str = "normal",
                        burn_speed: int = 1000,
                        rapid_speed: int = 3000,
                        laser_power: int = 1000):
    """
    Run the full pipeline on selected layer files.

    Modes:
      - trace:  Vector tracing along copper paths (default)
      - raster: Horizontal scan lines that SKIP copper (for etch-resist removal)

    Accepts:
      ?scale=N             (1, 2, 5, 10)
      ?layers=f1,f2,f3     (comma-separated filenames of active layers)
      ?mode=trace|raster   (toolpath generation mode)
      ?line_spacing=0.1    (raster scan line spacing in mm)
      ?laser_diameter=0.2  (physical laser spot size in mm)
      ?orientation=normal|mirror_x|rot90|rot180  (board orientation)
      ?burn_speed=1000     (engraving speed in mm/min)
      ?rapid_speed=3000    (travel speed in mm/min)
      ?laser_power=1000    (laser power S-value, 0-1000)
    """
    # Clamp scale to allowed values
    if scale not in (1, 2, 3, 5, 10):
        scale = 1

    # Validate mode
    if mode not in ("trace", "raster"):
        mode = "trace"

    # Validate orientation
    if orientation not in ("normal", "rot90", "rot180", "rot270"):
        orientation = "normal"

    # Clamp line spacing and laser diameter
    line_spacing = max(0.05, min(2.0, line_spacing))
    laser_diameter = max(0.05, min(1.0, laser_diameter))

    # Clamp speed and power parameters
    burn_speed = max(100, min(10000, burn_speed))
    rapid_speed = max(100, min(10000, rapid_speed))
    laser_power = max(0, min(1000, laser_power))

    # Import pipeline modules
    from parser.parse_gerber import parse_gerber
    from parser.toolpath_generator import generate_toolpath
    from parser.raster_generator import generate_raster_toolpath
    from parser.gcode_generator import generate_gcode
    import json as json_mod

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Determine which files to process
    if layers.strip():
        layer_files = [f.strip() for f in layers.split(",") if f.strip()]
    else:
        # Fallback: auto-detect copper layer
        copper_file = find_copper_layer()
        if not copper_file:
            raise HTTPException(
                status_code=400,
                detail="No layers selected and no copper layer found. "
                       "Toggle at least one layer on, or upload a .gbr file."
            )
        layer_files = [copper_file]

    # Validate all files exist
    valid_files = []
    for f in layer_files:
        path = GERBER_DIR / f
        if path.exists():
            valid_files.append(f)
    if not valid_files:
        raise HTTPException(
            status_code=400,
            detail=f"None of the selected layer files exist: {layer_files}"
        )

    toolpath_path = str(OUTPUT_DIR / "toolpath.json")
    gcode_path = str(OUTPUT_DIR / "output.gcode")

    try:
        # Step 1: Parse each layer and merge tracks + pads
        all_tracks = []
        all_pads = []
        total_parsed_tracks = 0
        total_parsed_pads = 0
        min_x = float('inf')
        min_y = float('inf')
        max_x = float('-inf')
        max_y = float('-inf')

        for i, filename in enumerate(valid_files):
            file_path = str(GERBER_DIR / filename)
            per_file_output = str(OUTPUT_DIR / f"parsed_{i}.json")
            parsed = parse_gerber(file_path, per_file_output)

            all_tracks.extend(parsed.get("tracks", []))
            all_pads.extend(parsed.get("pads", []))
            total_parsed_tracks += parsed["statistics"]["total_tracks"]
            total_parsed_pads += parsed["statistics"]["total_pads"]

            b = parsed["bounds"]
            min_x = min(min_x, b["min_x"])
            min_y = min(min_y, b["min_y"])
            max_x = max(max_x, b["max_x"])
            max_y = max(max_y, b["max_y"])

        # Build merged parsed output
        merged_parsed_path = str(OUTPUT_DIR / "parsed_tracks.json")
        merged = {
            "source_file": ", ".join(valid_files),
            "units": "mm",
            "bounds": {
                "min_x": round(min_x, 4),
                "min_y": round(min_y, 4),
                "max_x": round(max_x, 4),
                "max_y": round(max_y, 4),
                "width": round(max_x - min_x, 4),
                "height": round(max_y - min_y, 4),
            },
            "statistics": {
                "total_tracks": total_parsed_tracks,
                "total_pads": total_parsed_pads,
                "total_arcs": 0,
            },
            "tracks": all_tracks,
            "pads": all_pads,
        }
        with open(merged_parsed_path, 'w') as f:
            json_mod.dump(merged, f, indent=2)

        print(f"[convert] Merged {len(valid_files)} layers: {total_parsed_tracks} tracks, {total_parsed_pads} pads")
        print(f"[convert] Mode: {mode}, Orientation: {orientation}")

        # Step 2: Generate toolpath (mode-dependent), passing orientation
        if mode == "raster":
            toolpath = generate_raster_toolpath(
                merged_parsed_path, toolpath_path,
                line_spacing=line_spacing,
                laser_diameter=laser_diameter,
                orientation=orientation
            )
        else:
            toolpath = generate_toolpath(merged_parsed_path, toolpath_path,
                                         orientation=orientation)

        # Step 3: Generate G-code (with scale and user-controlled settings)
        generate_gcode(toolpath_path, gcode_path, scale=scale,
                       burn_speed=burn_speed, rapid_speed=rapid_speed,
                       laser_power=laser_power)

        # Read generated G-code for stats
        with open(gcode_path, 'r') as f:
            gcode_lines = f.readlines()

        scaled_work_area = {
            "width": round(toolpath["work_area"]["width"] * scale, 2),
            "height": round(toolpath["work_area"]["height"] * scale, 2),
        }

        response_stats = {
            "tracks_parsed": total_parsed_tracks,
            "pads_parsed": total_parsed_pads,
            "toolpath_commands": toolpath["statistics"]["total_commands"],
            "gcode_lines": len(gcode_lines),
            "rapid_distance_mm": round(toolpath["statistics"]["total_rapid_distance_mm"] * scale, 2),
            "draw_distance_mm": round(toolpath["statistics"]["total_draw_distance_mm"] * scale, 2),
            "work_area": scaled_work_area,
            "scale": scale,
            "mode": mode,
            "layers_count": len(valid_files),
            "burn_speed": burn_speed,
            "rapid_speed": rapid_speed,
            "laser_power": laser_power,
        }

        # Add raster-specific stats
        if mode == "raster":
            response_stats["scan_lines"] = toolpath["statistics"].get("scan_lines", 0)
            response_stats["line_spacing"] = line_spacing

        return JSONResponse(content={
            "success": True,
            "source_files": ", ".join(valid_files),
            "stats": response_stats,
            "output_file": "output.gcode",
        })

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"G-code conversion failed: {str(e)}"
        )


@app.get("/download-gcode")
async def download_gcode():
    """
    Download the generated G-code file.
    """
    gcode_path = OUTPUT_DIR / "output.gcode"
    if not gcode_path.exists():
        raise HTTPException(
            status_code=404,
            detail="No G-code file found. Run conversion first."
        )

    return FileResponse(
        path=str(gcode_path),
        filename="output.gcode",
        media_type="text/plain",
    )


# ── Serial USB API ───────────────────────────────────────────────────

from pydantic import BaseModel
class SerialConnectRequest(BaseModel):
    port: str
    baud: int = 115200

class SerialCommandRequest(BaseModel):
    command: str

import asyncio

@app.get("/api/serial/ports")
async def get_serial_ports():
    ports = [port.device for port in serial.tools.list_ports.comports()]
    ports.append("Virtual GRBL (Dummy)")
    return {"ports": ports}

@app.post("/api/serial/connect")
async def connect_serial(req: SerialConnectRequest):
    try:
        await asyncio.to_thread(serial_mgr.connect, req.port, req.baud)
        return {"success": True, "message": f"Connected to {req.port}"}
    except serial.SerialException as e:
        raise HTTPException(status_code=503, detail=f"Device unavailable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/serial/disconnect")
async def disconnect_serial():
    serial_mgr.disconnect()
    return {"success": True}

@app.post("/api/serial/send")
async def send_serial_cmd(req: SerialCommandRequest):
    try:
        response = await asyncio.to_thread(serial_mgr.send_command, req.command)
        return {"success": True, "response": response}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/serial/stream")
async def stream_gcode():
    gcode_path = OUTPUT_DIR / "output.gcode"
    if not gcode_path.exists():
        raise HTTPException(status_code=404, detail="No G-code file found.")
    
    with open(gcode_path, 'r') as f:
        lines = f.readlines()
        
    try:
        serial_mgr.start_stream(lines)
        return {"success": True, "total_lines": len(lines)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/serial/status")
async def get_serial_status():
    return {
        "connected": serial_mgr.serial_port is not None and serial_mgr.serial_port.is_open,
        "streaming": serial_mgr.streaming,
        "total_lines": serial_mgr.total_lines,
        "sent_lines": serial_mgr.sent_lines
    }

@app.post("/api/serial/stop")
async def stop_serial_stream():
    serial_mgr.abort_flag = True
    return {"success": True}

@app.get("/api/serial/position")
async def get_serial_position():
    """
    Query GRBL's current machine position using the '?' real-time status command.
    GRBL responds with: <Idle|MPos:X,Y,Z|...>
    Returns parsed X, Y, Z in mm and the machine state string.
    """
    import re
    if not serial_mgr.serial_port or not serial_mgr.serial_port.is_open:
        raise HTTPException(status_code=400, detail="Not connected")
    if serial_mgr.streaming:
        # Don't interrupt streaming — return last known pos
        return {"x": 0.0, "y": 0.0, "z": 0.0, "state": "Run"}
    try:
        def _query():
            with serial_mgr._lock:
                # '?' is a GRBL real-time command — no newline needed
                serial_mgr.serial_port.write(b"?")
                # GRBL sends the status report immediately on the next cycle
                # Give it up to 1 second to respond
                original = serial_mgr.serial_port.timeout
                serial_mgr.serial_port.timeout = 1.0
                try:
                    resp = serial_mgr.serial_port.readline().decode(errors='replace').strip()
                finally:
                    serial_mgr.serial_port.timeout = original
                return resp

        resp = await asyncio.to_thread(_query)
        # Parse: <Idle|MPos:10.000,25.000,0.000|Bf:15,127|FS:0,0>
        m = re.search(r'MPos:([-\d.]+),([-\d.]+),([-\d.]+)', resp)
        state_m = re.search(r'<(\w+)\|', resp)
        if m:
            return {
                "x": float(m.group(1)),
                "y": float(m.group(2)),
                "z": float(m.group(3)),
                "state": state_m.group(1) if state_m else "Unknown",
                "raw": resp
            }
        else:
            return {"x": 0.0, "y": 0.0, "z": 0.0, "state": "Unknown", "raw": resp}
    except serial.SerialException as e:
        # Physical device was unplugged — auto-disconnect and tell the client
        serial_mgr._handle_disconnect(str(e))
        raise HTTPException(status_code=503, detail=f"Device disconnected: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/serial/stream-events")
async def stream_events():
    """
    Server-Sent Events endpoint.
    The browser subscribes here and receives a JSON event for every G-code
    line sent to the Arduino — enabling real-time preview sync.

    Event format:
      data: {"type": "line", "line": "G1 X23.45 Y14.32 F1000", "sent": 42, "total": 300}
      data: {"type": "done", "sent": 300, "total": 300}
      data: {"type": "abort", "sent": 42, "total": 300}
    """
    import queue as q_module
    from fastapi.responses import StreamingResponse

    subscriber_queue = serial_mgr.subscribe_sse()

    async def event_generator():
        try:
            while True:
                # Poll the thread-safe queue in a non-blocking async way
                try:
                    data = await asyncio.to_thread(subscriber_queue.get, True, 30)
                    yield f"data: {data}\n\n"
                    # If the stream finished or aborted, stop the SSE stream
                    import json
                    parsed = json.loads(data)
                    if parsed.get("type") in ("done", "abort"):
                        break
                except q_module.Empty:
                    # Keepalive ping so the browser connection stays open
                    yield ": keepalive\n\n"
        finally:
            serial_mgr.unsubscribe_sse(subscriber_queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disables nginx buffering
        }
    )


# ── GRBL Parameter API ───────────────────────────────────────────────

class GrblParamChange(BaseModel):
    id: int
    value: float

class GrblSetParamsRequest(BaseModel):
    params: list[GrblParamChange]


@app.get("/api/grbl/params")
async def get_grbl_params():
    """
    Query current GRBL $$ configuration.
    Sends '$$' and parses the multi-line response into a dict.
    """
    import re
    if not serial_mgr.serial_port or not serial_mgr.serial_port.is_open:
        raise HTTPException(status_code=400, detail="Not connected")
    if serial_mgr.streaming:
        raise HTTPException(status_code=409, detail="Cannot read params while streaming")

    try:
        def _query_params():
            with serial_mgr._lock:
                # Drain stale data
                while serial_mgr.serial_port.in_waiting:
                    serial_mgr.serial_port.readline()

                serial_mgr.serial_port.write(b"$$\n")
                import time
                time.sleep(0.5)

                params = {}
                original_timeout = serial_mgr.serial_port.timeout
                serial_mgr.serial_port.timeout = 2.0
                try:
                    while True:
                        line = serial_mgr.serial_port.readline().decode(errors='replace').strip()
                        if not line:
                            break
                        if line == 'ok':
                            break
                        # Parse lines like "$0=10"
                        m = re.match(r'^\$(\d+)=([\d.]+)', line)
                        if m:
                            params[int(m.group(1))] = float(m.group(2))
                finally:
                    serial_mgr.serial_port.timeout = original_timeout
                return params

        params = await asyncio.to_thread(_query_params)
        return {"success": True, "params": params}

    except serial.SerialException as e:
        serial_mgr._handle_disconnect(str(e))
        raise HTTPException(status_code=503, detail=f"Device disconnected: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/grbl/set-params")
async def set_grbl_params(req: GrblSetParamsRequest):
    """
    Send one or more $ID=VALUE commands to the GRBL controller.
    Each parameter is sent individually and its response is captured.
    """
    if not serial_mgr.serial_port or not serial_mgr.serial_port.is_open:
        raise HTTPException(status_code=400, detail="Not connected")
    if serial_mgr.streaming:
        raise HTTPException(status_code=409, detail="Cannot set params while streaming")

    results = []
    try:
        for param in req.params:
            cmd = f"${param.id}={param.value}"
            print(f"[GRBL] Setting parameter: {cmd}")
            response = await asyncio.to_thread(serial_mgr.send_command, cmd)
            success = 'ok' in response.lower()
            results.append({
                "id": param.id,
                "value": param.value,
                "command": cmd,
                "response": response,
                "success": success,
            })
            if not success:
                print(f"[GRBL] Parameter {cmd} failed: {response}")

        all_ok = all(r["success"] for r in results)
        return {
            "success": all_ok,
            "results": results,
            "message": f"Set {sum(1 for r in results if r['success'])}/{len(results)} parameters successfully"
        }

    except serial.SerialException as e:
        serial_mgr._handle_disconnect(str(e))
        raise HTTPException(status_code=503, detail=f"Device disconnected: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def main():
    """Entry point — starts the server and opens the browser."""
    import threading
    import webbrowser
    import uvicorn

    def open_browser():
        webbrowser.open("http://localhost:5050")

    threading.Timer(1.5, open_browser).start()
    uvicorn.run(
        "parser.gerber_preview:app",
        host="127.0.0.1",
        port=5050,
    )


if __name__ == "__main__":
    main()
