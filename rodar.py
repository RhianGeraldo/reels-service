import os
import sys
import uuid
import requests
from dotenv import load_dotenv

# Carrega as variáveis de ambiente do .env
load_dotenv()

from lib.pipeline import run_pipeline
from lib.supabase_client import create_job, update_job

# =====================================================================
# CONFIGURAÇÕES (Altere a URL do vídeo aqui!)
# =====================================================================
VIDEO_URL = "https://drive.google.com/uc?export=download&id=1nKwp67CtdcxvBPU5BtAfbEKyGQeDgZEV"
USER_ID = "rhian-dev"

# OPÇÕES DE EDIÇÃO (Altere para True ou False conforme necessário)
REMOVE_SILENCES = True
GENERATE_CAPTIONS = True
GENERATE_OVERLAYS = False
GENERATE_SORA = False
DYNAMIC_EDITING = False
# =====================================================================


def get_user_keys(user_id):
    URL = os.environ.get('SUPABASE_URL')
    KEY = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
    if not URL or not KEY:
        print("❌ Erro: SUPABASE_URL ou SUPABASE_SERVICE_ROLE_KEY não encontrados no .env")
        return None
        
    HEADERS = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}
    
    r = requests.get(f'{URL}/rest/v1/user_settings', headers=HEADERS, params={'user_id': f'eq.{user_id}', 'select': '*'})
    if r.status_code == 200 and r.json():
        return r.json()[0]
    return None

def main():
    if VIDEO_URL == "COLOQUE_SUA_URL_AQUI":
        print("❌ Por favor, abra o arquivo rodar.py e coloque o link do vídeo na variável VIDEO_URL.")
        return

    print(f"🚀 Iniciando processo para o vídeo:\n{VIDEO_URL}\n")
    
    # 1. Busca as chaves do usuário no banco
    keys = get_user_keys(USER_ID)
    if not keys:
        print("❌ Falha ao buscar as chaves da API do usuário no Supabase.")
        return
    
    openai_key = keys.get("openai_api_key")
    gemini_key = keys.get("gemini_api_key")
    openrouter_key = keys.get("openrouter_api_key")
    groq_key = keys.get("groq_api_key")
    
    # 2. Cria um Job no banco para registrar o processo
    job_id = "local-" + os.urandom(4).hex()
    print(f"📝 Criando registro (job) {job_id}...")
    create_job(job_id, USER_ID, VIDEO_URL)

    # Função para printar o progresso na tela
    def progress_cb(pct, step):
        print(f"[PROGRESSO] {pct}% - {step}")
        update_job(job_id, progress=pct, step=step)

    # 3. Roda o Pipeline
    print("🎬 Rodando a edição (isso pode demorar)...\n")
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
            dynamic_editing=DYNAMIC_EDITING
        )
        
        print("\n🎉 VÍDEO EDITADO COM SUCESSO!")
        print(f"🔗 Link do vídeo final: {result['video_url']}")
        
        update_job(job_id, status="completed", progress=100, step="done", result=result)
        
    except Exception as e:
        print(f"\n❌ Falha na edição: {e}")
        update_job(job_id, status="failed", error=str(e))

if __name__ == "__main__":
    main()
