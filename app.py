import os, threading, logging
from flask import Flask, jsonify, request
from google.colab import userdata

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)
_lock = threading.Lock()

@app.route("/health")
def health():
    return jsonify({"status": "online", "brand": "LeadFlow AI"})

@app.route("/api/run-pipeline", methods=["POST"])
def run_pipeline():
    if not _lock.acquire(blocking=False):
        return jsonify({"status": "already_running"}), 409
    def _run():
        try:
            exec(open("leadflow_v9_phase3.py").read())
        finally:
            _lock.release()
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started"})

@app.route("/api/status")
def status():
    running = not _lock.acquire(blocking=False)
    if not running: _lock.release()
    return jsonify({"running": running})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
