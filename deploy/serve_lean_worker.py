"""Authenticated private Lean endpoint; pinned config is selected only at startup."""
import argparse
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import secrets
from pathlib import Path

from nima_semantica.lean_transport import local_lean_verifier, execute_packet, MAX_RESPONSE
from nima_semantica.lean_verification_service import LeanVerificationService
from nima_semantica.providers import strict_json_object


def handler(verifier,token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass

        def authorized(self):
            self.connection.settimeout(10);self.close_connection=True
            if not hmac.compare_digest(self.headers.get("Authorization",""),"Bearer "+token):
                self.send_error(403);return False
            return True

        def reply(self,value):
            body=json.dumps(value,allow_nan=False).encode()
            if len(body)>MAX_RESPONSE:raise ValueError("response limit")
            self.send_response(200);self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)

        def do_GET(self):
            if not self.authorized():return
            if self.path!="/manifest":self.send_error(404);return
            self.reply(LeanVerificationService(None,verifier).environment_manifest())

        def do_POST(self):
            if not self.authorized():return
            if self.path!="/verify":self.send_error(404);return
            try:
                size=int(self.headers.get("Content-Length","0"))
                if self.headers.get("Transfer-Encoding") or not 0<size<=16*1024*1024:raise ValueError("request framing")
                raw=self.rfile.read(size)
                if len(raw)!=size:raise ValueError("incomplete body")
                payload=strict_json_object(raw.decode())
            except Exception:
                self.send_error(400);return
            try:self.reply(execute_packet(verifier,payload))
            except Exception:self.send_error(503,"Lean verification transport failed")
    return Handler


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host",default="127.0.0.1");parser.add_argument("--port",type=int,default=7873)
    parser.add_argument("--config",required=True,type=Path);parser.add_argument("--token-file",required=True,type=Path)
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
    server=HTTPServer((args.host,args.port),handler(verifier,token))
    print(f"Pinned Lean worker listening on {args.host}:{args.port}",flush=True)
    server.serve_forever()


if __name__=="__main__":main()
