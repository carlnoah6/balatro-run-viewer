#!/usr/bin/env python3
"""Lightweight deploy webhook listener for balatro-run-viewer."""
import http.server
import subprocess
import json
import os
import hmac
import hashlib

PORT = 12391
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "balatro-deploy-2026")
REPO_DIR = "/home/ubuntu/.openclaw/workspace/projects/balatro-run-viewer"

class DeployHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/deploy":
            self.send_response(404)
            self.end_headers()
            return

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)

        # Verify token
        auth = self.headers.get('Authorization', '')
        if auth != f'Bearer {WEBHOOK_SECRET}':
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b'Forbidden')
            return

        # Deploy via Docker
        try:
            subprocess.run(["git", "pull", "origin", "main"], cwd=REPO_DIR, timeout=30, check=True)
            subprocess.run(["docker", "compose", "build"], cwd=REPO_DIR, timeout=120, check=True)
            subprocess.run(["docker", "compose", "up", "-d"], cwd=REPO_DIR, timeout=30, check=True)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok": true, "message": "deployed via docker"}')
            print(f"[deploy] Success")
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f'{{"ok": false, "error": "{e}"}}'.encode())
            print(f"[deploy] Failed: {e}")

    def log_message(self, format, *args):
        print(f"[deploy-webhook] {args[0]}")

if __name__ == "__main__":
    server = http.server.HTTPServer(("127.0.0.1", PORT), DeployHandler)
    print(f"[deploy-webhook] Listening on port {PORT}")
    server.serve_forever()
