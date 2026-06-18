# Serve a health endpoint

Start an HTTP server listening on `127.0.0.1:8080`. A `GET /health` request must
return HTTP `200` with a JSON body `{"status": "ok"}`. Any other path may return
`404`. The server must stay running so it can be probed.
