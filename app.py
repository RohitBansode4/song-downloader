from flask import Flask, request, render_template, Response, send_file, abort
import yt_dlp
import tempfile
import os
import shutil
import json
import threading
import zipfile
import uuid
import time

app = Flask(__name__)

jobs = {}  # job_id -> job data


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/download-all", methods=["POST"])
def download_all():
    data = request.get_json()
    urls = data.get("urls") if data else None
    if not urls:
        abort(400, "No URLs provided")

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "starting",
        "videos": {},
        "current": None
    }

    t = threading.Thread(
        target=process_downloads,
        args=(job_id, urls),
        daemon=True
    )
    t.start()

    return {"job_id": job_id}


@app.route("/progress/<job_id>")
def progress(job_id):
    def stream():
        while True:
            job = jobs.get(job_id)
            if not job:
                break

            yield f"data: {json.dumps(job)}\n\n"

            if job["status"] == "done":
                break

            time.sleep(0.4)

    return Response(stream(), mimetype="text/event-stream")


@app.route("/result/<job_id>")
def result(job_id):
    job = jobs.get(job_id)
    if not job or job.get("status") != "done":
        abort(404)

    zip_path = job["zip_path"]

    resp = send_file(
        zip_path,
        as_attachment=True,
        download_name="songs.zip"
    )
    resp.call_on_close(lambda: cleanup_job(job_id))
    return resp


def process_downloads(job_id, urls):
    temp_dir = tempfile.mkdtemp(prefix="yt_")
    zip_path = os.path.join(temp_dir, "songs.zip")

    def hook(d):
        if d["status"] == "downloading":
            info = d.get("info_dict", {})
            vid = info.get("id")

            jobs[job_id]["current"] = vid
            jobs[job_id]["videos"].setdefault(vid, {
                "title": info.get("title"),
                "downloaded": 0,
                "total": 0,
                "status": "downloading"
            })

            jobs[job_id]["videos"][vid]["downloaded"] = d.get("downloaded_bytes", 0)
            jobs[job_id]["videos"][vid]["total"] = (
                d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            )

        elif d["status"] == "finished":
            vid = jobs[job_id]["current"]
            if vid:
                jobs[job_id]["videos"][vid]["status"] = "processing"

    ydl_opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "noplaylist": True,
        "progress_hooks": [hook],
        "extractor_args": {
            "youtube": {"player_client": ["android"]}
        },
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            }
        ],
        "outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s"),
    }

    jobs[job_id]["status"] = "downloading"

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for url in urls:
            ydl.download([url])

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in os.listdir(temp_dir):
            if f.endswith(".mp3"):
                z.write(os.path.join(temp_dir, f), arcname=f)

    jobs[job_id]["status"] = "done"
    jobs[job_id]["zip_path"] = zip_path


def cleanup_job(job_id):
    job = jobs.pop(job_id, None)
    if job:
        shutil.rmtree(os.path.dirname(job["zip_path"]), ignore_errors=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, threaded=True, use_reloader=False)
