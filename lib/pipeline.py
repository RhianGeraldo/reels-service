"""
Pipeline completo de edição de Reels.

1. Download video
2. Converter VFR → CFR
3. Remover silêncios
4. Transcrever (Whisper)
5. Analisar segmentos + overlay plan (AI)
6. Gerar imagens hook (Gemini)
6b. Gerar 8+ overlay images (Gemini)
7. Gerar vídeos Sora (opcional)
7b. Gerar SFX pop
8. Construir hook frames (PIL)
9. Editar vídeo (zoom + hook + Ken Burns + flash/shake transitions)
9b. Aplicar image overlays (blur_overlay / split)
9c. Build SFX track
10. Captions karaokê (ASS)
11. Burn captions + 3-audio mix (voz + música + SFX)
12. Upload pro Storage
"""

import os
import json
import math
import time
import shutil
import tempfile
import subprocess
import requests
from PIL import Image, ImageDraw, ImageFont
from typing import Callable
from lib.supabase_client import upload_to_storage

MUSIC_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "music", "epic_games.mp3")
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Impact bundled no repo > fallbacks do sistema
_BUNDLED_IMPACT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fonts", "Impact.ttf")
for fp in [
    _BUNDLED_IMPACT,                                          # bundled (Railway + local)
    "/System/Library/Fonts/Supplemental/Impact.ttf",          # macOS system
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux fallback
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]:
    if os.path.exists(fp):
        FONT_PATH = fp
        break


def download_file(url, destination_path):
    import re
    import requests

    # 1. Google Drive
    match_drive = re.search(r"drive\.google\.com/file/d/([a-zA-Z0-9_-]+)", url)
    if not match_drive:
        match_drive = re.search(r"drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)", url)
    if not match_drive:
        match_drive = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", url) if "drive.google.com" in url else None

    if match_drive:
        drive_id = match_drive.group(1)
        print(f"[REELS] Downloading from Google Drive ID: {drive_id}", flush=True)
        session = requests.Session()
        download_url = "https://docs.google.com/uc?export=download"
        resp = session.get(download_url, params={"id": drive_id}, stream=True, timeout=120)
        token = None
        for key, value in resp.cookies.items():
            if key.startswith("download_warning"):
                token = value
                break
        if token:
            resp = session.get(download_url, params={"id": drive_id, "confirm": token}, stream=True, timeout=120)
        resp.raise_for_status()
    else:
        # 2. Dropbox
        if "dropbox.com" in url and "dl=1" not in url:
            if "dl=0" in url:
                url = url.replace("dl=0", "dl=1")
            elif "?" in url:
                url += "&dl=1"
            else:
                url += "?dl=1"
        print(f"[REELS] Downloading from URL: {url}", flush=True)
        resp = requests.get(url, stream=True, timeout=120)
        resp.raise_for_status()

    # Verificar se retornou HTML
    content_type = resp.headers.get("Content-Type", "").lower()
    if "text/html" in content_type:
        first_chunk = next(resp.iter_content(chunk_size=100), b"")
        if b"<!doctype" in first_chunk.lower() or b"<html" in first_chunk.lower() or b"<head" in first_chunk.lower():
            raise ValueError("URL returned an HTML page instead of the binary file. Check permissions/link.")
        with open(destination_path, "wb") as f:
            f.write(first_chunk)
            for chunk in resp.iter_content(chunk_size=32768):
                f.write(chunk)
    else:
        with open(destination_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=32768):
                f.write(chunk)


def run_pipeline(
    video_url: str,
    user_id: str,
    openai_key: str,
    gemini_key: str,
    openrouter_key: str | None = None,
    groq_key: str | None = None,
    hook_line1: str | None = None,
    hook_line2: str | None = None,
    zoom_levels: list | None = None,
    generate_sora: bool = True,
    image_provider: str = "gemini",
    progress_callback: Callable | None = None,
    remove_silences: bool = True,
    generate_captions_enabled: bool = True,
    generate_overlays: bool = True,
    dynamic_editing: bool = True,
    caption_color: str | None = None,
    caption_position: str | None = None,
    denoise_audio: bool = True,
    music_url: str | None = None,
    music_volume: float = 0.15,
    visual_filter: str | None = None,
    brightness: float = 0.0,
    contrast: float = 1.0,
    saturation: float = 1.0,
    sharpness: float = 0.0,
) -> dict:

    if zoom_levels is None:
        zoom_levels = [1.0, 1.5, 1.0, 1.6]

    import sys

    def progress(pct, step):
        if progress_callback:
            progress_callback(pct, step)
        print(f"[REELS] {pct}% — {step}", flush=True)

    workdir = tempfile.mkdtemp(prefix="reels_")
    print(f"[REELS] Workdir: {workdir}", flush=True)

    try:
        # ===== 1. DOWNLOAD VIDEO =====
        progress(5, "downloading_video")
        video_path = os.path.join(workdir, "bruto.mp4")
        download_file(video_url, video_path)
        print(f"[REELS] Downloaded: {os.path.getsize(video_path)} bytes", flush=True)

        # Obter resolução + rotation metadata
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", video_path],
            capture_output=True, text=True
        )
        streams = json.loads(probe.stdout)["streams"]
        vstream = next(s for s in streams if s["codec_type"] == "video")
        raw_w, raw_h = int(vstream["width"]), int(vstream["height"])
        rotation = 0
        for sd in vstream.get("side_data_list", []):
            if "rotation" in sd:
                rotation = int(sd["rotation"]) % 360
                break

        # Target Reels: 1080x1920 vertical fixo
        W, H = 1080, 1920
        print(f"[REELS] Resolution: raw={raw_w}x{raw_h} rotation={rotation} -> target {W}x{H}", flush=True)

        # ===== 2. CONVERT VFR → CFR (rotation física + scale para target + limpa displaymatrix) =====
        progress(8, "converting_cfr")
        cfr_path = os.path.join(workdir, "bruto_cfr.mp4")
        vf_parts = []
        # rotation no displaymatrix é counter-clockwise (convenção FFmpeg)
        # Para queimar nos pixels: transpose=2 = 90° CCW, transpose=1 = 90° CW
        if rotation in (90, -270):
            vf_parts.append("transpose=2")  # 90° CCW
        elif rotation in (270, -90):
            vf_parts.append("transpose=1")  # 90° CW
        elif rotation == 180:
            vf_parts.append("transpose=2,transpose=2")
        # Scale fit-and-crop centralizado para 1080x1920
        vf_parts.append(f"scale={W}:{H}:force_original_aspect_ratio=increase")
        vf_parts.append(f"crop={W}:{H}")
        vf_parts.append("fps=30")

        # Filtros de cor e presets visuais
        preset_brightness = 0.0
        preset_contrast = 1.0
        preset_saturation = 1.0
        colorbalance_filter = None

        if visual_filter:
            vf_lower = visual_filter.lower().strip()
            if vf_lower in ("vibrant", "vibrante"):
                preset_contrast = 1.05
                preset_saturation = 1.25
                preset_brightness = 0.01
            elif vf_lower in ("cinematic", "cinematografico", "cinematográfico"):
                preset_contrast = 1.12
                preset_saturation = 1.10
                preset_brightness = -0.01
                colorbalance_filter = "colorbalance=rs=0.06:gs=0.01:bs=-0.04"
            elif vf_lower in ("vintage", "retro"):
                preset_contrast = 0.95
                preset_saturation = 0.85
                preset_brightness = 0.02
                colorbalance_filter = "colorbalance=rs=0.08:gs=0.03:bs=-0.06"
            elif vf_lower in ("cool", "frio"):
                preset_contrast = 1.02
                preset_saturation = 0.95
                colorbalance_filter = "colorbalance=rs=-0.05:gs=-0.01:bs=0.08"
            elif vf_lower in ("b&w", "bw", "preto_e_branco", "pb"):
                preset_contrast = 1.15
                preset_saturation = 0.0
                preset_brightness = 0.02

        final_brightness = max(-1.0, min(1.0, preset_brightness + brightness))
        final_contrast = max(0.0, min(10.0, preset_contrast * contrast))
        final_saturation = max(0.0, min(10.0, preset_saturation * saturation))

        if final_brightness != 0.0 or final_contrast != 1.0 or final_saturation != 1.0:
            vf_parts.append(f"eq=brightness={final_brightness:.2f}:contrast={final_contrast:.2f}:saturation={final_saturation:.2f}")

        if colorbalance_filter:
            vf_parts.append(colorbalance_filter)

        if sharpness > 0.0:
            vf_parts.append(f"unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount={sharpness:.2f}")

        vf = ",".join(vf_parts)

        cmd = [
            "ffmpeg", "-y", "-display_rotation", "0", "-i", video_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        ]
        if denoise_audio:
            model_path = os.path.join(os.path.dirname(__file__), "sh.rnnn")
            cmd.extend(["-af", f"highpass=f=100,arnndn=model='{model_path}'"])
        cmd.extend([
            "-c:a", "aac", "-b:a", "128k", "-video_track_timescale", "30000",
            cfr_path
        ])
        subprocess.run(cmd, capture_output=True, check=True)

        # ===== 3. REMOVE SILENCES =====
        progress(12, "removing_silences")
        nosilence_path = os.path.join(workdir, "no_silence.mp4")
        if remove_silences:
            try:
                subprocess.run(
                    [sys.executable, "-m", "auto_editor", cfr_path, "--margin", "1.0s", "-o", nosilence_path],
                    capture_output=True, check=True, timeout=300
                )
            except (subprocess.CalledProcessError, FileNotFoundError):
                # Se auto-editor não está instalado, pular
                print("[REELS] auto-editor not available, skipping silence removal", flush=True)
                shutil.copy(cfr_path, nosilence_path)
        else:
            print("[REELS] Skipping silence removal as requested", flush=True)
            shutil.copy(cfr_path, nosilence_path)

        if dynamic_editing:
            # ===== 4. TRANSCRIBE (WHISPER) =====
            progress(18, "transcribing")
            transcription = transcribe_whisper(nosilence_path, openai_key, workdir, groq_key=groq_key, openrouter_key=openrouter_key)
            words = transcription.get("words", [])
            full_text = transcription.get("text", "")
            print(f"[REELS] Transcribed: {len(words)} words, {len(full_text)} chars", flush=True)

            # ===== 5. ANALYZE + PLAN (now includes overlay_images) =====
            progress(22, "analyzing_content")
            plan = analyze_content(full_text, openai_key, hook_line1, hook_line2, openrouter_key=openrouter_key)
            hook_l1 = plan["hook_line1"]
            hook_l2 = plan["hook_line2"]
            segments = plan["segments"]
            hook_images_prompts = plan["hook_images"]
            sora_prompts = plan["sora_videos"]
            overlay_specs = plan.get("overlay_images", [])
            
            if not generate_overlays:
                overlay_specs = []

            # Hard limit: max 85s de segmentos (video final ~90s com hook)
            HOOK_DUR = 5.0
            MAX_CONTENT_DUR = 85.0
            trimmed_segments = []
            total_dur = 0.0
            for seg in segments:
                s = seg["start"] if isinstance(seg, dict) else seg[0]
                e = seg["end"] if isinstance(seg, dict) else seg[1]
                # Segment 0: edit_video skips content before HOOK_DUR
                effective_s = max(s, HOOK_DUR) if len(trimmed_segments) == 0 else s
                seg_dur = e - effective_s
                if seg_dur <= 0:
                    continue
                if total_dur + seg_dur > MAX_CONTENT_DUR:
                    remaining = MAX_CONTENT_DUR - total_dur
                    if remaining > 2:  # so inclui se sobrar mais de 2s
                        if isinstance(seg, dict):
                            seg = {**seg, "end": effective_s + remaining}
                        trimmed_segments.append(seg)
                    break
                trimmed_segments.append(seg)
                total_dur += seg_dur
            if len(trimmed_segments) < len(segments):
                print(f"[REELS] Trimmed segments: {len(segments)} -> {len(trimmed_segments)} (max {MAX_CONTENT_DUR}s)", flush=True)
            segments = trimmed_segments

            # Build timeline map: original timestamps -> edited video timestamps
            remap_ts = build_timeline_map(segments, HOOK_DUR)

            print(f"[REELS] Plan: hook='{hook_l1}/{hook_l2}', {len(segments)} segments, {len(sora_prompts)} sora, {len(overlay_specs)} overlays", flush=True)

            # Filter overlay collisions with Sora windows
            if overlay_specs and sora_prompts:
                overlay_specs = filter_overlay_collisions(overlay_specs, sora_prompts)
                print(f"[REELS] After collision filter: {len(overlay_specs)} overlays", flush=True)

            # ===== 6. GENERATE HOOK IMAGES =====
            progress(28, "generating_hook_images")
            if generate_overlays:
                hook_img_a = generate_image(image_provider, hook_images_prompts[0], openai_key, gemini_key, workdir, "hook_a.png", openrouter_key=openrouter_key)
                time.sleep(2)  # rate limit
                hook_img_b = generate_image(image_provider, hook_images_prompts[1], openai_key, gemini_key, workdir, "hook_b.png", openrouter_key=openrouter_key)
            else:
                hook_img_a = None
                hook_img_b = None
                print("[REELS] Skipping hook images generation", flush=True)

            # ===== 6b. GENERATE OVERLAY IMAGES =====
            overlay_data = []
            if overlay_specs:
                progress(32, "generating_overlay_images")
                overlay_data = generate_overlay_images(overlay_specs, image_provider, openai_key, gemini_key, workdir, openrouter_key=openrouter_key)
                print(f"[REELS] Generated {len(overlay_data)} overlay images", flush=True)
                # Remap overlay timestamps from original to edited timeline
                remapped_ov = []
                for ov in overlay_data:
                    new_t = remap_ts(ov["insert_at"])
                    if new_t is not None:
                        ov["insert_at"] = new_t
                        remapped_ov.append(ov)
                overlay_data = remapped_ov
                print(f"[REELS] Remapped {len(overlay_data)} overlays to edited timeline", flush=True)

            # ===== 7. GENERATE SORA VIDEOS (OPTIONAL) =====
            sora_paths = []
            if generate_sora and sora_prompts:
                progress(42, "generating_sora_videos")
                sora_paths = generate_sora_videos(sora_prompts, openai_key, workdir, W, H)
                # Remap Sora insert_at from original to edited timeline
                for sp in sora_paths:
                    new_t = remap_ts(sp["insert_at"])
                    if new_t is not None:
                        sp["insert_at"] = new_t
                    else:
                        sp["insert_at"] = -1
                sora_paths = [sp for sp in sora_paths if sp["insert_at"] >= 0]
                print(f"[REELS] Remapped {len(sora_paths)} Sora cutaways to edited timeline", flush=True)
                progress(55, "sora_videos_done")
            else:
                progress(55, "skipping_sora")

            # ===== 7b. GENERATE SFX POP =====
            progress(55, "generating_sfx")
            try:
                sfx_pop_path = generate_sfx_pop(workdir)
                print(f"[REELS] SFX pop generated", flush=True)
            except Exception as e:
                sfx_pop_path = None
                print(f"[REELS] SFX pop generation failed: {e}", flush=True)

            # ===== 8. BUILD HOOK FRAMES =====
            progress(58, "building_hook_frames")
            hook_frame_a, hook_frame_b, video_start_y, crop_top = build_hook_frames(
                W, H, hook_img_a, hook_img_b, hook_l1, hook_l2, nosilence_path, workdir,
                banner_color=caption_color
            )

            # ===== 9. EDIT VIDEO (zoom + hook + Ken Burns + transitions) =====
            progress(62, "editing_video")
            noCaption_path = edit_video(
                nosilence_path, W, H, hook_frame_a, hook_frame_b,
                video_start_y, crop_top, segments, zoom_levels, sora_paths, workdir
            )
            progress(72, "video_edited")

            # ===== 9b. APPLY IMAGE OVERLAYS =====
            if overlay_data:
                progress(72, "applying_image_overlays")
                noCaption_path = apply_image_overlays(noCaption_path, overlay_data, W, H, workdir)
                print(f"[REELS] Image overlays applied", flush=True)

            # ===== 9c. BUILD SFX TRACK =====
            sfx_track_path = None
            if sfx_pop_path:
                progress(78, "building_sfx_track")
                sfx_timestamps = collect_sfx_timestamps(segments, overlay_data, HOOK_DUR)
                total_dur = get_duration(noCaption_path)
                sfx_track_path = build_sfx_track(sfx_pop_path, sfx_timestamps, total_dur, workdir)

        else:
            print('[REELS] Bypassing dynamic editing. Pass-through mode active.', flush=True)
            noCaption_path = nosilence_path
            sfx_track_path = None
            hook_l1 = ''
            hook_l2 = ''
            full_text = ''

        # ===== 10. CAPTIONS (ASS) =====
        progress(80, "generating_captions")
        print(f"[REELS] noCaption file: {os.path.getsize(noCaption_path)} bytes", flush=True)
        if generate_captions_enabled:
            ass_path = generate_captions(
                noCaption_path, openai_key, W, H, workdir,
                groq_key=groq_key, openrouter_key=openrouter_key,
                caption_color=caption_color, caption_position=caption_position
            )
            print(f"[REELS] ASS captions generated", flush=True)
        else:
            ass_path = None
            print(f"[REELS] Skipping captions generation", flush=True)

        # Limpar segmentos intermediários pra liberar disco
        for f_name in os.listdir(workdir):
            f_path = os.path.join(workdir, f_name)
            if f_name.startswith("seg_") or f_name.startswith("hook_") or f_name == "hook.mp4":
                try:
                    os.remove(f_path)
                except:
                    pass

        # ===== 11. DOWNLOAD MUSIC + BURN CAPTIONS + 3-AUDIO MIX =====
        progress(82, "downloading_music")
        resolved_music_path = MUSIC_PATH  # default
        if music_url:
            m_url = music_url.strip().lower()
            if m_url == "none":
                resolved_music_path = None
                print("[REELS] Music disabled by user.", flush=True)
            else:
                m_url = music_url.strip()
                try:
                    progress(82, "downloading_music")
                    downloaded_music = os.path.join(workdir, "bg_music.mp3")

                    # YouTube -> yt-dlp
                    is_youtube = "youtube.com" in m_url or "youtu.be" in m_url
                    if is_youtube:
                        import sys as _sys
                        yt_dlp_bin = os.path.join(os.path.dirname(_sys.executable), "yt-dlp")
                        temp_output_template = os.path.join(workdir, "yt_download.%(ext)s")
                        cmd = [yt_dlp_bin, "--no-playlist", "-x", "--audio-format", "mp3",
                               "-o", temp_output_template, m_url]
                        print(f"[REELS] Downloading YouTube audio using command: {' '.join(cmd)}", flush=True)
                        res = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
                        if res.returncode != 0:
                            raise ValueError(f"yt-dlp failed: {res.stderr}")
                        extracted_mp3 = os.path.join(workdir, "yt_download.mp3")
                        if os.path.exists(extracted_mp3):
                            shutil.move(extracted_mp3, downloaded_music)
                        else:
                            found = False
                            for f_name in os.listdir(workdir):
                                if f_name.startswith("yt_download") and f_name.endswith(".mp3"):
                                    shutil.move(os.path.join(workdir, f_name), downloaded_music)
                                    found = True
                                    break
                            if not found:
                                raise ValueError("Could not find extracted mp3 file from yt-dlp")
                        resolved_music_path = downloaded_music
                        print(f"[REELS] YouTube audio successfully downloaded: {os.path.getsize(resolved_music_path)} bytes", flush=True)
                    else:
                        # Google Drive, Dropbox, or direct link
                        download_file(m_url, downloaded_music)
                        resolved_music_path = downloaded_music
                        print(f"[REELS] Downloaded custom music: {os.path.getsize(resolved_music_path)} bytes", flush=True)

                except Exception as e:
                    print(f"[REELS] Failed to download/verify music from {m_url} (error: {e}). Falling back to default music.", flush=True)
                    resolved_music_path = MUSIC_PATH
        else:
            # Check if it's a local file in music/
            if music_url is None:
                resolved_music_path = MUSIC_PATH

        progress(85, "burning_captions")
        print("[REELS] Starting burn_captions_and_music (3-audio mix)...", flush=True)
        final_path = burn_captions_and_music(
            noCaption_path, ass_path, workdir,
            sfx_track_path=sfx_track_path,
            music_path=resolved_music_path,
            music_volume=music_volume
        )
        print(f"[REELS] Final video: {os.path.getsize(final_path)} bytes", flush=True)
        progress(92, "video_finalized")

        # Limpar tudo exceto o final
        for f_name in os.listdir(workdir):
            f_path = os.path.join(workdir, f_name)
            if f_path != final_path and os.path.isfile(f_path):
                try:
                    os.remove(f_path)
                except:
                    pass

        # ===== 12. UPLOAD TO STORAGE =====
        progress(95, "uploading")
        file_size = os.path.getsize(final_path)
        file_size_mb = round(file_size / 1024 / 1024, 1)
        print(f"[REELS] Uploading {file_size_mb}MB to Storage...", flush=True)
        storage_path = f"reels/{user_id}/{os.path.basename(workdir)}/REELS_FINAL.mp4"

        try:
            public_url = upload_to_storage("user-uploads", storage_path, final_path)
        except Exception as upload_err:
            if "413" in str(upload_err) or "too large" in str(upload_err).lower() or "Payload" in str(upload_err):
                # Re-encode com bitrate menor e tentar de novo
                print(f"[REELS] Upload falhou ({file_size_mb}MB). Re-encoding com CRF 28...", flush=True)
                smaller_path = os.path.join(workdir, "REELS_FINAL_small.mp4")
                subprocess.run([
                    "ffmpeg", "-y", "-i", final_path,
                    "-c:v", "libx264", "-crf", "28", "-preset", "fast",
                    "-c:a", "aac", "-b:a", "128k", smaller_path
                ], capture_output=True)
                if os.path.exists(smaller_path):
                    final_path = smaller_path
                    file_size_mb = round(os.path.getsize(final_path) / 1024 / 1024, 1)
                    print(f"[REELS] Re-encoded: {file_size_mb}MB. Retentando upload...", flush=True)
                    public_url = upload_to_storage("user-uploads", storage_path, final_path)
                else:
                    raise upload_err
            else:
                raise

        print(f"[REELS] Upload done: {public_url}", flush=True)
        progress(100, "done")

        return {
            "video_url": public_url,
            "duration": get_duration(final_path),
            "resolution": f"{W}x{H}",
            "hook_text": f"{hook_l1}\n{hook_l2}",
            "transcript": full_text[:500],
        }

    finally:
        # Cleanup workdir
        try:
            shutil.rmtree(workdir, ignore_errors=True)
        except:
            pass


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def get_duration(path):
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", path],
        capture_output=True, text=True
    )
    try:
        return float(json.loads(probe.stdout)["format"]["duration"])
    except (KeyError, json.JSONDecodeError, ValueError) as e:
        size = os.path.getsize(path) if os.path.exists(path) else "MISSING"
        raise RuntimeError(
            f"ffprobe failed for {path} (size={size}): {e}; "
            f"stdout={probe.stdout[:400]!r} stderr={probe.stderr[:400]!r}"
        )


def transcribe_whisper(video_path, openai_key, workdir, groq_key=None, openrouter_key=None):
    """Transcreve com Whisper. Cascata: OpenAI Whisper → Groq whisper-large-v3 → OpenRouter."""
    file_size = os.path.getsize(video_path)
    upload_path = video_path

    if file_size > 25 * 1024 * 1024:
        audio_path = os.path.join(workdir, "audio.m4a")
        subprocess.run([
            "ffmpeg", "-y", "-i", video_path, "-vn", "-c:a", "aac", "-b:a", "64k", audio_path
        ], capture_output=True, check=True)
        upload_path = audio_path

    result = None
    try:
        with open(upload_path, "rb") as f:
            resp = requests.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {openai_key}"},
                files={"file": (os.path.basename(upload_path), f)},
                data={
                    "model": "whisper-1",
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": ["word", "segment"],
                    "language": "pt",
                },
            )
        resp.raise_for_status()
        result = resp.json()
    except Exception as e:
        print(f"[REELS] OpenAI Whisper falhou ({e})", flush=True)

    if result is None and groq_key:
        try:
            print("[REELS] tentando Groq whisper-large-v3", flush=True)
            result = _groq_transcribe(upload_path, groq_key)
        except Exception as e:
            print(f"[REELS] Groq Whisper falhou ({e})", flush=True)

    if result is None and openrouter_key:
        print("[REELS] tentando OpenRouter gpt-4o-audio-preview", flush=True)
        result = _openrouter_transcribe(upload_path, openrouter_key, workdir)

    if result is None:
        raise RuntimeError("Todos os providers de transcrição falharam (OpenAI, Groq, OpenRouter)")

    with open(os.path.join(workdir, "transcription.json"), "w") as f:
        json.dump(result, f, ensure_ascii=False)

    return result


def analyze_content(text, openai_key, hook_line1=None, hook_line2=None, openrouter_key=None):
    """Usa GPT-4o-mini pra analisar transcrição e gerar plano de edição."""
    prompt = f"""Analise esta transcrição de um Reels e retorne um JSON com:
1. "hook_line1": primeira linha do hook (máximo 5 palavras, IMPACTANTE, CAPSLOCK)
2. "hook_line2": segunda linha do hook (máximo 6 palavras, IMPACTANTE, CAPSLOCK)
3. "segments": array de objetos {{"start": seconds, "end": seconds, "topic": "descrição curta"}}
   - Divida em 5-8 segmentos temáticos baseado nas mudanças de assunto
   - IMPORTANTE: duracao TOTAL de todos os segmentos deve ser no MAXIMO 85 segundos (video final sera ~90s com hook)
   - Se a transcricao for longa, selecione APENAS os trechos mais impactantes
4. "hook_images": array com 2 prompts em PORTUGUÊS para gerar imagens realistas 16:9
   - Estilo: foto real, luz natural, sem filtro, sem efeito cinematografico. NUNCA mencionar iPhone, camera ou dispositivo
   - Devem ser impactantes e complementares ao tema do vídeo
5. "sora_videos": array com 3 objetos {{"prompt": "em português", "insert_at": seconds}}
   - Videos de apoio (cutaway) estilo real, luz natural, sem filtros. NUNCA mencionar iPhone, camera ou dispositivo
   - insert_at = momento no vídeo onde o cutaway deve aparecer
6. "overlay_images": array com 8+ objetos {{"prompt": "descricao PT-BR", "insert_at": seconds, "duration": 2.5, "mode": "blur_overlay" ou "split"}}
   - 8+ imagens distribuidas pelo video, intercaladas com rosto do apresentador
   - Duration 2-3s cada
   - Timing NAO sobrepor com sora_videos (minimo 5s de distancia)
   - Alternar blur_overlay e split
   - Prompts: foto realista PT-BR, luz natural. NUNCA mencionar iPhone, camera ou dispositivo

{"Ignore hook_line1/hook_line2 do JSON, use estes:" + chr(10) + f"hook_line1: {hook_line1}" + chr(10) + f"hook_line2: {hook_line2}" if hook_line1 and hook_line2 else ""}

Transcrição:
{text}

Retorne APENAS o JSON, sem markdown."""

    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {openai_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
            },
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        if not openrouter_key:
            raise
        print(f"[REELS] GPT-4o-mini falhou ({e}); fallback OpenRouter gpt-oss-120b:free", flush=True)
        content = _openrouter_chat(prompt, openrouter_key, model="openai/gpt-oss-120b:free")

    # Parse JSON (remover markdown se houver)
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0]

    return json.loads(content)


def generate_gemini_image(prompt, gemini_key, workdir, filename, max_retries=3):
    """Gera imagem com Gemini 3 Pro (com retry)."""
    import base64

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-image-preview:generateContent?key={gemini_key}"
    payload = {
        "contents": [{"parts": [{"text": f"Gere uma imagem fotorrealista, luz natural, sem filtro, alta resolucao, 16:9: {prompt}"}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE", "TEXT"],
        },
    }

    for attempt in range(max_retries):
        try:
            resp = requests.post(url, json=payload, timeout=120)
            resp.raise_for_status()
            data = resp.json()

            candidates = data.get("candidates", [])
            if not candidates:
                print(f"[REELS] Gemini attempt {attempt + 1}: no candidates. Response: {str(data)[:200]}", flush=True)
                time.sleep(3)
                continue

            for part in candidates[0].get("content", {}).get("parts", []):
                if "inlineData" in part:
                    img_bytes = base64.b64decode(part["inlineData"]["data"])
                    path = os.path.join(workdir, filename)
                    with open(path, "wb") as f:
                        f.write(img_bytes)
                    return path

            print(f"[REELS] Gemini attempt {attempt + 1}: no image in response", flush=True)
            time.sleep(3)
        except Exception as e:
            print(f"[REELS] Gemini attempt {attempt + 1} error: {e}", flush=True)
            time.sleep(3)

    raise Exception("Gemini did not return an image")


def generate_openai_image(prompt, openai_key, workdir, filename, max_retries=3):
    """Gera imagem com gpt-image-1 (formato 16:9 via 1536x1024). Retorno: caminho local."""
    import base64

    url = "https://api.openai.com/v1/images/generations"
    headers = {"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"}
    payload = {
        "model": "gpt-image-1",
        "prompt": f"Imagem fotorrealista, luz natural, sem filtro, alta resolução, 16:9: {prompt}",
        "size": "1536x1024",
        "n": 1,
    }

    for attempt in range(max_retries):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=180)
            if not resp.ok:
                print(f"[REELS] OpenAI image attempt {attempt + 1} HTTP {resp.status_code}: {resp.text[:400]}", flush=True)
                time.sleep(3)
                continue
            data = resp.json()
            items = data.get("data", [])
            if not items:
                print(f"[REELS] OpenAI image attempt {attempt + 1}: empty data. Response: {str(data)[:200]}", flush=True)
                time.sleep(3)
                continue
            b64 = items[0].get("b64_json")
            if not b64:
                print(f"[REELS] OpenAI image attempt {attempt + 1}: no b64_json in response", flush=True)
                time.sleep(3)
                continue
            img_bytes = base64.b64decode(b64)
            path = os.path.join(workdir, filename)
            with open(path, "wb") as f:
                f.write(img_bytes)
            return path
        except Exception as e:
            print(f"[REELS] OpenAI image attempt {attempt + 1} error: {e}", flush=True)
            time.sleep(3)

    raise Exception("OpenAI did not return an image")


def generate_image(provider, prompt, openai_key, gemini_key, workdir, filename, openrouter_key=None):
    """Dispatcher por provedor de imagem. provider ∈ {'gemini', 'openai'}.
    Fallback: OpenRouter (google/gemini-2.5-flash-image — pago, mais barato)."""
    try:
        if provider == "openai":
            return generate_openai_image(prompt, openai_key, workdir, filename)
        return generate_gemini_image(prompt, gemini_key, workdir, filename)
    except Exception as e:
        if not openrouter_key:
            raise
        print(f"[REELS] image provider '{provider}' falhou ({e}); fallback OpenRouter gemini-2.5-flash-image", flush=True)
        return _openrouter_image(prompt, openrouter_key, workdir, filename, model="google/gemini-2.5-flash-image")


def generate_sora_videos(sora_prompts, openai_key, workdir, target_w, target_h):
    """Gera vídeos Sora em paralelo, faz polling, download e resize."""
    headers = {"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"}

    # Submit all jobs
    job_ids = []
    for i, spec in enumerate(sora_prompts):
        prompt = spec if isinstance(spec, str) else spec.get("prompt", "")
        resp = requests.post(
            "https://api.openai.com/v1/videos",
            headers=headers,
            json={"model": "sora-2", "prompt": prompt, "seconds": "4", "size": "720x1280"},
        )
        if resp.status_code == 200:
            vid_id = resp.json().get("id")
            insert_at = spec.get("insert_at", 15 + i * 20) if isinstance(spec, dict) else 15 + i * 20
            job_ids.append({"id": vid_id, "index": i, "insert_at": insert_at})
            print(f"[SORA] Job {i}: {vid_id}", flush=True)
        else:
            print(f"[SORA] Job {i} failed to submit: {resp.status_code}", flush=True)

    # Poll until all complete (max 5 min)
    deadline = time.time() + 300
    completed = set()
    paths = []

    while len(completed) < len(job_ids) and time.time() < deadline:
        time.sleep(15)
        for job in job_ids:
            if job["id"] in completed:
                continue
            resp = requests.get(
                f"https://api.openai.com/v1/videos/{job['id']}",
                headers={"Authorization": f"Bearer {openai_key}"},
            )
            status = resp.json().get("status")
            if status == "completed":
                completed.add(job["id"])
                # Download
                dl_resp = requests.get(
                    f"https://api.openai.com/v1/videos/{job['id']}/content",
                    headers={"Authorization": f"Bearer {openai_key}"},
                )
                raw_path = os.path.join(workdir, f"sora{job['index']}_raw.mp4")
                with open(raw_path, "wb") as f:
                    f.write(dl_resp.content)
                # Resize
                out_path = os.path.join(workdir, f"sora{job['index']}.mp4")
                subprocess.run([
                    "ffmpeg", "-y", "-i", raw_path,
                    "-vf", f"scale={target_w}:{target_h}",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-an", out_path
                ], capture_output=True)
                paths.append({"path": out_path, "insert_at": job["insert_at"]})
                print(f"[SORA] Job {job['index']} completed", flush=True)
            elif status == "failed":
                completed.add(job["id"])
                print(f"[SORA] Job {job['index']} failed", flush=True)

    return paths


def filter_overlay_collisions(overlay_specs, sora_prompts):
    """Remove overlays que colidem com janelas de Sora cutaway (+0.5s buffer)."""
    sora_windows = []
    for spec in sora_prompts:
        t = spec.get("insert_at", 0) if isinstance(spec, dict) else 0
        sora_windows.append((t - 0.5, t + 4.5))  # 4s clip + 0.5s buffer each side

    filtered = []
    for ov in overlay_specs:
        ov_start = ov.get("insert_at", 0)
        ov_end = ov_start + ov.get("duration", 2.5)
        collides = False
        for sw_start, sw_end in sora_windows:
            if ov_start < sw_end and ov_end > sw_start:
                collides = True
                break
        if not collides:
            filtered.append(ov)
        else:
            print(f"[REELS] Overlay at {ov_start}s removed — collides with Sora cutaway", flush=True)
    return filtered


def build_timeline_map(segments, hook_dur):
    """Map original video timestamps to edited video timestamps.

    Edited video = hook (0..hook_dur) + segments concatenated.
    Segment 0 may start at hook_dur instead of its original start.
    Returns remap(orig_t) -> edited_t or None if outside included segments.
    """
    edited_pos = hook_dur
    intervals = []  # (orig_start, orig_end, edited_start)

    for i, seg in enumerate(segments):
        s = seg["start"] if isinstance(seg, dict) else seg[0]
        e = seg["end"] if isinstance(seg, dict) else seg[1]
        if i == 0 and s < hook_dur:
            s = hook_dur
        dur = e - s
        if dur <= 0:
            continue
        intervals.append((s, e, edited_pos))
        edited_pos += dur

    def remap(orig_t):
        for orig_s, orig_e, ed_s in intervals:
            if orig_s <= orig_t < orig_e:
                return ed_s + (orig_t - orig_s)
        return None

    return remap


def generate_overlay_images(overlay_specs, provider, openai_key, gemini_key, workdir, openrouter_key=None):
    """Gera 8+ overlay images via provider escolhido. Pula falhas sem matar o pipeline."""
    results = []
    for i, spec in enumerate(overlay_specs):
        prompt = spec.get("prompt", "")
        if not prompt:
            continue
        try:
            path = generate_image(provider, prompt, openai_key, gemini_key, workdir, f"overlay_{i}.png", openrouter_key=openrouter_key)
            results.append({
                "path": path,
                "insert_at": spec.get("insert_at", 0),
                "duration": spec.get("duration", 2.5),
                "mode": spec.get("mode", "blur_overlay"),
            })
            print(f"[REELS] Overlay image {i} generated: {prompt[:50]}...", flush=True)
        except Exception as e:
            print(f"[REELS] Overlay image {i} failed (skipping): {e}", flush=True)
        time.sleep(2)  # rate limit
    return results


def build_hook_frames(W, H, hook_img_a_path, hook_img_b_path, line1, line2, video_path, workdir, banner_color=None):
    """PNG RGBA full canvas: banner superior personalizável + imagem decorativa ocupando metade inferior.
    Vídeo entra por baixo no edit_video (overlay). Mantém assinatura legada."""
    import numpy as np
    import re as _re

    # Resolver cor do banner: hex -> RGB, padrão laranja
    def hex_to_rgb(hex_color):
        h = hex_color.lstrip("#")
        if _re.match(r'^[0-9A-Fa-f]{6}$', h):
            return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
        return None

    banner_rgb = (255, 140, 0)  # padrão laranja
    if banner_color:
        parsed = hex_to_rgb(banner_color)
        if parsed:
            banner_rgb = parsed
    banner_fill = banner_rgb + (255,)  # adiciona alpha=255

    # Imagem decorativa: ocupa toda a metade inferior do canvas (W x H/2)
    img_h = H // 2
    img_y = H - img_h

    # Banner proporcional, posicionado no terço superior
    BANNER_W = int(W * 0.851)
    BANNER_H = int(H * 0.115)
    BANNER_CX = W // 2
    BANNER_TOP_Y = int(H * 0.18)
    BANNER_R = int(W * 0.025)
    banner_x1 = BANNER_CX - BANNER_W // 2
    banner_y1 = BANNER_TOP_Y
    banner_x2 = banner_x1 + BANNER_W - 1
    banner_y2 = banner_y1 + BANNER_H - 1

    def find_font_size(text, max_w, target_h, min_size=20, max_size=120):
        for size in range(max_size, min_size, -1):
            font = ImageFont.truetype(FONT_PATH, size)
            bb = font.getbbox(text)
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            if tw <= max_w and th <= target_h:
                return font, tw, th, bb[1]
        font = ImageFont.truetype(FONT_PATH, min_size)
        bb = font.getbbox(text)
        return font, bb[2] - bb[0], bb[3] - bb[1], bb[1]

    usable_w = BANNER_W - 40
    line1_target_h = int(BANNER_H * 0.33)
    line2_target_h = int(BANNER_H * 0.36)
    font1, tw1, th1, yo1 = find_font_size(line1, usable_w, line1_target_h)
    font2, tw2, th2, yo2 = find_font_size(line2, usable_w, line2_target_h)
    gap_font = int(BANNER_H * 0.045)
    total_th = th1 + gap_font + th2
    text_start_y = banner_y1 + (BANNER_H - total_th) // 2

    paths = []
    for idx, img_path in enumerate([hook_img_a_path, hook_img_b_path]):
        # Canvas RGBA transparente
        canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))

        if img_path and os.path.exists(img_path):
            # Imagem decorativa opaca, cobrindo a metade inferior do canvas (fit-cover via resize+crop)
            decor_src = Image.open(img_path).convert("RGBA")
            sw, sh = decor_src.size
            target_aspect = W / img_h
            src_aspect = sw / sh
            if src_aspect > target_aspect:
                # Imagem mais larga que o alvo: scale por altura, crop horizontal
                new_h = img_h
                new_w = int(sw * (img_h / sh))
                decor = decor_src.resize((new_w, new_h))
                x0 = (new_w - W) // 2
                decor = decor.crop((x0, 0, x0 + W, new_h))
            else:
                new_w = W
                new_h = int(sh * (W / sw))
                decor = decor_src.resize((new_w, new_h))
                y0 = (new_h - img_h) // 2
                decor = decor.crop((0, y0, new_w, y0 + img_h))
            canvas.paste(decor, (0, img_y), decor)

        # Banner laranja superior
        draw = ImageDraw.Draw(canvas)
        draw.rounded_rectangle(
            [(banner_x1, banner_y1), (banner_x2, banner_y2)],
            radius=BANNER_R, fill=banner_fill
        )
        draw.text((BANNER_CX - tw1 // 2, text_start_y - yo1), line1, fill="white", font=font1)
        draw.text((BANNER_CX - tw2 // 2, text_start_y + th1 + gap_font - yo2), line2, fill="white", font=font2)

        suffix = "a" if idx == 0 else "b"
        out_path = os.path.join(workdir, f"hook_frame_{suffix}.png")
        canvas.save(out_path)
        paths.append(out_path)

    # Mantém retorno legado mas valores não são mais usados (vídeo é full no novo layout)
    return paths[0], paths[1], 0, 0


def edit_video(video_path, W, H, hook_frame_a_path, hook_frame_b_path,
               video_start_y, crop_top, segments, zoom_pattern, sora_specs, workdir):
    """Edita vídeo com ffmpeg puro: hook + zoom corte seco + cutaways. Baixo uso de RAM."""
    HOOK_DUR = 5.0
    IMG_SWITCH = 2.5

    # --- 1. Criar hook clip (5s) com ffmpeg ---
    # Hook: vídeo live full-screen + PNG RGBA (banner + imagem com fade) sobreposto
    hook_a_clip = os.path.join(workdir, "hook_a.mp4")
    hook_b_clip = os.path.join(workdir, "hook_b.mp4")

    for idx, (img_path, out_path, dur) in enumerate([
        (hook_frame_a_path, hook_a_clip, IMG_SWITCH),
        (hook_frame_b_path, hook_b_clip, HOOK_DUR - IMG_SWITCH),
    ]):
        offset = 0 if idx == 0 else IMG_SWITCH
        r = subprocess.run([
            "ffmpeg", "-y",
            "-loop", "1", "-i", img_path, "-t", str(dur),
            "-noautorotate", "-ss", str(offset), "-i", video_path, "-t", str(dur),
            "-filter_complex",
            f"[1:v]scale={W}:{H},setsar=1[bg];"
            f"[0:v]format=rgba[ov];"
            f"[bg][ov]overlay=0:0[out]",
            "-map", "[out]", "-map", "1:a?",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "128k",
            "-r", "30", out_path
        ], capture_output=True, text=True)
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            tail = "\n".join((r.stderr or "").splitlines()[-25:])
            print(f"[REELS] hook clip {idx} failed (rc={r.returncode}). stderr_tail:\n{tail}", flush=True)

    # Concatenar hook A + hook B
    hook_path = os.path.join(workdir, "hook.mp4")
    hook_list = os.path.join(workdir, "hook_list.txt")
    with open(hook_list, "w") as f:
        f.write(f"file '{hook_a_clip}'\nfile '{hook_b_clip}'\n")
    r = subprocess.run([
        "ffmpeg", "-y", "-noautorotate", "-f", "concat", "-safe", "0", "-i", hook_list,
        "-c", "copy", hook_path
    ], capture_output=True, text=True)
    if not os.path.exists(hook_path) or os.path.getsize(hook_path) == 0:
        tail = "\n".join((r.stderr or "").splitlines()[-25:])
        print(f"[REELS] hook concat failed (rc={r.returncode}). stderr_tail:\n{tail}", flush=True)

    # --- 2. Criar segmentos com zoom (ffmpeg) ---
    seg_paths = []
    # Obter duração do vídeo
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", video_path],
        capture_output=True, text=True
    )
    video_duration = float(json.loads(probe.stdout)["format"]["duration"])

    for i, seg in enumerate(segments):
        start = seg["start"] if isinstance(seg, dict) else seg[0]
        end = seg["end"] if isinstance(seg, dict) else seg[1]
        # First segment: skip content already used by hook (avoids audio/video repeating)
        if i == 0 and start < HOOK_DUR:
            start = HOOK_DUR
        end = min(end, video_duration)
        if start >= video_duration:
            break
        dur = end - start
        if dur <= 0:
            continue
        zoom = zoom_pattern[i % len(zoom_pattern)]
        seg_path = os.path.join(workdir, f"seg_{i}.mp4")

        if zoom == 1.0:
            # Sem zoom — corte direto
            r = subprocess.run([
                "ffmpeg", "-y", "-noautorotate", "-ss", str(start), "-t", str(dur), "-i", video_path,
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-c:a", "aac", "-b:a", "128k", "-r", "30", seg_path
            ], capture_output=True, text=True)
        else:
            # Zoom corte seco via ffmpeg: scale up + crop center
            zw = math.ceil(W * zoom)
            zh = math.ceil(H * zoom)
            zw += zw % 2
            zh += zh % 2
            r = subprocess.run([
                "ffmpeg", "-y", "-noautorotate", "-ss", str(start), "-t", str(dur), "-i", video_path,
                "-vf", f"scale={zw}:{zh},crop={W}:{H}",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-c:a", "aac", "-b:a", "128k", "-r", "30", seg_path
            ], capture_output=True, text=True)

        if os.path.exists(seg_path) and os.path.getsize(seg_path) > 0:
            seg_paths.append(seg_path)
        else:
            tail = "\n".join((r.stderr or "").splitlines()[-25:])
            print(f"[REELS] seg {i} (zoom={zoom}, {start:.2f}-{end:.2f}s) failed (rc={r.returncode}). stderr_tail:\n{tail}", flush=True)

    # --- 3. Concatenar tudo: hook + segmentos ---
    concat_list = os.path.join(workdir, "concat_list.txt")
    with open(concat_list, "w") as f:
        f.write(f"file '{hook_path}'\n")
        for sp in seg_paths:
            f.write(f"file '{sp}'\n")

    out_path = os.path.join(workdir, "reels_noCaption.mp4")
    r = subprocess.run([
        "ffmpeg", "-y", "-noautorotate", "-f", "concat", "-safe", "0", "-i", concat_list,
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "128k", "-r", "30",
        out_path
    ], capture_output=True, text=True)
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        tail = "\n".join((r.stderr or "").splitlines()[-30:])
        with open(concat_list, "r") as f:
            cl = f.read()
        raise RuntimeError(
            f"edit_video: concat final falhou (rc={r.returncode}). "
            f"hook_path_exists={os.path.exists(hook_path)} size={os.path.getsize(hook_path) if os.path.exists(hook_path) else 0}; "
            f"seg_paths_count={len(seg_paths)}; concat_list:\n{cl}\nstderr_tail:\n{tail}"
        )

    # --- 4. Sora cutaways (overlay) with Ken Burns ---
    if sora_specs:
        for si, spec in enumerate(sora_specs):
            fpath = spec["path"]
            insert_at = spec["insert_at"]
            if not os.path.exists(fpath):
                continue
            temp_out = os.path.join(workdir, "temp_overlay.mp4")
            sora_dur_probe = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", fpath],
                capture_output=True, text=True
            )
            try:
                sora_dur = float(json.loads(sora_dur_probe.stdout)["format"]["duration"])
            except:
                continue
            # Ken Burns: slow push-in 1.0→1.06x over clip duration
            # IMPORTANT: d=1 (1 output frame per input frame). d>1 multiplies frames!
            kb_total = int(sora_dur * 30)
            zoom_step = 0.06 / max(kb_total, 1)
            fade_out_st = max(sora_dur - 0.3, 0)
            subprocess.run([
                "ffmpeg", "-y", "-noautorotate", "-i", out_path, "-i", fpath,
                "-filter_complex",
                f"[1:v]scale={W}:{H},"
                f"zoompan=z='min(1+{zoom_step:.6f}*on,1.06)'"
                f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                f":d=1:s={W}x{H}:fps=30,"
                f"fade=in:st=0:d=0.3:alpha=1,"
                f"fade=out:st={fade_out_st:.2f}:d=0.3:alpha=1,"
                f"setpts=PTS+{insert_at}/TB[ov];"
                f"[0:v][ov]overlay=enable='between(t,{insert_at},{insert_at + sora_dur})'[out]",
                "-map", "[out]", "-map", "0:a",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-c:a", "copy", "-shortest", temp_out
            ], capture_output=True)
            if os.path.exists(temp_out) and os.path.getsize(temp_out) > 0:
                os.replace(temp_out, out_path)
                print(f"[REELS] Sora cutaway {si} applied with Ken Burns at {insert_at}s", flush=True)

    print(f"[REELS] Video edited: {out_path}", flush=True)
    return out_path


def add_transition_effects(seg_path, W, H, workdir, seg_index):
    """Flash branco nos primeiros 0.15s do segmento. Single-pass re-encode (sem desync)."""
    final_path = os.path.join(workdir, f"seg_trans_{seg_index}.mp4")

    try:
        # Single pass: aplica fade from white nos primeiros 0.15s do segmento inteiro
        subprocess.run([
            "ffmpeg", "-y", "-noautorotate", "-i", seg_path,
            "-vf", f"fade=in:st=0:d=0.15:color=white",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "128k", "-r", "30",
            final_path
        ], capture_output=True, check=True)

        if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
            print(f"[REELS] Transition effect applied to segment {seg_index}", flush=True)
            return final_path
    except Exception as e:
        print(f"[REELS] Transition effect failed for segment {seg_index}: {e}", flush=True)

    return seg_path  # fallback to original


def _prepare_overlay_image(img_path, W, H, workdir, idx):
    """Pré-processa imagem decorativa: fit-cover para W x 60% H (faixa inferior).
    Retorna (caminho_imagem, caminho_mascara) para composição profissional de alpha."""
    img_h = int(H * 0.60)
    src = Image.open(img_path).convert("RGB")
    sw, sh = src.size
    target_aspect = W / img_h
    src_aspect = sw / sh
    if src_aspect > target_aspect:
        new_h = img_h
        new_w = int(sw * (img_h / sh))
        img = src.resize((new_w, new_h), Image.LANCZOS)
        x0 = (new_w - W) // 2
        img = img.crop((x0, 0, x0 + W, new_h))
    else:
        new_w = W
        new_h = int(sh * (W / sw))
        img = src.resize((new_w, new_h), Image.LANCZOS)
        y0 = (new_h - img_h) // 2
        img = img.crop((0, y0, new_w, y0 + img_h))

    # Salva a imagem base sem alpha
    img_out_path = os.path.join(workdir, f"overlay_img_{idx}.png")
    img.save(img_out_path, "PNG")

    # Cria a máscara de alpha grayscale (Preto = transparente, Branco = opaco)
    mask = Image.new("L", (W, img_h), 255)
    mask_data = mask.load()
    fade_height = 600  # Fade longo e orgânico como sugerido pelo usuário
    for y in range(min(fade_height, img_h)):
        factor = y / float(fade_height)
        val = int(255 * factor)
        for x in range(W):
            mask_data[x, y] = val

    # Aplica Gaussian Blur na máscara para "derreter" a borda
    from PIL import ImageFilter
    mask = mask.filter(ImageFilter.GaussianBlur(radius=30))

    # Reduz a opacidade máxima para 90% para integrar melhor com o fundo
    mask_data = mask.load()
    for y in range(img_h):
        for x in range(W):
            mask_data[x, y] = int(mask_data[x, y] * 0.90)

    mask_out_path = os.path.join(workdir, f"overlay_mask_{idx}.png")
    mask.save(mask_out_path, "PNG")

    return img_out_path, mask_out_path


def apply_image_overlays(video_path, overlay_data, W, H, workdir):
    """Aplica image overlays no estilo 'split storytelling' com pipeline de alpha profissional:
    - Combina imagem e máscara via alphamerge
    - Aplica fade temporal preservando o alpha
    - Sobrepõe usando format=auto para não achatar o alpha."""
    if not overlay_data:
        return video_path

    current_path = video_path
    img_h = int(H * 0.60)
    overlay_y = H - img_h  # Começa em 40% da tela (768px)

    for idx, ov in enumerate(overlay_data):
        img_prepped, mask_prepped = _prepare_overlay_image(ov["path"], W, H, workdir, str(idx))
        t_start = ov["insert_at"]
        dur = ov["duration"]
        t_end = t_start + dur
        fade_d = min(0.4, dur / 3)
        temp_out = os.path.join(workdir, f"overlay_pass_{idx}.mp4")

        # Filtro Profissional:
        # 1. Garante formatos corretos para imagem e máscara
        # 2. alphamerge combina gerando alpha real
        # 3. fade com alpha=1 preserva a transparência
        # 4. overlay com format=rgb força a composição em RGB (preserva alpha)
        # 5. Só converte para yuv420p no final para o encoder
        filt = (
            f"[1:v]format=rgba[img_rgba];"
            f"[2:v]format=gray[mask_gray];"
            f"[img_rgba][mask_gray]alphamerge,format=rgba[img_alpha];"
            f"[img_alpha]scale={W}:{img_h},setsar=1,"
            f"fade=in:st=0:d={fade_d:.3f}:alpha=1,"
            f"fade=out:st={dur - fade_d:.3f}:d={fade_d:.3f}:alpha=1[img_animated];"
            f"[0:v][img_animated]overlay=0:{overlay_y}:enable='between(t,{t_start},{t_end})':format=rgb,"
            f"format=yuv420p[vout]"
        )

        try:
            cmd = [
                "ffmpeg", "-y",
                "-noautorotate", "-i", current_path,
                "-loop", "1", "-t", str(dur + 1), "-i", img_prepped,
                "-loop", "1", "-t", str(dur + 1), "-i", mask_prepped,
                "-filter_complex", filt,
                "-map", "[vout]", "-map", "0:a",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-c:a", "copy", temp_out
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if os.path.exists(temp_out) and os.path.getsize(temp_out) > 0:
                current_path = temp_out
                print(f"[REELS] Overlay {idx + 1}/{len(overlay_data)} applied (professional alpha blend)", flush=True)
            else:
                tail = "\n".join(result.stderr.splitlines()[-20:])
                print(
                    f"[REELS] Overlay {idx + 1} failed (rc={result.returncode}), stderr_tail:\n{tail}",
                    flush=True,
                )
        except Exception as e:
            print(f"[REELS] Overlay {idx + 1} error: {e}", flush=True)

    return current_path



def generate_sfx_pop(workdir):
    """Gera SFX soft pop programaticamente via ffmpeg (sine wave + fade)."""
    sfx_path = os.path.join(workdir, "sfx_pop.wav")
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", "sine=frequency=800:duration=0.08",
        "-af", "afade=in:st=0:d=0.02,afade=out:st=0.04:d=0.04",
        sfx_path
    ], capture_output=True, check=True)
    return sfx_path


def build_sfx_track(sfx_pop_path, timestamps, total_duration, workdir):
    """Constroi trilha SFX posicionando pops nos timestamps dados."""
    if not timestamps:
        return None

    sfx_track_path = os.path.join(workdir, "sfx_track.wav")

    # Build adelay filter chain: duplicate pop at each timestamp
    inputs = []
    filter_parts = []
    for i, ts in enumerate(timestamps):
        inputs.extend(["-i", sfx_pop_path])
        delay_ms = int(ts * 1000)
        filter_parts.append(f"[{i}:a]adelay={delay_ms}|{delay_ms}[d{i}]")

    # Mix all delayed pops together
    mix_inputs = "".join(f"[d{i}]" for i in range(len(timestamps)))
    filter_parts.append(f"{mix_inputs}amix=inputs={len(timestamps)}:duration=longest:normalize=0[sfx]")

    # Pad/trim to total_duration
    filter_parts.append(f"[sfx]apad=whole_dur={total_duration}[out]")

    full_filter = ";".join(filter_parts)

    try:
        cmd = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", full_filter,
            "-map", "[out]", "-t", str(total_duration),
            sfx_track_path
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        if os.path.exists(sfx_track_path) and os.path.getsize(sfx_track_path) > 0:
            print(f"[REELS] SFX track built with {len(timestamps)} pops", flush=True)
            return sfx_track_path
    except Exception as e:
        print(f"[REELS] SFX track build failed: {e}", flush=True)

    return None


def collect_sfx_timestamps(segments, overlay_data, hook_dur):
    """Coleta timestamps para SFX pops: transicoes de segmento + overlays."""
    timestamps = []

    # Transition timestamps (start of each segment except first)
    cumulative = hook_dur
    for i, seg in enumerate(segments):
        start = seg["start"] if isinstance(seg, dict) else seg[0]
        end = seg["end"] if isinstance(seg, dict) else seg[1]
        # Account for segment 0 adjustment (edit_video skips before hook_dur)
        if i == 0 and start < hook_dur:
            start = hook_dur
        dur = end - start
        if dur <= 0:
            continue
        if i > 0:
            timestamps.append(cumulative)
        cumulative += dur

    # Overlay timestamps (already remapped to edited timeline)
    for ov in overlay_data:
        timestamps.append(ov["insert_at"])

    timestamps.sort()
    return timestamps


def generate_captions(video_path, openai_key, W, H, workdir, groq_key=None, openrouter_key=None, caption_color=None, caption_position=None):
    """Gera captions ASS karaokê a partir do vídeo renderizado."""
    transcription = transcribe_whisper(video_path, openai_key, workdir, groq_key=groq_key, openrouter_key=openrouter_key)
    words = transcription.get("words", [])

    FONT_SIZE = max(int(60 * W / 1080), 20)
    WORDS_PER_LINE = 5

    # Cor da legenda: hex -> ASS ABGR
    primary_colour = "&H00FFFFFF"  # padrão branco
    if caption_color:
        import re as _re
        hex_color = caption_color.lstrip("#")
        if _re.match(r'^[0-9A-Fa-f]{6}$', hex_color):
            r = hex_color[0:2]
            g = hex_color[2:4]
            b = hex_color[4:6]
            primary_colour = f"&H00{b}{g}{r}".upper()
        elif _re.match(r'^&H[0-9A-Fa-f]{8}$', caption_color, _re.IGNORECASE):
            primary_colour = caption_color.upper()

    # Posição vertical da legenda
    margin_v = int(H * 0.15)  # padrão: bottom
    pos_norm = (caption_position or "").lower().strip()
    if pos_norm in ("middle", "meio"):
        margin_v = int(H * 0.50)
    elif pos_norm in ("below_middle", "abaixo_do_meio"):
        margin_v = int(H * 0.30)
    elif pos_norm in ("bottom", "baixo"):
        margin_v = int(H * 0.15)

    lines = []
    for i in range(0, len(words), WORDS_PER_LINE):
        lines.append(words[i:i + WORDS_PER_LINE])

    def ts_to_ass(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    ass_content = f"""[Script Info]
Title: Karaoke Captions
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,Helvetica Neue,{FONT_SIZE},{primary_colour},&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,1,0,1,5,0,2,30,30,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []
    for line_words in lines:
        start = line_words[0]["start"]
        end = line_words[-1]["end"]
        formatted = ""
        for j, w in enumerate(line_words):
            dur_cs = int((w["end"] - w["start"]) * 100)
            word_text = w["word"].strip()
            prefix = " " if j > 0 else ""
            formatted += f"{prefix}{{\\kf{dur_cs}}}{word_text}"
        events.append(f"Dialogue: 0,{ts_to_ass(start)},{ts_to_ass(end)},Karaoke,,0,0,0,,{formatted}")

    ass_content += "\n".join(events) + "\n"
    ass_path = os.path.join(workdir, "captions.ass")
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_content)

    return ass_path


def burn_captions_and_music(video_path, ass_path, workdir, sfx_track_path=None, music_path=None, music_volume=0.15):
    """Burn ASS captions + static audio mix (voz + musica + SFX) using amerge+pan for stable levels."""
    with_caption = os.path.join(workdir, "reels_withCaption.mp4")

    if ass_path:
        subprocess.run([
            "ffmpeg", "-y", "-noautorotate", "-i", video_path,
            "-vf", f"ass={ass_path}",
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-pix_fmt", "yuv420p", "-c:a", "copy",
            with_caption
        ], capture_output=True, check=True)
    else:
        shutil.copy(video_path, with_caption)

    final_path = os.path.join(workdir, "REELS_FINAL.mp4")

    if music_path is None:
        music_path = MUSIC_PATH

    has_music = music_path and os.path.exists(music_path)
    has_sfx = sfx_track_path and os.path.exists(sfx_track_path)

    vol = max(0.0, min(1.0, float(music_volume)))

    if has_music and has_sfx:
        print("[REELS] 3-audio mix: voice + music + SFX", flush=True)
        duration = get_duration(with_caption)
        fade_start = max(0.0, duration - 1.5)
        subprocess.run([
            "ffmpeg", "-y", "-noautorotate",
            "-i", with_caption, "-i", music_path, "-i", sfx_track_path,
            "-filter_complex",
            f"[0:a]aformat=channel_layouts=stereo[v];"
            f"[1:a]volume={vol},afade=t=out:st={fade_start:.3f}:d=1.5,aformat=channel_layouts=stereo[m];"
            f"[2:a]volume=0.20,aformat=channel_layouts=stereo[sfx];"
            f"[v][m][sfx]amerge=inputs=3,pan=stereo|FL=c0+c2+c4|FR=c1+c3+c5[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", final_path
        ], capture_output=True, check=True)
    elif has_music:
        print("[REELS] 2-audio mix: voice + music", flush=True)
        duration = get_duration(with_caption)
        fade_start = max(0.0, duration - 1.5)
        subprocess.run([
            "ffmpeg", "-y", "-noautorotate",
            "-i", with_caption, "-i", music_path,
            "-filter_complex",
            f"[0:a]aformat=channel_layouts=stereo[v];"
            f"[1:a]volume={vol},afade=t=out:st={fade_start:.3f}:d=1.5,aformat=channel_layouts=stereo[m];"
            f"[v][m]amerge=inputs=2,pan=stereo|FL=c0+c2|FR=c1+c3[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", final_path
        ], capture_output=True, check=True)
    elif has_sfx:
        print("[REELS] 2-audio mix: voice + SFX", flush=True)
        subprocess.run([
            "ffmpeg", "-y", "-noautorotate",
            "-i", with_caption, "-i", sfx_track_path,
            "-filter_complex",
            "[0:a]aformat=channel_layouts=stereo[v];"
            "[1:a]volume=0.20,aformat=channel_layouts=stereo[sfx];"
            "[v][sfx]amerge=inputs=2,pan=stereo|FL=c0+c2|FR=c1+c3[a]",
            "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", final_path
        ], capture_output=True, check=True)
    else:
        print("[REELS] No music/SFX found, copying audio as-is", flush=True)
        shutil.copy(with_caption, final_path)

    return final_path


# ============================================================
# Fallback helpers (Groq + OpenRouter)
# ============================================================

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _groq_transcribe(audio_path, groq_key, model="whisper-large-v3"):
    """Transcrição via Groq (endpoint OpenAI-compat). Mesmo shape do OpenAI Whisper."""
    with open(audio_path, "rb") as f:
        resp = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {groq_key}"},
            files={"file": (os.path.basename(audio_path), f)},
            data={
                "model": model,
                "response_format": "verbose_json",
                "timestamp_granularities[]": ["word", "segment"],
                "language": "pt",
            },
            timeout=180,
        )
    if not resp.ok:
        raise RuntimeError(f"Groq transcribe {model} HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def _openrouter_chat(prompt, openrouter_key, model="openai/gpt-oss-120b:free", temperature=0.7):
    """Chat completion via OpenRouter. Retorna o conteúdo de texto da primeira choice."""
    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {openrouter_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        },
        timeout=120,
    )
    if not resp.ok:
        raise RuntimeError(f"OpenRouter chat {model} HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"]


def _openrouter_transcribe(audio_path, openrouter_key, workdir, model="openai/gpt-4o-audio-preview"):
    """Transcrição via OpenRouter chat-completions com input_audio. Word timings são estimados
    distribuindo as palavras uniformemente pela duração do áudio (Whisper-compatible shape)."""
    import base64

    # OpenRouter audio input aceita mp3/wav. Converter pra mp3.
    mp3_path = os.path.join(workdir, "audio_for_openrouter.mp3")
    subprocess.run([
        "ffmpeg", "-y", "-i", audio_path, "-vn", "-c:a", "libmp3lame", "-b:a", "64k", mp3_path
    ], capture_output=True, check=True)

    with open(mp3_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {openrouter_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Transcribe this audio verbatim in Portuguese. Output only the transcription text, no labels, no markdown."},
                    {"type": "input_audio", "input_audio": {"data": b64, "format": "mp3"}},
                ],
            }],
            "temperature": 0,
        },
        timeout=180,
    )
    if not resp.ok:
        raise RuntimeError(f"OpenRouter transcribe {model} HTTP {resp.status_code}: {resp.text[:300]}")
    text = resp.json()["choices"][0]["message"]["content"].strip()

    total_dur = get_duration(audio_path)
    word_list = text.split()
    n = len(word_list)
    words = []
    if n > 0:
        step = total_dur / n
        for i, w in enumerate(word_list):
            words.append({"word": w, "start": i * step, "end": (i + 1) * step})

    return {"text": text, "words": words, "segments": []}


def _openrouter_image(prompt, openrouter_key, workdir, filename, model="google/gemini-2.5-flash-image"):
    """Geração de imagem via OpenRouter. Espera resposta com image/* em message.images ou content."""
    import base64

    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {openrouter_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "modalities": ["image", "text"],
            "messages": [{
                "role": "user",
                "content": f"Imagem fotorrealista, luz natural, sem filtro, alta resolução, 16:9: {prompt}",
            }],
        },
        timeout=180,
    )
    if not resp.ok:
        raise RuntimeError(f"OpenRouter image {model} HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    msg = data["choices"][0]["message"]

    b64 = None
    for img in msg.get("images", []) or []:
        url = img.get("image_url", {}).get("url", "") if isinstance(img, dict) else ""
        if url.startswith("data:"):
            b64 = url.split(",", 1)[1]
            break
    if not b64:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in ("image_url", "image"):
                    url = part.get("image_url", {}).get("url") if part.get("type") == "image_url" else part.get("image", "")
                    if isinstance(url, str) and url.startswith("data:"):
                        b64 = url.split(",", 1)[1]
                        break
    if not b64:
        raise RuntimeError(f"OpenRouter image {model}: no inline image in response: {str(msg)[:300]}")

    path = os.path.join(workdir, filename)
    with open(path, "wb") as f:
        f.write(base64.b64decode(b64))
    return path
