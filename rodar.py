import os
import sys
import time
import requests
from dotenv import load_dotenv

# Carrega as variáveis de ambiente do .env
load_dotenv()

# =====================================================================
# CONFIGURAÇÕES DE EXECUÇÃO
# =====================================================================
# EXECUTION_MODE:
#   "api"          -> Testa via API HTTP (POST /edit + GET /status/<id>)
#   "local_direct" -> Executa o pipeline diretamente no script Python (sem passar pela API HTTP)
EXECUTION_MODE = "api"

# TARGET_ENV (Usado quando EXECUTION_MODE = "api"):
#   "production" -> Usa a URL de produção (https://editor.erriesse.com)
#   "local"      -> Usa a API local (http://localhost:3001)
#   "custom"     -> Usa a URL configurada em CUSTOM_API_URL
TARGET_ENV = "production"

# URLs da API
PRODUCTION_API_URL = "https://editor.erriesse.com"
LOCAL_API_URL = f"http://localhost:{os.environ.get('PORT', '3001')}"
CUSTOM_API_URL = os.environ.get("API_URL", "")

# VÍDEO E USUÁRIO
VIDEO_URL = "https://drive.google.com/uc?export=download&id=1nKwp67CtdcxvBPU5BtAfbEKyGQeDgZEV"
USER_ID = "rhian-dev"

# OPÇÕES DE EDIÇÃO
REMOVE_SILENCES = True
GENERATE_CAPTIONS = True
GENERATE_OVERLAYS = False
GENERATE_SORA = False
DYNAMIC_EDITING = False

# CONFIGURAÇÕES DE LEGENDA
# CAPTION_COLOR: Hexadecimal (ex: "#FF0000") ou None para legenda branca
CAPTION_COLOR = None
# CAPTION_POSITION: "middle", "below_middle" ou "bottom" (padrão)
CAPTION_POSITION = "below_middle"

# REMOÇÃO DE RUÍDO
DENOISE_AUDIO = True

# MÚSICA DE FUNDO
# MUSIC_URL: URL do YouTube/mp3, nome de arquivo em "music/", "none" ou None para sem música (padrão)
MUSIC_URL = None
MUSIC_VOLUME = 0.10

# FILTROS VISUAIS E AJUSTES DE IMAGEM
VISUAL_FILTER = "cinematic"  # "vibrant", "cinematic", "vintage", "cool", "b&w" ou None
BRIGHTNESS = 0.0
CONTRAST = 1.0
SATURATION = 1.0
SHARPNESS = 1.0
# =====================================================================


def get_api_url():
    if TARGET_ENV == "production":
        return PRODUCTION_API_URL.rstrip("/")
    elif TARGET_ENV == "local":
        return LOCAL_API_URL.rstrip("/")
    elif TARGET_ENV == "custom" and CUSTOM_API_URL:
        return CUSTOM_API_URL.rstrip("/")
    return os.environ.get("API_URL", PRODUCTION_API_URL).rstrip("/")


def get_auth_token():
    return os.environ.get("SERVICE_SECRET") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or ""


def run_via_api():
    api_url = get_api_url()
    token = get_auth_token()

    print("==================================================")
    print("🤖 REELS SERVICE — TESTE DA API HTTP")
    print("==================================================")
    print(f"🎯 Ambiente Alvo : {TARGET_ENV.upper()} ({api_url})")
    print(f"👤 Usuário       : {USER_ID}")
    print(f"📹 Vídeo URL     : {VIDEO_URL}")
    print("==================================================\n")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # 1. Testar endpoint /health
    print("🔍 1. Testando conexão com a API (/health)...")
    try:
        r_health = requests.get(f"{api_url}/health", headers=headers, timeout=10)
        if r_health.status_code == 200:
            print(f"   ✅ API Online! Status: {r_health.json()}\n")
        else:
            print(f"   ⚠️ Resposta do /health: HTTP {r_health.status_code} - {r_health.text}\n")
    except Exception as e:
        print(f"   ⚠️ Não foi possível consultar /health: {e}")
        print("   (Tentando prosseguir com POST /edit mesmo assim...)\n")

    # 2. Montar payload
    payload = {
        "user_id": USER_ID,
        "video_url": VIDEO_URL,
        "remove_silences": REMOVE_SILENCES,
        "generate_captions": GENERATE_CAPTIONS,
        "generate_overlays": GENERATE_OVERLAYS,
        "generate_sora": GENERATE_SORA,
        "dynamic_editing": DYNAMIC_EDITING,
        "caption_color": CAPTION_COLOR,
        "caption_position": CAPTION_POSITION,
        "denoise_audio": DENOISE_AUDIO,
        "music_url": MUSIC_URL,
        "music_volume": MUSIC_VOLUME,
        "visual_filter": VISUAL_FILTER,
        "brightness": BRIGHTNESS,
        "contrast": CONTRAST,
        "saturation": SATURATION,
        "sharpness": SHARPNESS,
    }

    # 3. Disparar requisição POST /edit
    print("🚀 2. Enviando requisição POST /edit...")
    try:
        response = requests.post(f"{api_url}/edit", json=payload, headers=headers, timeout=30)
    except Exception as e:
        print(f"❌ Erro na requisição HTTP POST: {e}")
        return

    if response.status_code not in (200, 201, 202):
        print(f"❌ API retornou erro HTTP {response.status_code}: {response.text}")
        return

    data = response.json()
    job_id = data.get("job_id")
    if not job_id:
        print(f"❌ Resposta da API não contém job_id: {data}")
        return

    print(f"   ✅ Job criado com sucesso! ID: {job_id}")
    print("⏳ 3. Acompanhando o progresso em tempo real (/status/<id>)...\n")

    # 4. Polling do status
    last_step = None
    last_progress = None

    while True:
        try:
            r_status = requests.get(f"{api_url}/status/{job_id}", headers=headers, timeout=15)
            if r_status.status_code == 200:
                job_info = r_status.json()
                status = job_info.get("status")
                progress = job_info.get("progress", 0)
                step = job_info.get("step", "")

                if step != last_step or progress != last_progress:
                    print(f"[PROGRESSO] {progress}% - {step}")
                    last_step = step
                    last_progress = progress

                if status == "completed":
                    result = job_info.get("result", {})
                    print("\n" + "=" * 50)
                    print("🎉 VÍDEO EDITADO COM SUCESSO PELA API!")
                    print("=" * 50)
                    print(f"🔗 Link do vídeo final: {result.get('video_url')}")
                    if result.get("duration"):
                        print(f"⏱️ Duração: {result.get('duration')}s")
                    break
                elif status == "failed":
                    error = job_info.get("error", "Erro desconhecido")
                    print(f"\n❌ Falha no processamento relatada pela API: {error}")
                    break
            else:
                print(f"⚠️ Erro ao verificar status: HTTP {r_status.status_code} - {r_status.text}")
        except Exception as e:
            print(f"⚠️ Erro de conexão durante polling de status: {e}")

        time.sleep(4)


def run_local_direct():
    from lib.pipeline import run_pipeline
    from lib.supabase_client import create_job, update_job, get_user_settings

    print("==================================================")
    print("💻 REELS SERVICE — EXECUÇÃO LOCAL DIRETA")
    print("==================================================")
    print(f"👤 Usuário   : {USER_ID}")
    print(f"📹 Vídeo URL : {VIDEO_URL}")
    print("==================================================\n")

    settings = get_user_settings(USER_ID)
    if not settings:
        print("❌ Falha ao buscar as chaves da API do usuário no Supabase.")
        return

    openai_key = os.environ.get("OPENAI_API_KEY") or settings.get("openai_api_key")
    gemini_key = os.environ.get("GEMINI_API_KEY") or settings.get("gemini_api_key")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY") or settings.get("openrouter_api_key")
    groq_key = os.environ.get("GROQ_API_KEY") or settings.get("groq_api_key")

    job_id = "local-" + os.urandom(4).hex()
    print(f"📝 Criando registro (job) {job_id}...")
    create_job(job_id, USER_ID, VIDEO_URL)

    def progress_cb(pct, step):
        print(f"[PROGRESSO] {pct}% - {step}")
        update_job(job_id, progress=pct, step=step)

    print("🎬 Rodando o pipeline diretamente...\n")
    try:
        result = run_pipeline(
            video_url=VIDEO_URL,
            user_id=USER_ID,
            openai_key=openai_key,
            gemini_key=gemini_key,
            openrouter_key=openrouter_key,
            groq_key=groq_key,
            generate_sora=GENERATE_SORA,
            image_provider="gemini",
            progress_callback=progress_cb,
            remove_silences=REMOVE_SILENCES,
            generate_captions_enabled=GENERATE_CAPTIONS,
            generate_overlays=GENERATE_OVERLAYS,
            dynamic_editing=DYNAMIC_EDITING,
            caption_color=CAPTION_COLOR,
            caption_position=CAPTION_POSITION,
            denoise_audio=DENOISE_AUDIO,
            music_url=MUSIC_URL,
            music_volume=MUSIC_VOLUME,
            visual_filter=VISUAL_FILTER,
            brightness=BRIGHTNESS,
            contrast=CONTRAST,
            saturation=SATURATION,
            sharpness=SHARPNESS,
        )

        print("\n🎉 VÍDEO EDITADO COM SUCESSO!")
        print(f"🔗 Link do vídeo final: {result['video_url']}")
        update_job(job_id, status="completed", progress=100, step="done", result=result)

    except Exception as e:
        print(f"\n❌ Falha na edição: {e}")
        update_job(job_id, status="failed", error=str(e))


def main():
    if VIDEO_URL == "COLOQUE_SUA_URL_AQUI":
        print("❌ Por favor, abra o arquivo rodar.py e coloque o link do vídeo na variável VIDEO_URL.")
        return

    if EXECUTION_MODE == "api":
        run_via_api()
    else:
        run_local_direct()


if __name__ == "__main__":
    main()
