"""Authenticated private Lean endpoint; pinned config is selected only at startup."""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
import json
import os
import secrets
import time
from pathlib import Path

from nima_semantica.lean_transport import local_lean_verifier, execute_packet, response_limit
from nima_semantica.lean_verification_service import LeanVerificationService
from nima_semantica.providers import strict_json_object


def handler(verifier,token,jobs=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass

        def authorized(self):
            self.connection.settimeout(10);self.close_connection=True
            if not hmac.compare_digest(self.headers.get("Authorization",""),"Bearer "+token):
                self.send_error(403);return False
            return True

        def reply(self,value):
            body=json.dumps(value,allow_nan=False).encode()
            if len(body)>response_limit(getattr(verifier, "policy", None)):raise ValueError("response limit")
            self.send_response(200);self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)

        def do_GET(self):
            if not self.authorized():return
            if self.path == "/manifest":
                self.reply(LeanVerificationService(None,verifier).environment_manifest()); return
            parsed = urlsplit(self.path)
            try:
                identifier = parse_qs(parsed.query)["id"][0]
                if jobs is None: raise ValueError("durable operations unavailable")
                if parsed.path == "/operations/status": self.reply(jobs.status(identifier))
                elif parsed.path == "/operations/result": self.reply(jobs.result(identifier))
                else: self.send_error(404)
            except (ValueError, KeyError): self.send_error(400)

        def do_POST(self):
            if not self.authorized():return
            if self.path not in {"/verify", "/operations/submit", "/operations/cancel"}:self.send_error(404);return
            try:
                size=int(self.headers.get("Content-Length","0"))
                if self.headers.get("Transfer-Encoding") or not 0<size<=16*1024*1024:raise ValueError("request framing")
                raw=self.rfile.read(size)
                if len(raw)!=size:raise ValueError("incomplete body")
                payload=strict_json_object(raw.decode())
            except Exception:
                self.send_error(400);return
            try:
                if self.path == "/verify":
                    if jobs is None:
                        self.reply(execute_packet(verifier,payload))
                    else:
                        from nima_semantica.models import identity
                        identifier = "sync-" + identity(payload)
                        jobs.submit(identifier, payload)
                        deadline = time.monotonic() + jobs.policy.transport_seconds
                        while time.monotonic() < deadline:
                            result = jobs.result(identifier)
                            if result["packet"] is not None:
                                self.reply(result["packet"]); break
                            if result["status"]["state"] not in {"queued", "running", "cancel_requested"}:
                                raise ValueError("synchronous Lean job failed: " + str(result["status"]["failure_layer"]))
                            time.sleep(.2)
                        else: raise ValueError("synchronous Lean delivery deadline")
                elif jobs is None: self.send_error(404)
                elif self.path == "/operations/submit":
                    if set(payload) != {"operation_id", "payload"}: raise ValueError("invalid submit")
                    self.reply(jobs.submit(payload["operation_id"], payload["payload"]))
                else:
                    if set(payload) != {"operation_id"}: raise ValueError("invalid cancellation")
                    self.reply(jobs.cancel(payload["operation_id"]))
            except Exception:self.send_error(503,"Lean verification transport failed")
    return Handler


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host",default="127.0.0.1");parser.add_argument("--port",type=int,default=7873)
    parser.add_argument("--config",required=True,type=Path);parser.add_argument("--token-file",required=True,type=Path)
    parser.add_argument("--jobs-database", type=Path)
    args=parser.parse_args()
    args.token_file.parent.mkdir(parents=True,exist_ok=True)
    try:
        fd=os.open(args.token_file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError:pass
    else:
        with os.fdopen(fd,"w") as stream:stream.write(secrets.token_urlsafe(48))
    if args.token_file.is_symlink() or args.token_file.stat().st_mode&0o077:raise ValueError("token file must be private")
    token=args.token_file.read_text().strip()
    if len(token)<32:raise ValueError("token too short")
    verifier=local_lean_verifier(args.config)
    verifier.durable_operations = True
    from nima_semantica.lean_jobs import LeanJobs
    jobs = LeanJobs(args.jobs_database or args.config.with_suffix(".jobs.sqlite3"), verifier)
    server=ThreadingHTTPServer((args.host,args.port),handler(verifier,token,jobs))
    print(f"Pinned Lean worker listening on {args.host}:{args.port}",flush=True)
    try: server.serve_forever()
    finally: server.server_close(); jobs.close()


if __name__=="__main__":main()
