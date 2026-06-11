"""
Reels Editing Service — Edita Reels de Instagram server-side.

POST /edit        → Inicia edição async (recebe user_id + video_url)
GET  /status/:id  → Verifica progresso (memória + fallback Supabase)
GET  /jobs        → Lista todos os jobs (persistido no Supabase)
GET  /health      → Health check
"""

import os
import uuid
import threading
import traceback

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flask import Flask, request, jsonify
from flask_cors import CORS
from lib.pipeline import run_pipeline
from lib.supabase_client import (
    get_user_settings,
    upload_to_storage,
    create_job,
    update_job,
    get_job,
    list_jobs,
    mark_stuck_jobs,
)

app = Flask(__name__)
CORS(app)  # Permite requisições de outros domínios como o do Roteiriza

# Jobs em memória (cache rápido — Supabase é a source of truth)
jobs = {}

SERVICE_SECRET = os.environ.get("SERVICE_SECRET", "")

# Throttle: só atualiza Supabase a cada N% de mudança
_last_db_progress = {}


def recover_orphaned_jobs():
    """Na startup, marca como 'failed' qualquer job preso em 'processing' no Supabase.

    Esses são jobs que estavam rodando quando o processo foi reiniciado — eles nunca
    vão completar porque a thread foi destruída junto com o processo anterior.
    """
    count = mark_stuck_jobs()
    if count:
        print(f"[STARTUP] {count} job(s) órfão(s) marcado(s) como 'failed' (restart detectado)", flush=True)
    else:
        print("[STARTUP] Nenhum job órfão encontrado.", flush=True)


def check_auth(req):
    """Aceita SERVICE_SECRET ou SUPABASE_SERVICE_ROLE_KEY como Bearer token."""
    token = req.headers.get("Authorization", "").replace("Bearer ", "").strip()
    if not token:
        return False
    service_role = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if token == service_role:
        return True
    if SERVICE_SECRET and token == SERVICE_SECRET:
        return True
    return False


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "reels-service", "jobs": len(jobs)})


@app.route("/edit", methods=["POST"])
def edit_reels():
    if not check_auth(request):
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    video_url = data.get("video_url")

    if not user_id:
        return jsonify({"error": "user_id is required"}), 400
    if not video_url:
        return jsonify({"error": "video_url is required"}), 400

    # Opções opcionais
    hook_line1 = data.get("hook_line1")
    hook_line2 = data.get("hook_line2")
    zoom_levels = data.get("zoom_levels", [1.0, 1.5, 1.0, 1.6])
    generate_sora = data.get("generate_sora", True)
    image_provider = (data.get("image_provider") or "gemini").lower()
    if image_provider not in ("gemini", "openai"):
        return jsonify({"error": "image_provider must be 'gemini' or 'openai'"}), 400

    remove_silences = data.get("remove_silences", True)
    generate_captions_enabled = data.get("generate_captions", True)
    generate_overlays = data.get("generate_overlays", True)
    dynamic_editing = data.get("dynamic_editing", True)

    job_id = str(uuid.uuid4())

    # Salvar em memória
    jobs[job_id] = {
        "status": "processing",
        "progress": 0,
        "step": "starting",
        "user_id": user_id,
        "result": None,
        "error": None,
    }

    # Persistir no Supabase
    create_job(job_id, user_id, video_url)

    def run_job():
        try:
            settings = get_user_settings(user_id)
            if not settings:
                jobs[job_id]["status"] = "failed"
                jobs[job_id]["error"] = "User settings not found"
                update_job(job_id, status="failed", error="User settings not found")
                return

            openai_key = settings.get("openai_api_key")
            gemini_key = settings.get("gemini_api_key")
            openrouter_key = settings.get("openrouter_api_key")
            groq_key = settings.get("groq_api_key")

            if not openai_key:
                jobs[job_id]["status"] = "failed"
                jobs[job_id]["error"] = "OpenAI API key not configured in settings"
                update_job(job_id, status="failed", error="OpenAI API key not configured")
                return

            if image_provider == "gemini" and not gemini_key:
                jobs[job_id]["status"] = "failed"
                jobs[job_id]["error"] = "Gemini API key not configured in settings"
                update_job(job_id, status="failed", error="Gemini API key not configured")
                return

            def update_progress(progress, step):
                jobs[job_id]["progress"] = progress
                jobs[job_id]["step"] = step

                # Throttle DB updates: só persiste a cada 10% ou mudança de step
                last = _last_db_progress.get(job_id, -10)
                if progress - last >= 10 or progress >= 95:
                    _last_db_progress[job_id] = progress
                    update_job(job_id, progress=progress, step=step)

            result = run_pipeline(
                video_url=video_url,
                user_id=user_id,
                openai_key=openai_key,
                gemini_key=gemini_key,
                openrouter_key=openrouter_key,
                groq_key=groq_key,
                hook_line1=hook_line1,
                hook_line2=hook_line2,
                zoom_levels=zoom_levels,
                generate_sora=generate_sora,
                image_provider=image_provider,
                progress_callback=update_progress,
                remove_silences=remove_silences,
                generate_captions_enabled=generate_captions_enabled,
                generate_overlays=generate_overlays,
                dynamic_editing=dynamic_editing,
            )

            jobs[job_id]["status"] = "completed"
            jobs[job_id]["progress"] = 100
            jobs[job_id]["step"] = "done"
            jobs[job_id]["result"] = result

            # Persistir resultado final
            update_job(job_id, status="completed", progress=100, step="done", result=result)
            _last_db_progress.pop(job_id, None)

        except Exception as e:
            traceback.print_exc()
            jobs[job_id]["status"] = "failed"
            jobs[job_id]["error"] = str(e)
            update_job(job_id, status="failed", error=str(e))
            _last_db_progress.pop(job_id, None)

    thread = threading.Thread(target=run_job, daemon=True)
    thread.start()

    return jsonify({"job_id": job_id, "status": "processing"}), 202


@app.route("/status/<job_id>", methods=["GET"])
def get_status(job_id):
    if not check_auth(request):
        return jsonify({"error": "Unauthorized"}), 401

    # Tentar memória primeiro (mais rápido)
    job = jobs.get(job_id)

    # Fallback: buscar no Supabase
    if not job:
        db_job = get_job(job_id)
        if not db_job:
            return jsonify({"error": "Job not found"}), 404
        # Converter formato DB pro formato da API
        job = {
            "status": db_job["status"],
            "progress": db_job["progress"],
            "step": db_job["step"],
            "result": db_job.get("result"),
            "error": db_job.get("error"),
        }

    response = {
        "job_id": job_id,
        "status": job["status"],
        "progress": job["progress"],
        "step": job["step"],
    }

    if job["status"] == "completed" and job.get("result"):
        response["result"] = job["result"]
    elif job["status"] == "failed" and job.get("error"):
        response["error"] = job["error"]

    return jsonify(response)


@app.route("/jobs", methods=["GET"])
def list_all_jobs():
    if not check_auth(request):
        return jsonify({"error": "Unauthorized"}), 401

    user_id = request.args.get("user_id")
    status = request.args.get("status")
    limit = min(int(request.args.get("limit", "20")), 100)

    result = list_jobs(user_id=user_id, status=status, limit=limit)
    return jsonify(result)


# Recupera jobs órfãos de um eventual restart anterior
recover_orphaned_jobs()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3001))
    app.run(host="0.0.0.0", port=port, debug=True)
