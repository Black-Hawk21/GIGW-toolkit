"""
STQC Web Dashboard
==================
Flask + SocketIO web interface that wraps the four CLI accessibility
audit tools — Crawler, Alt-Text Checker, Contrast Checker, Media Crawler —
and provides interactive dashboards for each.
"""

import os
import sys
import csv
import json
import uuid
import threading
import subprocess
import time
from datetime import datetime
from pathlib import Path

from flask import (
    Flask, render_template, request, jsonify, send_from_directory,
    redirect, url_for
)
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename

# ── App setup ──────────────────────────────────────────────────────────────────

BASE_DIR   = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["SECRET_KEY"] = "stqc-secret-key-2024"
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Track running jobs
jobs = {}


# ── Helpers ────────────────────────────────────────────────────────────────────

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in {"csv", "xlsx"}


def parse_csv(filepath):
    """Parse a CSV file and return (headers, rows)."""
    rows = []
    headers = []
    try:
        with open(filepath, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            for row in reader:
                rows.append(dict(row))
    except Exception as e:
        print(f"Error parsing CSV {filepath}: {e}")
    return headers, rows


def run_tool_process(job_id, cmd, cwd=None):
    """Run a CLI tool in a subprocess and stream output via SocketIO."""
    # Job dict is pre-initialised by the caller before the thread starts.
    print(f"[JOB {job_id}] Starting: {' '.join(cmd)}")

    # Force UTF-8 on the child process so Unicode chars (─ ✓ ✗) don't
    # crash with cp1252 on Windows.
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=cwd or str(BASE_DIR),
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )

        for line in iter(process.stdout.readline, ""):
            line = line.rstrip()
            if line:
                jobs[job_id]["output"].append(line)
                socketio.emit("tool_output", {
                    "job_id": job_id,
                    "line": line,
                })

        process.wait()
        elapsed = time.time() - jobs[job_id]["start"]
        jobs[job_id]["status"] = "done" if process.returncode in (0, 2) else "error"
        jobs[job_id]["elapsed"] = round(elapsed, 1)
        jobs[job_id]["returncode"] = process.returncode
        print(f"[JOB {job_id}] Finished (exit={process.returncode}, {elapsed:.1f}s)")

        socketio.emit("tool_complete", {
            "job_id": job_id,
            "status": jobs[job_id]["status"],
            "elapsed": jobs[job_id]["elapsed"],
        })

    except Exception as e:
        print(f"[JOB {job_id}] ERROR: {e}")
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        socketio.emit("tool_complete", {
            "job_id": job_id,
            "status": "error",
            "error": str(e),
        })


# ── Routes: Pages ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/tool/crawler")
def tool_crawler():
    return render_template("tool_crawler.html")


@app.route("/tool/crawler-js")
def tool_crawler_js():
    return render_template("tool_crawler_js.html")


@app.route("/tool/alt-text")
def tool_alt_text():
    return render_template("tool_alt_text.html")


@app.route("/tool/contrast")
def tool_contrast():
    return render_template("tool_contrast.html")


@app.route("/tool/media")
def tool_media():
    return render_template("tool_media.html")


@app.route("/summary/crawler")
def summary_crawler():
    return render_template("summary_crawler.html")


@app.route("/summary/alt-text")
def summary_alt_text():
    return render_template("summary_alt_text.html")


@app.route("/summary/contrast")
def summary_contrast():
    return render_template("summary_contrast.html")


@app.route("/summary/media")
def summary_media():
    return render_template("summary_media.html")


# ── Routes: API ────────────────────────────────────────────────────────────────

@app.route("/api/run/crawler", methods=["POST"])
def api_run_crawler():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400

    depth   = data.get("depth", -1)
    delay   = data.get("delay", 1.0)
    timeout = data.get("timeout", 15)
    fmt     = data.get("format", "csv")

    job_id   = str(uuid.uuid4())[:8]
    out_file = str(OUTPUT_DIR / f"crawl_{job_id}.{fmt}")

    cmd = [
        sys.executable, "-u", str(BASE_DIR / "crawler.py"),
        "--url", url,
        "--depth", str(depth),
        "--delay", str(delay),
        "--timeout", str(timeout),
        "--format", fmt,
        "--output", out_file,
    ]

    # Pre-initialise job dict before starting the thread
    jobs[job_id] = {"status": "running", "output": [], "start": time.time(),
                    "output_file": out_file}

    thread = threading.Thread(target=run_tool_process, args=(job_id, cmd))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id, "output_file": os.path.basename(out_file)})


@app.route("/api/run/crawler-js", methods=["POST"])
def api_run_crawler_js():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400

    depth     = data.get("depth", -1)
    delay     = data.get("delay", 1.5)
    timeout   = data.get("timeout", 20)
    wait      = data.get("wait", 2000)
    fmt       = data.get("format", "csv")
    headless  = data.get("headless", True)

    job_id   = str(uuid.uuid4())[:8]
    out_file = str(OUTPUT_DIR / f"crawl_js_{job_id}.{fmt}")

    cmd = [
        sys.executable, "-u", str(BASE_DIR / "crawler_js.py"),
        "--url", url,
        "--depth", str(depth),
        "--delay", str(delay),
        "--timeout", str(timeout),
        "--wait", str(wait),
        "--format", fmt,
        "--output", out_file,
    ]
    if not headless:
        cmd.append("--no-headless")

    jobs[job_id] = {"status": "running", "output": [], "start": time.time(),
                    "output_file": out_file}

    thread = threading.Thread(target=run_tool_process, args=(job_id, cmd))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id, "output_file": os.path.basename(out_file)})


@app.route("/api/run/alt-text", methods=["POST"])
def api_run_alt_text():
    if "csv_file" not in request.files:
        return jsonify({"error": "CSV file is required"}), 400

    file = request.files["csv_file"]
    if not file or not allowed_file(file.filename):
        return jsonify({"error": "Invalid file type. Upload a CSV."}), 400

    filename  = secure_filename(file.filename)
    input_path = str(UPLOAD_DIR / filename)
    file.save(input_path)

    delay = request.form.get("delay", "0.5")
    limit = request.form.get("limit", "")

    job_id   = str(uuid.uuid4())[:8]
    out_file = str(OUTPUT_DIR / f"alt_text_{job_id}.csv")

    cmd = [
        sys.executable, "-u", str(BASE_DIR / "alt_text.py"),
        input_path, out_file,
        "--delay", str(delay),
    ]
    if limit:
        cmd.extend(["--limit", str(limit)])

    jobs[job_id] = {"status": "running", "output": [], "start": time.time(),
                    "output_file": out_file}

    thread = threading.Thread(target=run_tool_process, args=(job_id, cmd))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id, "output_file": os.path.basename(out_file)})


@app.route("/api/run/contrast", methods=["POST"])
def api_run_contrast():
    if "csv_file" not in request.files:
        return jsonify({"error": "CSV file is required"}), 400

    file = request.files["csv_file"]
    if not file or not allowed_file(file.filename):
        return jsonify({"error": "Invalid file type. Upload a CSV."}), 400

    filename   = secure_filename(file.filename)
    input_path = str(UPLOAD_DIR / filename)
    file.save(input_path)

    level            = request.form.get("level", "AA")
    custom_threshold = request.form.get("custom_threshold", "")
    workers          = request.form.get("workers", "3")
    timeout_val      = request.form.get("timeout", "20")
    no_verify        = request.form.get("no_verify", "false")
    sample           = request.form.get("sample", "")
    delay            = request.form.get("delay", "0.5")

    job_id    = str(uuid.uuid4())[:8]
    out_dir   = str(OUTPUT_DIR / f"contrast_{job_id}")
    os.makedirs(out_dir, exist_ok=True)

    cmd = [
        sys.executable, "-u", str(BASE_DIR / "contrast_checker.py"),
        input_path,
        "--level", level,
        "--output", out_dir,
        "--workers", str(workers),
        "--timeout", str(timeout_val),
        "--delay", str(delay),
    ]
    if custom_threshold:
        cmd.extend(["--custom-threshold", str(custom_threshold)])
    if no_verify == "true":
        cmd.append("--no-verify")
    if sample:
        cmd.extend(["--sample", str(sample)])

    jobs[job_id] = {"status": "running", "output": [], "start": time.time(),
                    "output_dir": out_dir}

    thread = threading.Thread(target=run_tool_process, args=(job_id, cmd))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id, "output_dir": f"contrast_{job_id}"})


@app.route("/api/run/media", methods=["POST"])
def api_run_media():
    if "csv_file" not in request.files:
        return jsonify({"error": "CSV file is required"}), 400

    file = request.files["csv_file"]
    if not file or not allowed_file(file.filename):
        return jsonify({"error": "Invalid file type. Upload a CSV."}), 400

    filename   = secure_filename(file.filename)
    input_path = str(UPLOAD_DIR / filename)
    file.save(input_path)

    delay     = request.form.get("delay", "0.5")
    timeout   = request.form.get("timeout", "10")
    no_verify = request.form.get("no_verify", "false")
    fmt       = request.form.get("format", "csv")

    job_id    = str(uuid.uuid4())[:8]
    out_base  = str(OUTPUT_DIR / f"media_{job_id}")

    cmd = [
        sys.executable, "-u", str(BASE_DIR / "media_crawler.py"),
        "--input", input_path,
        "--output", out_base,
        "--format", fmt,
        "--delay", str(delay),
        "--timeout", str(timeout),
    ]
    if no_verify == "true":
        cmd.append("--no-verify")

    jobs[job_id] = {"status": "running", "output": [], "start": time.time(),
                    "output_base": out_base}

    thread = threading.Thread(target=run_tool_process, args=(job_id, cmd))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id, "output_base": f"media_{job_id}"})


@app.route("/api/job/<job_id>")
def api_job_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "status": job.get("status"),
        "elapsed": job.get("elapsed"),
        "output_lines": len(job.get("output", [])),
    })


@app.route("/api/parse-csv", methods=["POST"])
def api_parse_csv():
    """Parse an uploaded CSV and return JSON data for the dashboard."""
    if "csv_file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["csv_file"]
    if not file or not allowed_file(file.filename):
        return jsonify({"error": "Invalid file type"}), 400

    filename   = secure_filename(file.filename)
    filepath   = str(UPLOAD_DIR / f"summary_{filename}")
    file.save(filepath)

    headers, rows = parse_csv(filepath)
    return jsonify({"headers": headers, "rows": rows, "total": len(rows)})


@app.route("/api/download/<path:filename>")
def api_download(filename):
    """Download output files."""
    # Check in outputs directory
    filepath = OUTPUT_DIR / filename
    if filepath.exists() and filepath.is_file():
        return send_from_directory(str(OUTPUT_DIR), filename, as_attachment=True)

    # Check in subdirectories
    for subdir in OUTPUT_DIR.iterdir():
        if subdir.is_dir():
            candidate = subdir / filename
            if candidate.exists():
                return send_from_directory(str(subdir), filename, as_attachment=True)

    return jsonify({"error": "File not found"}), 404


@app.route("/api/list-outputs/<job_id>")
def api_list_outputs(job_id):
    """List output files for a job."""
    files = []

    # Direct file
    for f in OUTPUT_DIR.iterdir():
        if f.is_file() and job_id in f.name:
            files.append(f.name)

    # Subdirectory
    subdir = OUTPUT_DIR / f"contrast_{job_id}"
    if subdir.exists():
        for f in subdir.iterdir():
            if f.is_file():
                files.append(f"contrast_{job_id}/{f.name}")

    return jsonify({"files": files})


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port  = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("RENDER") is None   # debug on locally, off on Render

    print(f"\n  STQC Web Dashboard")
    print(f"  http://localhost:{port}\n")
    socketio.run(app, host="0.0.0.0", port=port, debug=debug,
                 allow_unsafe_werkzeug=True)
