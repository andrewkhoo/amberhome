"""Serve the Amber usage pages and proxy date requests to the Amber usage API.

Run: python usage_server.py   then open http://127.0.0.1:8765/

Sites and tokens are read from /etc/amber.env, one [section] per site with
AMBER_SITE and AMBER_TOKEN keys. On start, one SITE-usage.html page is written per
section into this folder, built from page_template.html; each page has a picker to
switch between sites. The tokens stay on the server; the browser only calls
api/usage?site=... relative to the page.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

API = "https://api.amber.com.au/v1/sites/{site}/usage?startDate={start}&endDate={end}"
HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "page_template.html"
ENV_FILE = Path("/etc/amber.env")
HOST = os.environ.get("AMBER_HOST", "127.0.0.1")
PORT = int(os.environ.get("AMBER_PORT", "8765"))

MAX_RANGE_DAYS = 31


def load_sites(path):
    """Return {section: (AMBER_SITE, AMBER_TOKEN)} from an INI-style env file."""
    sections = {}
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            sections[current] = {}
        elif "=" in line and current is not None:
            key, _, value = line.partition("=")
            sections[current][key.strip()] = value.strip()
    sites = {
        name: (vals["AMBER_SITE"], vals["AMBER_TOKEN"])
        for name, vals in sections.items()
        if "AMBER_SITE" in vals and "AMBER_TOKEN" in vals
    }
    if not sites:
        sys.exit(f"no sites with AMBER_SITE and AMBER_TOKEN found in {path}")
    return sites


SITES = load_sites(ENV_FILE)


def render_page(current):
    options = "".join(
        f'<option value="{name}">{name}</option>' for name in SITES
    )
    return (
        TEMPLATE.read_text(encoding="utf-8")
        .replace("__SITE__", current)
        .replace("__OPTIONS__", options)
        .encode("utf-8")
    )


PAGES = {name: render_page(name) for name in SITES}
for name, data in PAGES.items():
    (HERE / f"{name}-usage.html").write_bytes(data)


def fetch_usage(site_name, start, end):
    site_id, token = SITES[site_name]
    req = urllib.request.Request(
        API.format(site=site_id, start=start.isoformat(), end=end.isoformat()),
        headers={"accept": "*/*", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def parse_range(query):
    """Return (start, end) dates from the query string, or raise ValueError with a message."""
    raw_start = query.get("start", [""])[0]
    raw_end = query.get("end", [""])[0]
    try:
        start = date.fromisoformat(raw_start)
        end = date.fromisoformat(raw_end)
    except ValueError:
        raise ValueError("start and end must be YYYY-MM-DD")
    if end < start:
        raise ValueError("end must be on or after start")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise ValueError(f"range is limited to {MAX_RANGE_DAYS} days")
    return start, end


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, ctype):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj), "application/json; charset=utf-8")

    def _site(self, query):
        """Return the site name from ?site=, or None after sending a 400."""
        name = query.get("site", [""])[0]
        if name not in SITES:
            self._json(400, {"error": f"site must be one of: {', '.join(SITES)}"})
            return None
        return name

    def _fetch(self, site_name, start, end):
        try:
            return self._json(200, fetch_usage(site_name, start, end))
        except urllib.error.HTTPError as e:
            return self._json(e.code, {"error": f"Amber API returned HTTP {e.code}"})
        except urllib.error.URLError as e:
            return self._json(502, {"error": f"Amber API unreachable: {e.reason}"})

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(url.query)
        if url.path in ("/", "/index.html"):
            first = next(iter(SITES))
            self.send_response(302)
            self.send_header("Location", f"/{first}-usage.html")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if url.path.startswith("/") and url.path.endswith("-usage.html"):
            name = url.path[1 : -len("-usage.html")]
            if name in PAGES:
                return self._send(200, PAGES[name], "text/html; charset=utf-8")
        if url.path == "/api/usage":
            # Single day: ?site=NAME&date=YYYY-MM-DD
            site_name = self._site(query)
            if site_name is None:
                return
            raw = query.get("date", [""])[0]
            try:
                day = date.fromisoformat(raw)
            except ValueError:
                return self._json(400, {"error": "date must be YYYY-MM-DD"})
            return self._fetch(site_name, day, day)
        if url.path == "/api/usage/range":
            # Date range: ?site=NAME&start=YYYY-MM-DD&end=YYYY-MM-DD (inclusive, max MAX_RANGE_DAYS)
            site_name = self._site(query)
            if site_name is None:
                return
            try:
                start, end = parse_range(query)
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            return self._fetch(site_name, start, end)
        self._send(404, "Not found", "text/plain; charset=utf-8")

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Sites: {', '.join(SITES)}", file=sys.stderr)
    print(f"Serving on http://{HOST}:{PORT}/", file=sys.stderr)
    server.serve_forever()
