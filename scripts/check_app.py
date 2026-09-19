"""Inspect the running local app; optional connection checks never print keys/tokens."""

import argparse
import json
import re
import time

import httpx


class LocalApp:
    def __init__(self, port=8766):
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=30)
        html = self.client.get("/")
        html.raise_for_status()
        token = re.search(r'name="ctrl-token" content="([^\"]+)"', html.text)
        if token is None:
            raise RuntimeError("The local [CTRL] interface did not expose its session token.")
        self.client.headers["X-CTRL-Token"] = token[1]

    def state(self):
        response = self.client.get("/api/state")
        response.raise_for_status()
        return response.json()

    def command(self, name, body=None, wait=True):
        response = self.client.post(f"/api/{name}", json=body or {})
        response.raise_for_status()
        if not wait:
            return response.json()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            state = self.state()
            if not state["busy"]:
                return state
            time.sleep(0.2)
        raise RuntimeError("The local app operation has not finished within 90 seconds.")

    def tabs(self):
        response = self.client.get("/api/tabs")
        response.raise_for_status()
        return response.json()["tabs"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--connect", action="store_true")
    parser.add_argument("--credentials", action="store_true")
    args = parser.parse_args()
    app = LocalApp()
    if args.credentials:
        app.command("credentials")
    if args.connect:
        app.command("connect")
    state = app.state()
    print(json.dumps({key: state.get(key) for key in ("status", "browser", "credentials", "busy", "error")}))
    print(json.dumps({"available_tab_count": len(state.get("tabs", []))}))


if __name__ == "__main__":
    main()
