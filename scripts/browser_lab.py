"""Synthetic browser lab. Serve locally, then explicitly authorize a Jev smoke run.

python scripts/browser_lab.py serve
python scripts/browser_lab.py smoke --run --scenario catalog
python scripts/browser_lab.py smoke --run --scenario inbox-small
python scripts/browser_lab.py smoke --run --scenario inbox-100

The driver submits natural-language goals through the existing CTRL HTTP API.
It never connects to CDP or supplies a prepared browser action sequence. The
separate validation endpoint measures submitted filters and bodies rendered by
the browser. Coverage evidence does not prove semantic understanding.
"""

import argparse
import html
import json
import math
import re
import secrets
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

LAB_URL = "http://127.0.0.1:8877"
APP_URL = "http://127.0.0.1:8766"
PRODUCTS = [
    {"id": "alpine-trail-mug", "name": "Alpine Trail Mug", "department": "outdoors", "stock": True, "price": "$18"},
    {"id": "trail-map-case", "name": "Trail Map Case", "department": "outdoors", "stock": False, "price": "$12"},
    {"id": "forest-trail-guide", "name": "Forest Trail Guide", "department": "books", "stock": True, "price": "$24"},
    {"id": "camp-lantern", "name": "Camp Lantern", "department": "outdoors", "stock": True, "price": "$31"},
    {"id": "trail-desk-print", "name": "Trail Desk Print", "department": "home", "stock": True, "price": "$16"},
]
STYLE = """
body{font:17px/1.5 system-ui,sans-serif;max-width:960px;margin:24px auto;padding:0 20px;
color:#162632;background:#f5f8fa}
header,main{background:white;border:1px solid #d9e2e8;padding:20px;border-radius:10px;margin-bottom:16px}
h1{font-size:26px;margin:4px 0 12px}h2{font-size:20px}a{color:#075c9d}label{display:block;margin:12px 0}
input[type=search],select{font:inherit;padding:8px;min-width:260px;margin-left:10px}
button{font:inherit;padding:8px 18px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:9px;border-bottom:1px solid #e0e8ed}
nav{display:flex;gap:24px;margin:16px 0}.muted{color:#566978}.read{color:#16673c}.body{padding:16px;background:#eef6fa}
"""
VIEW_SCRIPT = """window.addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
const body=document.querySelector('[data-view-token]');
if(body){const r=body.getBoundingClientRect();
if(r.width>0&&r.height>0&&r.top<innerHeight&&r.bottom>0){
fetch('/api/view',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({nonce:body.dataset.viewToken}),keepalive:true});}}
"""


def escape(value):
    return html.escape(str(value), quote=True)


def page(title, content):
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)} · CTRL synthetic lab</title><style>{STYLE}</style></head><body>"
        "<header><strong>CTRL synthetic browser lab</strong><div class='muted'>"
        "Local test data only. No real accounts, messages, purchases, or external requests.</div></header>"
        f"<main>{content}</main><script src='/lab.js'></script></body></html>"
    )


class LabState:
    def __init__(self):
        self.lock = threading.RLock()
        self.reset()

    def reset(self, message_count=100, page_size=10):
        if type(message_count) is not int or not 1 <= message_count <= 200:
            raise ValueError("message_count must be between 1 and 200")
        if type(page_size) is not int or not 1 <= page_size <= 20:
            raise ValueError("page_size must be between 1 and 20")
        with self.lock:
            self.session = secrets.token_hex(8)
            self.message_count, self.page_size = message_count, page_size
            self.catalog_submissions = []
            self.catalog_opened, self.catalog_viewed = set(), set()
            self.inbox_pages, self.message_opened, self.message_viewed = set(), set(), set()
            self.tokens = {}

    def view_token(self, kind, identifier):
        with self.lock:
            nonce = secrets.token_urlsafe(18)
            self.tokens[nonce] = (kind, identifier)
            return nonce

    def record_view(self, nonce):
        with self.lock:
            if not isinstance(nonce, str) or nonce not in self.tokens:
                raise ValueError("Invalid or already consumed view token")
            kind, identifier = self.tokens.pop(nonce)
            (self.message_viewed if kind == "message" else self.catalog_viewed).add(identifier)

    def validation(self):
        with self.lock:
            return {
                "session": self.session,
                "catalog": {
                    "submissions": list(self.catalog_submissions),
                    "opened_items": sorted(self.catalog_opened),
                    "body_view_items": sorted(self.catalog_viewed),
                },
                "inbox": {
                    "message_count": self.message_count,
                    "page_size": self.page_size,
                    "list_pages": sorted(self.inbox_pages),
                    "opened_unique": len(self.message_opened),
                    "body_view_unique": len(self.message_viewed),
                    "body_view_ids": sorted(self.message_viewed),
                },
            }


def catalog_page(state, query):
    search = query.get("q", [""])[0][:100]
    department = query.get("department", ["all"])[0]
    if department not in {"all", "outdoors", "books", "home"}:
        raise ValueError("Unknown department")
    stock = query.get("stock", [""])[0] == "1"
    if query.get("searched") == ["1"]:
        state.catalog_submissions.append({"query": search, "department": department, "stock_only": stock})
    options = "".join(
        f"<option value='{value}'{' selected' if department == value else ''}>{label}</option>"
        for value, label in [("all", "All departments"), ("outdoors", "Outdoors"), ("books", "Books"), ("home", "Home")]
    )
    products = [
        item
        for item in PRODUCTS
        if search.lower() in item["name"].lower()
        and (department == "all" or item["department"] == department)
        and (not stock or item["stock"])
    ]
    rows = "".join(
        f"<tr><td><a href='/catalog/item/{item['id']}'>{escape(item['name'])}</a></td>"
        f"<td>{escape(item['department'])}</td><td>{'In stock' if item['stock'] else 'Out of stock'}</td>"
        f"<td>{item['price']}</td></tr>"
        for item in products
    )
    return page(
        "Catalog",
        f"<h1>Catalog search</h1><form method='get' action='/catalog'>"
        "<input type='hidden' name='searched' value='1'>"
        f"<label>Search catalog<input type='search' name='q' value='{escape(search)}'></label>"
        f"<label>Department<select name='department'>{options}</select></label>"
        f"<label><input type='checkbox' name='stock' value='1'{' checked' if stock else ''}> In stock only</label>"
        "<button type='submit'>Search catalog</button></form>"
        f"<h2>{len(products)} products</h2><table><thead><tr><th>Product</th><th>Department</th>"
        f"<th>Availability</th><th>Price</th></tr></thead><tbody>{rows}</tbody></table>",
    )


def inbox_page(state, query):
    pages = math.ceil(state.message_count / state.page_size)
    number = int(query.get("page", ["1"])[0])
    if not 1 <= number <= pages:
        raise ValueError("Inbox page out of range")
    state.inbox_pages.add(number)
    first, last = (number - 1) * state.page_size + 1, min(number * state.page_size, state.message_count)
    rows = "".join(
        f"<tr><td><a href='/inbox/message/{identifier}'>Open message {identifier:03d}</a></td>"
        f"<td>Synthetic shipping update {identifier:03d}</td><td class='read'>"
        f"{'Read' if identifier in state.message_viewed else 'Unread'}</td></tr>"
        for identifier in range(first, last + 1)
    )
    links = f"<a href='/inbox?page={number - 1}'>Previous page</a>" if number > 1 else ""
    links += f"<a href='/inbox?page={number + 1}'>Next page</a>" if number < pages else ""
    return page(
        "Inbox",
        f"<h1>Synthetic inbox</h1><p>{state.message_count} messages total. "
        f"{len(state.message_viewed)} read. Page {number} of {pages}; messages {first}–{last}.</p>"
        "<p>Open a message to read its body. Read markers remain visible when you return.</p>"
        f"<nav>{links}</nav><table><thead><tr><th>Open</th><th>Subject</th><th>Status</th></tr></thead>"
        f"<tbody>{rows}</tbody></table><nav>{links}</nav>",
    )


def make_lab_server(port=8877, state=None):
    state = state or LabState()

    class Handler(BaseHTTPRequestHandler):
        def send(self, status, body, mime="text/html"):
            if not isinstance(body, (str, bytes)):
                body, mime = json.dumps(body), "application/json"
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(status)
            for name, value in {
                "Content-Type": mime + "; charset=utf-8",
                "Content-Length": str(len(data)),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            }.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def local(self):
            expected = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != expected or self.headers.get("Origin") not in (None, f"http://{expected}"):
                self.send(403, {"error": "Local synthetic lab requests only"})
                return False
            return True

        def do_GET(self):
            if not self.local():
                return
            route = urlsplit(self.path)
            try:
                with state.lock:
                    if route.path == "/health":
                        return self.send(200, {"app": "CTRL synthetic browser lab", "status": "ok"})
                    if route.path == "/validation":
                        return self.send(200, state.validation())
                    if route.path == "/lab.js":
                        return self.send(200, VIEW_SCRIPT, "text/javascript")
                    if route.path == "/":
                        return self.send(
                            200,
                            page(
                                "Home",
                                "<h1>Choose a practice area</h1><nav>"
                                "<a href='/catalog'>Catalog search</a><a href='/inbox'>Synthetic inbox</a></nav>",
                            ),
                        )
                    if route.path == "/catalog":
                        return self.send(200, catalog_page(state, parse_qs(route.query)))
                    if route.path == "/inbox":
                        return self.send(200, inbox_page(state, parse_qs(route.query)))
                    if route.path.startswith("/catalog/item/"):
                        identifier = route.path.removeprefix("/catalog/item/")
                        item = next((item for item in PRODUCTS if item["id"] == identifier), None)
                        if item is None:
                            return self.send(404, {"error": "Unknown synthetic product"})
                        state.catalog_opened.add(identifier)
                        nonce = state.view_token("product", identifier)
                        return self.send(
                            200,
                            page(
                                item["name"],
                                f"<h1>{escape(item['name'])}</h1><section class='body' data-view-token='{nonce}'>"
                                f"<h2>Product details</h2><p>Department: {escape(item['department'])}. "
                                f"Price: {item['price']}. "
                                f"{'In stock' if item['stock'] else 'Out of stock'}.</p>"
                                "<p>Synthetic product details. No ordering or payment is available.</p>"
                                f"<p>Reference: LAB-{escape(identifier.upper())}</p></section>"
                                "<nav><a href='/catalog'>Back to catalog</a></nav>",
                            ),
                        )
                    if route.path.startswith("/inbox/message/"):
                        identifier = int(route.path.removeprefix("/inbox/message/"))
                        if not 1 <= identifier <= state.message_count:
                            return self.send(404, {"error": "Unknown synthetic message"})
                        state.message_opened.add(identifier)
                        nonce = state.view_token("message", identifier)
                        number = (identifier - 1) // state.page_size + 1
                        return self.send(
                            200,
                            page(
                                f"Message {identifier:03d}",
                                f"<h1>Message {identifier:03d}: Synthetic shipping update</h1>"
                                "<p class='muted'>From: example-sender.invalid · Synthetic message</p>"
                                f"<section class='body' data-view-token='{nonce}'><h2>Message body</h2>"
                                f"<p>Shipment LAB-{identifier:03d} is scheduled for day {(identifier % 28) + 1}. "
                                f"It contains {(identifier % 7) + 1} sample units "
                                f"for warehouse {(identifier % 4) + 1}.</p>"
                                f"<p>Evidence code: M{identifier:03d}-{(identifier * 7919) % 10000:04d}.</p>"
                                "<p>This is fictional test content, with no real account data.</p></section>"
                                f"<nav><a href='/inbox?page={number}'>Back to inbox page {number}</a></nav>",
                            ),
                        )
                    self.send(404, {"error": "Not found"})
            except ValueError as error:
                self.send(400, {"error": str(error)})

        def do_POST(self):
            if not self.local():
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 2000:
                    raise ValueError("Invalid request size")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("Expected an object")
                if self.path == "/api/reset":
                    state.reset(body.get("message_count", 100), body.get("page_size", 10))
                elif self.path == "/api/view":
                    state.record_view(body.get("nonce"))
                else:
                    return self.send(404, {"error": "Not found"})
                self.send(200, {"ok": True})
            except (ValueError, TypeError) as error:
                self.send(400, {"error": str(error)})

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.lab_state = state
    return server


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, _request, _response, _code, _message, _headers, _new_url):
        raise RuntimeError("The local lab runner does not follow HTTP redirects")


class LocalHttp:
    def __init__(self, base_url):
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username or parsed.password:
            raise ValueError("The lab runner only connects to HTTP on 127.0.0.1")
        self.base_url = base_url.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.token = None

    def request(self, path, body=None):
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-CTRL-Token"] = self.token
        request = urllib.request.Request(
            self.base_url + path, headers=headers, data=json.dumps(body).encode() if body is not None else None
        )
        with self.opener.open(request, timeout=20) as response:
            content = response.read().decode("utf-8")
            return json.loads(content) if "application/json" in response.headers.get("Content-Type", "") else content

    def authenticate_app(self):
        match = re.search(r"<meta\s+name=[\"\']ctrl-token[\"\']\s+content=[\"\']([^\"\']+)", self.request("/"))
        if not match:
            raise RuntimeError("CTRL token was not found; start the existing local CTRL app first")
        self.token = match[1]


def validate_result(scenario, evidence):
    if scenario == "catalog":
        expected = {"query": "trail", "department": "outdoors", "stock_only": True}
        return (
            expected in evidence["catalog"]["submissions"]
            and "alpine-trail-mug" in evidence["catalog"]["body_view_items"]
        )
    inbox = evidence["inbox"]
    return inbox["body_view_unique"] == inbox["message_count"] and len(inbox["list_pages"]) == math.ceil(
        inbox["message_count"] / inbox["page_size"]
    )


def run_smoke(scenario, timeout=900, target_id=None):
    if scenario not in {"catalog", "inbox-small", "inbox-100"}:
        raise ValueError("Unknown browser lab scenario")
    if timeout <= 0:
        raise ValueError("The lab timeout must be positive")
    lab, app = LocalHttp(LAB_URL), LocalHttp(APP_URL)
    app.authenticate_app()
    state = app.request("/api/state")
    if state.get("busy") or state.get("running"):
        raise RuntimeError("CTRL is already busy; pause it before starting a lab run")
    if not state.get("browser", {}).get("connected"):
        raise RuntimeError("Connect Chrome in the existing CTRL app; the lab never opens a second CDP connection")
    count, page_size = (12, 5) if scenario == "inbox-small" else (100, 10)
    lab.request("/api/reset", {"message_count": count, "page_size": page_size})
    if scenario == "catalog":
        goal = (
            f'Open {LAB_URL}/catalog. Search the catalog for "trail", choose Outdoors as the department, '
            "enable In stock only, apply the search, and open Alpine Trail Mug to read its product details."
        )
        limit = 25
    else:
        goal = (
            f"Open {LAB_URL}/inbox. Read every one of the {count} synthetic messages by opening each message "
            "and reading its body. Use the inbox Read markers to track progress and continue through all pages. "
            "Finish only when all messages are marked Read. Do not use the catalog."
        )
        limit = min(500, count * 3 + 25)
    started = time.monotonic()
    accepted = False
    try:
        app.request("/api/start", {"goal": goal, "target_id": target_id, "values": [], "run": True, "max_steps": limit})
        accepted = True
        while True:
            time.sleep(0.5)
            state = app.request("/api/state")
            if not state.get("busy") and state.get("status") in {
                "done", "blocked", "needs_input", "error", "paused", "cancelled",
            }:
                break
            if time.monotonic() - started > timeout:
                raise TimeoutError("Lab run timed out")
    except (Exception, KeyboardInterrupt):
        if accepted:
            try:
                app.request("/api/pause", {})
            except Exception:
                pass
        raise
    time.sleep(0.2)  # Let a final keepalive body-view notification finish.
    evidence = lab.request("/validation")
    return {
        "scenario": scenario,
        "validated": validate_result(scenario, evidence),
        "passed": validate_result(scenario, evidence) and state.get("status") == "done" and not state.get("error"),
        "agent_status": state.get("status"),
        "stop_reason": state.get("stop_reason"),
        "error": state.get("error"),
        "actions": len(state.get("history", [])),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "evidence": evidence,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("serve", help="Serve synthetic pages on 127.0.0.1:8877")
    smoke = subparsers.add_parser("smoke", help="Run a task through the existing CTRL app")
    smoke.add_argument("--run", action="store_true", help="Required to run the browser agent")
    smoke.add_argument("--scenario", choices=["catalog", "inbox-small", "inbox-100"], default="catalog")
    smoke.add_argument("--timeout", type=int, default=900)
    smoke.add_argument("--target-id")
    args = parser.parse_args()
    if args.command == "serve":
        server = make_lab_server()
        print(f"Synthetic browser lab ready at {LAB_URL}", flush=True)
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return
    if not args.run:
        parser.error("smoke requires --run; no browser work was started")
    result = run_smoke(args.scenario, args.timeout, args.target_id)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
