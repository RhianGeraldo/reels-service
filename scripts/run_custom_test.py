"""
run_custom_test.py — Downloads video from Google Drive, uploads to Supabase, and runs the pipeline.
"""

import os
import sys
import requests
from dotenv import load_dotenv

from dotenv import load_dotenv
load_dotenv()

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.pipeline import run_pipeline
from lib.supabase_client import upload_to_storage, create_job, update_job

USER_ID = "rhian-dev"
DRIVE_FILE_ID = "1nKwp67CtdcxvBPU5BtAfbEKyGQeDgZEV"

def get_confirm_token(response):
    for key, value in response.cookies.items():
        if key.startswith('download_warning'):
            return value
    return None

def download_file_from_google_drive(id, destination):
    URL = "https://docs.google.com/uc?export=download"
    session = requests.Session()
    response = session.get(URL, params={'id': id}, stream=True)
    token = get_confirm_token(response)

    if token:
        params = {'id': id, 'confirm': token}
        response = session.get(URL, params=params, stream=True)

    CHUNK_SIZE = 32768
    with open(destination, "wb") as f:
        for chunk in response.iter_content(CHUNK_SIZE):
            if chunk:
                f.write(chunk)

def get_user_keys(user_id):
    URL = os.environ['SUPABASE_URL']
    KEY = os.environ['SUPABASE_SERVICE_ROLE_KEY']
    HEADERS = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
    
    r = requests.get(f'{URL}/rest/v1/user_settings', headers=HEADERS, params={'user_id': f'eq.{user_id}', 'select': '*'})
    if r.status_code == 200 and r.json():
        return r.json()[0]
    return None

def main():
    print("🚀 Starting custom test...")
    
    # 1. Get keys
    keys = get_user_keys(USER_ID)
    if not keys:
        print("❌ Failed to get user keys")
        return
    
    openai_key = keys.get("openai_api_key")
    gemini_key = keys.get("gemini_api_key")
    openrouter_key = keys.get("openrouter_api_key")
    groq_key = keys.get("groq_api_key")
    
    # 2. Download from Drive
    local_video = "temp_input.mp4"
    print(f"📥 Downloading video from Drive ID: {DRIVE_FILE_ID}...")
    try:
        download_file_from_google_drive(DRIVE_FILE_ID, local_video)
        print(f"✅ Downloaded to {local_video} ({os.path.getsize(local_video)} bytes)")
    except Exception as e:
        print(f"❌ Download failed: {e}")
        return

    # 3. Upload to Supabase Storage
    print("📤 Uploading input video to Supabase Storage...")
    try:
        storage_path = f"tests/input_{DRIVE_FILE_ID}.mp4"
        input_url = upload_to_storage("user-uploads", storage_path, local_video)
        print(f"✅ Uploaded: {input_url}")
    except Exception as e:
        print(f"❌ Upload failed: {e}")
        # fallback to local file URL if pipeline supported it, but it doesn't.
        # Let's try to serve it locally if upload fails?
        # Let's hope upload works.
        return
    finally:
        if os.path.exists(local_video):
            os.remove(local_video)

    # 4. Create Job in DB
    job_id = "test-" + os.urandom(4).hex()
    print(f"📝 Creating job {job_id}...")
    create_job(job_id, USER_ID, input_url)

    # 5. Run Pipeline
    print("🎬 Running pipeline...")
    
    def progress_cb(pct, step):
        print(f"[PROGRESS] {pct}% - {step}")
        update_job(job_id, progress=pct, step=step)

    try:
        result = run_pipeline(
            video_url=input_url,
            user_id=USER_ID,
            openai_key=openai_key,
            gemini_key=gemini_key,
            openrouter_key=openrouter_key,
            groq_key=groq_key,
            generate_sora=False,  # Set to False for faster/reliable testing unless requested
            image_provider="gemini",
            progress_callback=progress_cb
        )
        
        print("\n🎉 PIPELINE COMPLETED SUCCESSFULLY!")
        print(f"🔗 Video URL: {result['video_url']}")
        
        update_job(job_id, status="completed", progress=100, step="done", result=result)
        
    except Exception as e:
        print(f"\n❌ Pipeline failed: {e}")
        update_job(job_id, status="failed", error=str(e))

if __name__ == "__main__":
    main()
