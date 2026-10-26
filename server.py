#!/usr/bin/env python3
"""Wireframe agent: local server for the review page. No dependencies.

  python3 server.py   (settings in .env, see .env.example)

Serves the review page at / and the design system's source stylesheets at /tds/.
Generation requests (directions, build, iterate) go to the Pipedream trigger, which calls Bedrock.
Set AGENT_MOCK=1 only to check the page layout without calling Pipedream.
"""
import json, mimetypes, os, time, urllib.error, urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(HERE, "ui")


def load_settings():
    """Reads .env (KEY=VALUE lines) next to this file into the environment. The file is git-ignored."""
    path = os.path.join(HERE, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


load_settings()

# The design-system checkout. Set DESIGN_SYSTEM_DIR in .env to point elsewhere.
DESIGN_SYSTEM = os.environ.get("DESIGN_SYSTEM_DIR", os.path.expanduser("~/Projects/thoughtful-design-system"))
TDS = os.path.join(DESIGN_SYSTEM, "tds")
TRIGGER_URL = os.environ.get("PIPEDREAM_TRIGGER_URL", "").strip()
# The token from the trigger's "Custom token" authorization in Pipedream.
TRIGGER_TOKEN = os.environ.get("PIPEDREAM_TRIGGER_TOKEN", "").strip()
MOCK = os.environ.get("AGENT_MOCK") == "1"
PORT = int(os.environ.get("AGENT_PORT", "5174"))
POLL_SECONDS = 3
POLL_LIMIT_SECONDS = 420

# Fields the Pipedream flow reads from agentInput. Anything else from the page is dropped.
FLOW_FIELDS = (
    "mode", "briefUrl", "briefContent", "briefTitle", "persona",
    "selectedDirections", "direction", "screenComments", "generalComment",
)

mimetypes.add_type("font/woff2", ".woff2")


def pipedream(agent_input):
    if not TRIGGER_URL:
        raise RuntimeError("PIPEDREAM_TRIGGER_URL is not set. Add it to agent/.env (see .env.example).")
    body = json.dumps({"agentInput": agent_input}).encode()
    headers = {"Content-Type": "application/json"}
    if TRIGGER_TOKEN:
        headers["Authorization"] = "Bearer " + TRIGGER_TOKEN
    req = urllib.request.Request(TRIGGER_URL, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=300) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Pipedream returned {e.code}: {e.read().decode(errors='replace')[:300]}")


def poll_job(job_id):
    deadline = time.time() + POLL_LIMIT_SECONDS
    while time.time() < deadline:
        time.sleep(POLL_SECONDS)
        _, entry = pipedream({"mode": "get-job", "jobId": job_id})
        if entry.get("status") == "complete":
            return normalize(entry.get("result", entry))
        if entry.get("status") == "error":
            raise RuntimeError(entry.get("error", "job failed"))
    # The flow leaves the job at "processing" if it fails before writing a result.
    raise TimeoutError(f"job {job_id} did not finish in {POLL_LIMIT_SECONDS}s")


def generate(payload):
    agent_input = {k: payload[k] for k in FLOW_FIELDS if k in payload}
    if MOCK:
        return mock_result(agent_input)
    status, data = pipedream(agent_input)
    if status == 202 and data.get("jobId"):
        return poll_job(data["jobId"])
    return normalize(data)


def normalize(result):
    """The original flow wraps build and iterate screens as {output: {screens}}; directions are flat.
    Return the same flat shape either way."""
    if isinstance(result, dict) and isinstance(result.get("output"), dict):
        return {**result["output"], "mode": result.get("mode"), "featureTitle": result.get("featureTitle")}
    return result


def mock_result(agent_input):
    """Only with AGENT_MOCK=1: placeholder output for checking the page layout."""
    mode = agent_input.get("mode", "directions")
    if mode == "directions":
        return {
            "mode": "directions", "mock": True, "title": "Mock directions",
            "directions": [
                {"name": f"Direction {i + 1}", "description": "Mock premise.",
                 "screens": [
                     {"id": "welcome", "name": "Welcome", "device": "mobile",
                      "html": '<div data-el="frame" style="min-height:100%;padding:24px;background:#1f0e41;color:#fff"><h2 data-el="headline">Welcome</h2><button data-el="primary-cta">Continue</button></div>'},
                 ]}
                for i in range(3)
            ],
        }
    source = (agent_input.get("selectedDirections") or [agent_input.get("direction") or {}])[0]
    return {"mode": mode, "mock": True, "screens": source.get("screens", [])}


class Handler(BaseHTTPRequestHandler):
    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path):
        if not os.path.isfile(path):
            return self.send_error(404)
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self.send_file(os.path.join(UI_DIR, "index.html"))
        if self.path == "/api/status":
            return self.send_json({"pipedream": bool(TRIGGER_URL), "mock": MOCK})
        if self.path.startswith("/tds/"):
            # Source stylesheets, loaded the same way the editor loads them.
            rel = os.path.normpath(self.path[len("/tds/"):])
            if rel.startswith(".."):
                return self.send_error(403)
            return self.send_file(os.path.join(TDS, rel))
        self.send_error(404)

    def do_POST(self):
        if self.path != "/api/generate":
            return self.send_error(404)
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            self.send_json(generate(payload))
        except Exception as e:
            self.send_json({"error": str(e)}, 502)

    def log_message(self, fmt, *args):
        print(fmt % args)


if __name__ == "__main__":
    mode = "mock" if MOCK else ("pipedream" if TRIGGER_URL else "not configured")
    print(f"wireframe agent on http://127.0.0.1:{PORT} ({mode})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
