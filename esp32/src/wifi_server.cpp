/*
 * wifi_server.cpp — WiFi connection, Web UI, and HTTP handlers
 *
 * Extracted from monolithic main.cpp.
 * Serves the D-pad jog control interface and handles /move, /stop endpoints.
 */

#include "wifi_server.h"
#include "laser.h"
#include "motion.h"
#include "gcode.h"
#include <WebServer.h>
#include <WiFi.h>

// ── FreeRTOS queue (created in main.cpp) ──────────────────────────
extern QueueHandle_t gcodeQueue;

// ── WiFi credentials ──────────────────────────────────────────────
static const char *SSID = "Galaxy_S25";
static const char *PASSWORD = "Hello@123";

// ── Web server instance ───────────────────────────────────────────
static WebServer server(80);

// ── HTML UI (stored in flash) ─────────────────────────────────────
static const char INDEX_HTML[] PROGMEM = R"rawhtml(
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Laser XY Controller</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Rajdhani:wght@400;600&display=swap');

  :root {
    --bg: #0a0a0f;
    --surface: #12121a;
    --border: #1e2a3a;
    --accent: #00e5ff;
    --accent2: #ff3d71;
    --text: #c8d8e8;
    --muted: #4a5a6a;
    --btn-x: #003d7a;
    --btn-x-hover: #0062c4;
    --btn-y: #003a2a;
    --btn-y-hover: #00664a;
    --stop: #5a0010;
    --stop-hover: #c4002a;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Rajdhani', sans-serif;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 24px;
    padding: 24px;
  }

  h1 {
    font-family: 'Share Tech Mono', monospace;
    font-size: 1.1rem;
    letter-spacing: 0.2em;
    color: var(--accent);
    text-transform: uppercase;
    border-bottom: 1px solid var(--border);
    padding-bottom: 10px;
    width: 100%;
    max-width: 380px;
    text-align: center;
  }

  .status-bar {
    font-family: 'Share Tech Mono', monospace;
    font-size: 0.75rem;
    color: var(--muted);
    letter-spacing: 0.1em;
    text-align: center;
  }

  .status-bar span { color: var(--accent); }

  /* ── Step size selector ── */
  .step-row {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 0.85rem;
    color: var(--muted);
    letter-spacing: 0.08em;
  }

  .step-row label { font-family: 'Share Tech Mono', monospace; }

  .step-row select {
    background: var(--surface);
    color: var(--text);
    border: 1px solid var(--border);
    padding: 6px 10px;
    font-family: 'Share Tech Mono', monospace;
    font-size: 0.85rem;
    border-radius: 4px;
    outline: none;
    cursor: pointer;
  }

  /* ── D-pad layout ── */
  .dpad {
    display: grid;
    grid-template-columns: repeat(3, 72px);
    grid-template-rows: repeat(3, 72px);
    gap: 6px;
  }

  .dpad-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--border);
    border-radius: 8px;
    font-size: 1.6rem;
    cursor: pointer;
    user-select: none;
    transition: background 0.12s, transform 0.08s, border-color 0.12s;
    -webkit-tap-highlight-color: transparent;
    position: relative;
    overflow: hidden;
  }

  .dpad-btn::after {
    content: '';
    position: absolute;
    inset: 0;
    background: white;
    opacity: 0;
    transition: opacity 0.15s;
  }

  .dpad-btn:active::after { opacity: 0.07; }
  .dpad-btn:active { transform: scale(0.93); }

  .btn-x  { background: var(--btn-x);  border-color: #005599; }
  .btn-y  { background: var(--btn-y);  border-color: #005533; }
  .btn-stop { background: var(--stop); border-color: #880022; grid-column: 2; grid-row: 2; font-size: 1rem; font-family: 'Share Tech Mono', monospace; color: var(--accent2); letter-spacing: 0.05em; }

  .btn-x:hover  { background: var(--btn-x-hover); border-color: #0088dd; }
  .btn-y:hover  { background: var(--btn-y-hover); border-color: #00aa66; }
  .btn-stop:hover { background: var(--stop-hover); border-color: #ff0044; }

  .axis-label {
    font-family: 'Share Tech Mono', monospace;
    font-size: 0.62rem;
    position: absolute;
    top: 5px;
    right: 7px;
    opacity: 0.5;
    letter-spacing: 0.1em;
  }

  /* ── Laser controls ── */
  .laser-row {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 0.85rem;
    color: var(--muted);
    font-family: 'Share Tech Mono', monospace;
    width: 100%;
    max-width: 380px;
    justify-content: space-between;
  }
  .laser-row input[type="range"] {
    flex-grow: 1;
    margin: 0 10px;
  }
  .btn-laser {
    background: transparent;
    color: #ff3366;
    border: 1px solid #ff3366;
    padding: 6px 12px;
    font-family: 'Share Tech Mono', monospace;
    border-radius: 4px;
    cursor: pointer;
    transition: 0.15s;
  }
  .btn-laser.active {
    background: #ff3366;
    color: #fff;
  }

  /* ── Legend ── */
  .legend {
    display: flex;
    gap: 20px;
    font-size: 0.75rem;
    font-family: 'Share Tech Mono', monospace;
    letter-spacing: 0.08em;
  }

  .legend-item { display: flex; align-items: center; gap: 6px; }

  .dot {
    width: 10px; height: 10px;
    border-radius: 2px;
  }

  .dot-x { background: #0062c4; }
  .dot-y { background: #00664a; }

  /* ── Log ── */
  .log {
    width: 100%;
    max-width: 380px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 10px 12px;
    font-family: 'Share Tech Mono', monospace;
    font-size: 0.72rem;
    color: var(--muted);
    height: 80px;
    overflow-y: auto;
    letter-spacing: 0.05em;
  }

  .log .entry { padding: 1px 0; }
  .log .ok  { color: var(--accent); }
  .log .err { color: var(--accent2); }
</style>
</head>
<body>

<h1>// Laser XY Control</h1>

<div class="status-bar">IP: <span id="ip">connecting...</span> &nbsp;|&nbsp; Steps: <span id="stepcount">0</span></div>

<div class="step-row">
  <label>STEP SIZE</label>
  <select id="stepSize">
    <option value="16">1 step (1/16)</option>
    <option value="80">5 steps</option>
    <option value="160" selected>10 steps</option>
    <option value="320">20 steps</option>
    <option value="800">50 steps</option>
    <option value="1600">100 steps</option>
  </select>
</div>

<div class="laser-row">
  <label>LASER</label>
  <input type="range" id="laserPwr" min="0" max="1000" value="100">
  <span id="laserVal" style="width:40px; text-align:right;">10%</span>
  <button id="laserBtn" class="btn-laser" onclick="toggleLaser()">FIRE</button>
</div>

<div class="dpad">
  <!-- Row 1 -->
  <div></div>
  <div class="dpad-btn btn-y" onclick="move('y', 1)" title="Y+">
    <span class="axis-label">Y</span>&#9650;
  </div>
  <div></div>

  <!-- Row 2 -->
  <div class="dpad-btn btn-x" onclick="move('x', 0)" title="X-">
    <span class="axis-label">X</span>&#9664;
  </div>
  <div class="dpad-btn btn-stop" onclick="stop()" title="STOP">STOP</div>
  <div class="dpad-btn btn-x" onclick="move('x', 1)" title="X+">
    <span class="axis-label">X</span>&#9654;
  </div>

  <!-- Row 3 -->
  <div></div>
  <div class="dpad-btn btn-y" onclick="move('y', 0)" title="Y-">
    <span class="axis-label">Y</span>&#9660;
  </div>
  <div></div>
</div>

<div class="legend">
  <div class="legend-item"><div class="dot dot-x"></div> X axis</div>
  <div class="legend-item"><div class="dot dot-y"></div> Y axis</div>
</div>

<div class="log" id="log"><div class="entry">-- ready --</div></div>

<script>
  let totalSteps = 0;

  document.getElementById('ip').textContent = window.location.hostname;

  function log(msg, type='ok') {
    const el = document.getElementById('log');
    const d = document.createElement('div');
    d.className = 'entry ' + type;
    d.textContent = '> ' + msg;
    el.appendChild(d);
    el.scrollTop = el.scrollHeight;
  }

  document.getElementById('laserPwr').addEventListener('input', function() {
    let p = this.value;
    document.getElementById('laserVal').textContent = Math.round((p/1000)*100) + '%';
  });

  async function toggleLaser() {
    const btn = document.getElementById('laserBtn');
    const isOn = btn.classList.contains('active');
    const pwr = document.getElementById('laserPwr').value;
    
    try {
      const res = await fetch('/laser', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: `state=${isOn ? 0 : 1}&power=${pwr}`
      });
      if (res.ok) {
        if (isOn) {
          btn.classList.remove('active');
          btn.textContent = 'FIRE';
        } else {
          btn.classList.add('active');
          btn.textContent = 'OFF';
        }
        log(isOn ? 'LASER OFF' : 'LASER ON (S' + pwr + ')');
      }
    } catch(e) {
      log('LASER ERR', 'err');
    }
  }

  async function move(axis, dir) {
    const steps = parseInt(document.getElementById('stepSize').value);
    const label = axis.toUpperCase() + (dir ? '+' : '-') + ' x' + steps;
    try {
      const res = await fetch('/move', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: `axis=${axis}&dir=${dir}&steps=${steps}`
      });
      const txt = await res.text();
      totalSteps += steps;
      document.getElementById('stepcount').textContent = totalSteps;
      log(label + ' \u2192 ' + txt);
    } catch(e) {
      log(label + ' FAILED', 'err');
    }
  }

  async function stop() {
    try {
      await fetch('/stop');
      log('STOP sent', 'err');
    } catch(e) {
      log('STOP failed', 'err');
    }
  }
</script>
</body>
</html>
)rawhtml";

// ── HTTP handlers ──────────────────────────────────────────────────

static void handleRoot() { server.send_P(200, "text/html", INDEX_HTML); }

static void handleMove() {
  server.sendHeader("Access-Control-Allow-Origin", "*");

  if (!server.hasArg("axis") || !server.hasArg("dir") ||
      !server.hasArg("steps")) {
    server.send(400, "text/plain", "missing params");
    return;
  }

  String axis = server.arg("axis");
  int dir = server.arg("dir").toInt();
  int steps = server.arg("steps").toInt();

  steps = constrain(steps, 1, 6400); // safety cap: max 2 full revs per command

  setStopFlag(false);

  if (axis == "x") {
    stepMotor(X_STEP_PIN, X_DIR_PIN, dir, steps);
    server.send(200, "text/plain", "X OK");
  } else if (axis == "y") {
    stepMotor(Y_STEP_PIN, Y_DIR_PIN, dir, steps);
    server.send(200, "text/plain", "Y OK");
  } else {
    server.send(400, "text/plain", "unknown axis");
  }
}

static void handleStop() {
  setStopFlag(true);
  turnLaserOff();
  // Flush the queue so pending lines don't execute after stop
  if (gcodeQueue) xQueueReset(gcodeQueue);
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "text/plain", "stopped");
}

static void handleLaser() {
  if (!server.hasArg("state") || !server.hasArg("power")) {
    server.sendHeader("Access-Control-Allow-Origin", "*");
    server.send(400, "text/plain", "missing params");
    return;
  }
  int state = server.arg("state").toInt();
  float power = server.arg("power").toFloat();

  server.sendHeader("Access-Control-Allow-Origin", "*");
  if (state == 1) {
    setLaserPower(power);
    server.send(200, "text/plain", "laser on");
  } else {
    turnLaserOff();
    server.send(200, "text/plain", "laser off");
  }
}

// ── G-code streaming endpoint ─────────────────────────────────────
// Receives a single G-code line and pushes it into the FreeRTOS queue.
// Returns 200 if accepted, 429 if queue is full (client should retry).
static void handleGCode() {
  server.sendHeader("Access-Control-Allow-Origin", "*");

  if (!server.hasArg("line")) {
    server.send(400, "text/plain", "missing 'line' param");
    return;
  }

  String lineStr = server.arg("line");
  if (lineStr.length() == 0 || lineStr.length() > 255) {
    server.send(400, "text/plain", "invalid line length");
    return;
  }

  // Copy into a fixed-size buffer for the queue
  char buf[256];
  lineStr.toCharArray(buf, sizeof(buf));

  // Non-blocking push (timeout = 0). If full, return 429.
  if (xQueueSend(gcodeQueue, buf, 0) == pdTRUE) {
    server.send(200, "text/plain", "ok");
  } else {
    server.send(429, "text/plain", "queue full");
  }
}

// ── Status endpoint ───────────────────────────────────────────────
// Returns current machine position and queue state as JSON.
static void handleStatus() {
  server.sendHeader("Access-Control-Allow-Origin", "*");

  int qFree = gcodeQueue ? uxQueueSpacesAvailable(gcodeQueue) : 0;
  int qTotal = 5;  // GCODE_QUEUE_DEPTH

  String json = "{";
  json += "\"x\":" + String(getPositionX(), 2) + ",";
  json += "\"y\":" + String(getPositionY(), 2) + ",";
  json += "\"queue_free\":" + String(qFree) + ",";
  json += "\"queue_total\":" + String(qTotal) + ",";
  json += "\"finished\":" + String(isGCodeFinished() ? "true" : "false") + ",";
  json += "\"stopped\":" + String(getStopFlag() ? "true" : "false");
  json += "}";

  server.send(200, "application/json", json);
}

// ── CORS preflight handler ────────────────────────────────────────
static void handleCORS() {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.sendHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  server.sendHeader("Access-Control-Allow-Headers", "Content-Type");
  server.send(204);
}

static void handleNotFound() {
  // Handle CORS preflight for any route
  if (server.method() == HTTP_OPTIONS) {
    handleCORS();
    return;
  }
  server.send(404, "text/plain", "not found");
}

// ── Public API ────────────────────────────────────────────────────

void initWiFi() {
  Serial.printf("[wifi] Connecting to: %s\n", SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(SSID, PASSWORD);

  // Set TX power immediately after begin() to reduce current spikes
  WiFi.setTxPower(WIFI_POWER_11dBm);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\n[wifi] Connected! IP: %s\n",
                WiFi.localIP().toString().c_str());
}

void initWebServer() {
  server.on("/", handleRoot);
  server.on("/move", HTTP_POST, handleMove);
  server.on("/stop", handleStop);
  server.on("/laser", HTTP_POST, handleLaser);
  server.on("/gcode", HTTP_POST, handleGCode);
  server.on("/gcode", HTTP_OPTIONS, handleCORS);
  server.on("/status", handleStatus);
  server.onNotFound(handleNotFound);
  server.begin();
  Serial.println("[wifi] Web server started on port 80");
}

void handleServer() { server.handleClient(); }
