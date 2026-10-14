#!/usr/bin/env python3
"""Run the customer-resolution app and supervised durable worker."""
import argparse
import json
import mimetypes
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import appcore as core


def supervise(stop):
    while not stop.is_set():
        process = subprocess.Popen([sys.executable, __file__, "--worker"], cwd=core.ROOT)
        with core.connect("runtime") as db:
            old = core.get(db, "meta", "worker") or {}
            core.put(db, "meta", "worker", {"pid": process.pid, "previous_pid": old.get("pid"), "boot": old.get("boot", 0) + 1, "started": time.time()})
        while process.poll() is None and not stop.wait(.5):
            pass
        if stop.is_set():
            process.terminate()
            process.wait(timeout=10)
        else:
            stop.wait(.5)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def response(self, status, data, content_type="application/json"):
        encoded = json.dumps(data).encode() if content_type == "application/json" else data
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        try:
            self.end_headers()
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            # A closed recording/browser tab does not undo committed work.
            pass

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in {"/api/state", "/api/export"}:
            return self.response(200, core.state())
        local = core.ROOT / "web" / ("index.html" if path == "/" else path.lstrip("/"))
        if local.resolve().is_relative_to((core.ROOT / "web").resolve()) and local.is_file():
            return self.response(200, local.read_bytes(), mimetypes.guess_type(local)[0] or "application/octet-stream")
        return self.response(404, {"error": "Not found"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 100_000:
                raise ValueError("Request too large")
            body = json.loads(self.rfile.read(length) or b"{}")
            path = urlsplit(self.path).path
            if path == "/api/start":
                result = core.start(body["case_id"], body.get("mode", "reference"))
            elif path == "/api/retry":
                result = core.retry_plan(body["case_id"], body["mode"], body["version"])
            elif path == "/api/reset":
                result = core.reset()
            elif path == "/api/approve":
                result = core.approve(body["case_id"], body["version"])
            elif path == "/api/check":
                result = core.check(body["case_id"])
                with core.connect("runtime") as db:
                    case = core.get(db, "case", body["case_id"])
                    case["check"] = result
                    if result["status"] == "PASS":
                        case["status"] = "RESOLVED"
                    elif case["status"] in {"RESOLVED", "HANDED_OFF"}:
                        case["status"] = "BLOCKED"
                    core.event(case, "Closure requested: " + result["status"], "Separate checker", result)
                    core.put(db, "case", case["id"], case)
            elif path == "/api/handoff":
                case_id = body["case_id"]
                with core.connect("runtime") as runtime, core.connect("world") as world:
                    case = core.get(runtime, "case", case_id)
                    if case["status"] != "ESCALATED":
                        raise ValueError("Only escalated cases can be handed off")
                    handoff = {"case_id": case_id, "owner": "Support Lead", "accepted": True, "at": time.time(), "actor": "Presenter as simulated support lead"}
                    core.put(world, "handoff", case_id, handoff)
                    world.commit()
                    core.event(case, "Named support lead accepted the investigation", "Support Lead", handoff)
                    core.put(runtime, "case", case_id, case)
                result = core.check(case_id)
                with core.connect("runtime") as db:
                    case = core.get(db, "case", case_id)
                    case["check"] = result
                    case["status"] = "HANDED_OFF" if result["status"] == "PASS" else "BLOCKED"
                    core.put(db, "case", case_id, case)
            elif path == "/api/console":
                kind = body["kind"]
                if kind == "crash":
                    with core.connect("runtime") as db:
                        core.put(db, "meta", "crash", {"armed": True, "at": time.time()})
                    result = {"armed": True, "description": "Actual worker exit after the next committed business write, before acknowledgement"}
                elif kind == "price":
                    with core.connect("world") as db:
                        item = core.get(db, "inventory", "BLEND-01")
                        item["cost"] += 50_000
                        item["version"] += 1
                        core.put(db, "inventory", item["sku"], item)
                    result = item
                elif kind == "corrupt":
                    with core.connect("world") as db:
                        refunds = core.rows(db, "refund")
                        shipments = core.rows(db, "shipment")
                        if refunds:
                            item = refunds[0]
                            item["amount"] += 100
                            core.put(db, "refund", item["reference"], item)
                        elif shipments:
                            item = shipments[0]
                            item["status"] = "missing"
                            core.put(db, "shipment", item["reference"], item)
                        else:
                            raise ValueError("Resolve a case before corrupting a receipt")
                    result = {"changed": True, "description": "Reviewer altered a saved source record"}
                else:
                    raise ValueError("Unknown reviewer challenge")
            else:
                return self.response(404, {"error": "Not found"})
            self.response(200, result)
        except (KeyError, ValueError, TypeError) as exc:
            self.response(400, {"error": str(exc)})
        except Exception as exc:
            self.response(500, {"error": str(exc)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8893)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    core.seed()
    if args.worker:
        while True:
            try:
                core.step()
            except Exception as exc:
                print("Worker operation blocked:", type(exc).__name__, str(exc), flush=True)
            time.sleep(.4)
    else:
        stop = threading.Event()
        thread = threading.Thread(target=supervise, args=(stop,), daemon=True)
        thread.start()
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
        print(f"Customer Resolution: http://127.0.0.1:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            server.server_close()
            thread.join(timeout=12)


if __name__ == "__main__":
    main()
