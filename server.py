#!/usr/bin/env python3
"""Wireframe agent backend. No dependencies.

  python3 server.py   (settings in .env, see .env.example)

Reads the design system from tds/ in the design-system checkout and sends it to the Pipedream copy of
WireframeArtboardAgent, which still pulls strategy and persona pages from Confluence.
Without PIPEDREAM_TRIGGER_URL it runs in mock mode and returns sample directions.
"""
import json, os, re, shutil, subprocess, tempfile, time, urllib.error, urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler



def load_settings():
    """Reads agent/.env (KEY=VALUE lines) into the environment. The file is git-ignored."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


load_settings()

# Where the design system checkout lives. Set DESIGN_SYSTEM_DIR in .env to point elsewhere.
DESIGN_SYSTEM = os.environ.get("DESIGN_SYSTEM_DIR", os.path.expanduser("~/Projects/thoughtful-design-system"))
TDS = os.path.join(DESIGN_SYSTEM, "tds")
TRIGGER_URL = os.environ.get("PIPEDREAM_TRIGGER_URL", "").strip()
# Set this to the token from the trigger's "Custom token" authorization in Pipedream.
TRIGGER_TOKEN = os.environ.get("PIPEDREAM_TRIGGER_TOKEN", "").strip()
PORT = int(os.environ.get("AGENT_PORT", "5174"))
POLL_SECONDS = 3
POLL_LIMIT_SECONDS = 300


def design_system():
    """The repo's design system, flattened to one stylesheet the same way the deploy does it."""
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(TDS, os.path.join(tmp, "tds"))
        subprocess.run(
            ["python3", os.path.join(DESIGN_SYSTEM, "tools/build_bundle.py"), "--in-place", tmp],
            check=True, capture_output=True,
        )
        with open(os.path.join(tmp, "tds/tds.css"), encoding="utf-8") as f:
            css = f.read()
    with open(os.path.join(TDS, "index.json"), encoding="utf-8") as f:
        components = json.load(f)["components"]
    # The class names the wireframe designer may use, read from the stylesheet.
    classes = sorted(set(re.findall(r"\.([a-zA-Z][\w-]*)", css)))
    return {"css": css, "components": components, "classes": classes}


def pipedream(agent_input):
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


def directions(payload):
    if not TRIGGER_URL:
        return mock_directions()
    ds = design_system()
    status, data = pipedream({
        "mode": "directions",
        "briefUrl": payload.get("briefUrl") or "",
        "briefContent": payload.get("briefContent") or "",
        "briefTitle": payload.get("briefTitle") or "",
        "persona": payload.get("persona") or "zenia",
        # Read by the Pipedream copy's fetch_resources in place of the Confluence TDS pages.
        "tdsCss": ds["css"],
        "tdsClasses": "\n".join(ds["classes"]),
    })
    if status == 202 and data.get("jobId"):
        return poll_job(data["jobId"])
    return data


def poll_job(job_id):
    deadline = time.time() + POLL_LIMIT_SECONDS
    while time.time() < deadline:
        time.sleep(POLL_SECONDS)
        _, entry = pipedream({"mode": "get-job", "jobId": job_id})
        if entry.get("status") == "complete":
            return entry["result"]
        if entry.get("status") == "error":
            raise RuntimeError(entry.get("error", "job failed"))
    # The Pipedream copy leaves the job at "processing" if it errors before writing a result.
    raise TimeoutError(f"job {job_id} did not finish in {POLL_LIMIT_SECONDS}s")


def mock_directions():
    def screen(name, color):
        html = (
            f'<div class="screen" style="background:{color};padding:24px;font-family:Inter,sans-serif">'
            f"<h2>{name}</h2><p>Mock screen. Set PIPEDREAM_TRIGGER_URL for real output.</p></div>"
        )
        return {"id": name.lower().replace(" ", "-"), "name": name, "description": "mock", "html": html}

    return {
        "mode": "directions",
        "mock": True,
        "title": "Mock directions",
        "directions": [
            {"name": f"Direction {i + 1}", "description": "mock premise",
             "screens": [screen("Welcome", c), screen("Booking", c)]}
            for i, c in enumerate(["#1f0e41", "#390c81", "#352654"])
        ],
    }


class Handler(BaseHTTPRequestHandler):
    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/design-system":
            return self.send_json(design_system())
        self.send_error(404)

    def do_POST(self):
        if self.path != "/api/directions":
            return self.send_error(404)
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            self.send_json(directions(payload))
        except Exception as e:
            self.send_json({"error": str(e)}, 502)

    def log_message(self, fmt, *args):
        print(fmt % args)


if __name__ == "__main__":
    mode = "pipedream" if TRIGGER_URL else "mock"
    print(f"agent backend on http://127.0.0.1:{PORT} ({mode} mode)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
