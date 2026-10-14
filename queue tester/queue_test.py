from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import random
import time

DEST = "https://texaslonghorns.evenue.net/signin"  # where the "queue" sends you

PAGE = """<!doctype html>
<title>Queue</title>
<h1>You are in the queue...</h1>
<p>Estimated wait: {mins}m {secs}s</p>
<div style="width:80%;height:40px;background:#ddd"><div id="bar" style="width:0;height:100%;background:#1CBD81"></div></div>
<script>
const start = Date.now();
setInterval(() => bar.style.width = Math.min(100, (Date.now() - start) / {ms} * 100) + "%", 250);
setTimeout(() => location.href = "{dest}", {ms});
</script>
"""

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        wait = random.randint(30, 600)  # 30 seconds to 10 minutes

        if self.path.startswith("/client"):
            body = PAGE.format(
                mins=wait // 60, secs=wait % 60, ms=wait * 1000, dest=DEST
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        elif self.path.startswith("/server"):
            time.sleep(wait)
            self.send_response(302)
            self.send_header("Location", DEST)
            self.end_headers()

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass  # keep the console quiet

ThreadingHTTPServer(("localhost", 8000), Handler).serve_forever()