#!/bin/bash
set -euo pipefail
cat > /app/server.py <<'PY'
import http.server, json, socketserver

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"status": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass

with socketserver.TCPServer(("127.0.0.1", 8080), Handler) as httpd:
    httpd.serve_forever()
PY

# Launch as a daemon the right way: detach stdout/stderr so the runner's pipe
# isn't held open (otherwise the harness waits on EOF and times out).
nohup python3 /app/server.py >/var/log/server.log 2>&1 &

# Wait for the port to accept connections before returning.
for _ in $(seq 1 30); do
  if python3 -c "import socket,sys; s=socket.socket(); sys.exit(0 if s.connect_ex(('127.0.0.1',8080))==0 else 1)"; then
    break
  fi
  sleep 0.5
done
