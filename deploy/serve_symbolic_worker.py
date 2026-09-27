"""Serve only pinned isolated SymPy executions; never expose a Docker API.

Run on the executor host, binding loopback or its private Docker bridge address.
The bearer token file is created with mode 0600 and is never logged.
"""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets

from nima_semantica.calculation import SymbolicExecutionInput, SymbolicWorker
from nima_semantica.providers import strict_json_object


def handler(worker, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.connection.settimeout(10)
            self.close_connection = True
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                self.send_error(403)
                return
            if self.path != "/execute":
                self.send_error(404)
                return
            try:
                if self.headers.get("Transfer-Encoding"):
                    raise ValueError("chunked requests unsupported")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 200000:
                    raise ValueError("invalid request size")
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ValueError("incomplete body")
                request = SymbolicExecutionInput.model_validate(strict_json_object(raw.decode()))
            except Exception:
                self.send_error(400)
                return
            try:
                result = worker.run(request.source, request.timeout_seconds)
                body = json.dumps(result, allow_nan=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                self.send_error(503, "isolated worker failed")
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7871)
    parser.add_argument("--token-file", type=Path, required=True)
    args = parser.parse_args()
    args.token_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(args.token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if args.token_file.is_symlink() or args.token_file.stat().st_mode & 0o077:
            raise ValueError("token file must be private and not a symlink")
        token = args.token_file.read_text().strip()
    else:
        token = secrets.token_urlsafe(48)
        with os.fdopen(fd, "w") as stream:
            stream.write(token)
    if len(token) < 32:
        raise ValueError("worker token too short")
    server = HTTPServer((args.host, args.port), handler(SymbolicWorker(), token))
    print(f"Isolated calculation worker listening on {args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
