"""Internal dev-only server for frontend/tools/hero-render.html.

Serves frontend/ like a plain static server, plus one POST endpoint
(/__save/<filename>) that writes the request body straight to
frontend/assets/hero/<filename>. Exists purely to get the hero renderer's
PNG output onto disk without going through the browser's download UI
(which pops a native Save-As dialog that automation tooling can't drive).
Not part of the shipped site.
"""
import http.server
import os

ROOT = os.path.join(os.path.dirname(__file__), "..", "frontend")
HERO_DIR = os.path.join(ROOT, "assets", "hero")
os.makedirs(HERO_DIR, exist_ok=True)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def do_POST(self):
        if not self.path.startswith("/__save/"):
            self.send_response(404)
            self.end_headers()
            return
        name = os.path.basename(self.path[len("/__save/"):])
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        with open(os.path.join(HERO_DIR, name), "wb") as f:
            f.write(body)
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b"ok")

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()


if __name__ == "__main__":
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 8787), Handler)
    print("serving", ROOT, "on http://127.0.0.1:8787 (POST /__save/<name> writes to assets/hero/)")
    server.serve_forever()
