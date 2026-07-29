# Reels Editing Service — Documentacao Completa

## O que e

Servico que edita videos de Reels automaticamente usando IA. Recebe um video bruto, e entrega um Reels pronto com:
- Hook de 5s com imagens geradas por IA + banner animado
- Zoom "corte seco" (1.0x / 1.3x / 1.4x alternando)
- Videos cutaway gerados por Sora (IA da OpenAI)
- 8+ imagens overlay geradas por Gemini (Estilo CapCut com fusão)
- Legendas karaoke (palavra por palavra)
- Musica de fundo + efeitos sonoros
- Resolucao 1080x1920 (vertical)

---

## Arquitetura

```
[Cliente] → POST /edit → [Flask API] → [Background Thread]
                                              ↓
                                    1. Download video
                                    2. VFR → CFR (30fps)
                                    3. Remove silencios
                                    4. Transcreve (Whisper)
                                    5. IA analisa + planeja edicao
                                    6. Gera 2 imagens hook (Gemini)
                                    6b. Gera 8+ overlay images (Gemini)
                                    7. Gera 3 videos Sora (OpenAI)
                                    7b. Gera SFX pop
                                    8. Monta hook frames (PIL)
                                    9. Edita video (FFmpeg)
                                    9b. Aplica image overlays
                                    9c. Build trilha SFX
                                    10. Gera captions karaoke (ASS)
                                    11. Burn captions + 3-audio mix
                                    12. Upload pro Supabase Storage
                                              ↓
                                    [GET /status/:id] → progresso
```

**Stack**: Python 3.12, Flask, FFmpeg, PIL, Gunicorn, Docker

---

## Estrutura de Arquivos

```
reels-service/
├── app.py                    # Flask server (endpoints REST)
├── lib/
│   ├── __init__.py
│   ├── pipeline.py           # Pipeline completo de edicao (~1100 lines)
│   └── supabase_client.py    # Client Supabase (DB + Storage)
├── fonts/
│   └── Impact.ttf            # Fonte bundled (necessaria pro banner)
├── music/
│   └── epic_games.mp3        # Musica de fundo
├── requirements.txt
├── Dockerfile
└── .env.example
```

---

## Pre-requisitos

### APIs necessarias
- **OpenAI API Key** — para Whisper (transcricao), GPT-4o-mini (analise), Sora 2 (videos)
- **Gemini API Key** — para gerar imagens (hook + overlays)
- **Supabase** — banco de dados (jobs) + Storage (upload video final)

### Tabela no Supabase

Criar a tabela `reels_jobs`:

```sql
CREATE TABLE IF NOT EXISTS reels_jobs (
    id UUID PRIMARY KEY,
    user_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'processing',
    progress INTEGER DEFAULT 0,
    step TEXT DEFAULT 'starting',
    video_url TEXT,
    result JSONB,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

Criar a tabela `user_settings` (se nao existir):

```sql
CREATE TABLE IF NOT EXISTS user_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL UNIQUE,
    openai_api_key TEXT,
    gemini_api_key TEXT,
    instagram_username TEXT,
    instagram_full_name TEXT,
    instagram_profile_pic_url TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

O bucket `user-uploads` precisa existir no Supabase Storage (pode ser publico).

---

## Variaveis de Ambiente

```env
SUPABASE_URL=https://SEU_PROJETO.supabase.co
SUPABASE_SERVICE_ROLE_KEY=eyJ...sua_service_role_key...
PORT=3001
SERVICE_SECRET=uma-senha-secreta-qualquer
```

- `SUPABASE_URL`: URL do seu projeto Supabase
- `SUPABASE_SERVICE_ROLE_KEY`: Chave service_role (nao a anon key!)
- `PORT`: Porta do servidor (Railway define automaticamente, default 3001)
- `SERVICE_SECRET`: Token pra autenticar chamadas na API (pode inventar qualquer string segura)

---

## Rodando Local

### 1. Instalar dependencias

```bash
# Python 3.12+
pip install -r requirements.txt
pip install auto-editor

# FFmpeg (necessario!)
# macOS: brew install ffmpeg
# Ubuntu: apt install ffmpeg
```

### 2. Configurar .env

```bash
cp .env.example .env
# Editar .env com suas chaves
```

### 3. Rodar

```bash
python app.py
# Ou com gunicorn:
gunicorn --bind 0.0.0.0:3001 --timeout 600 --workers 1 app:app
```

---

## Deploy no Railway

### Opcao 1: Via Dashboard
1. Criar novo projeto no Railway
2. Conectar repo Git ou fazer upload
3. Railway detecta o Dockerfile automaticamente
4. Configurar env vars (Settings > Variables)
5. Deploy automatico

### Opcao 2: Via API (sem CLI)

```bash
# 1. Criar tarball do projeto
cd reels-service
tar czf /tmp/reels.tar.gz --exclude='.git' --exclude='.env' --exclude='__pycache__' .

# 2. Upload e deploy
curl -X POST \
  "https://backboard.railway.com/project/SEU_PROJECT_ID/environment/SEU_ENV_ID/up?serviceId=SEU_SERVICE_ID" \
  -H "Authorization: Bearer SEU_RAILWAY_TOKEN" \
  -H "Content-Type: application/gzip" \
  --data-binary @/tmp/reels.tar.gz
```

### Opcao 3: Via Railway CLI

```bash
npm install -g @railway/cli
railway login
railway link  # conectar ao projeto
railway up    # deploy
```

---

## Endpoints da API

### GET /health
Health check. Sem autenticacao.

```bash
curl https://SEU_DOMINIO/health
```

Resposta:
```json
{"status": "ok", "service": "reels-service", "jobs": 0}
```

### POST /edit
Inicia edicao de video (async). Retorna imediatamente com job_id.

```bash
curl -X POST https://SEU_DOMINIO/edit \
  -H "Authorization: Bearer SUA_SERVICE_SECRET" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "id-do-usuario",
    "video_url": "https://url-publica-do-video.mp4",
    "hook_line1": "GESTOR DE TRAFEGO",
    "hook_line2": "VAI ACABAR",
    "zoom_levels": [1.0, 1.3, 1.0, 1.4],
    "generate_sora": true
  }'
```

Parametros:
| Campo | Obrigatorio | Padrão | Descricao |
|-------|:-----------:|:------:|-----------|
| `user_id` | Sim | - | ID do usuario (deve existir em user_settings) |
| `video_url` | Sim | - | URL publica do video bruto (.mp4) |
| `dynamic_editing` | Nao | `true` | Ativa IA para hook, zoom e overlays (false = modo pass-through) |
| `remove_silences` | Nao | `true` | Remove silencios com auto-editor |
| `generate_captions` | Nao | `true` | Transcreve e adiciona legendas karaokê |
| `generate_overlays` | Nao | `true` | Gera e aplica imagens decorativas de overlay |
| `image_provider` | Nao | `"gemini"` | Provedor de imagens IA: `"gemini"` ou `"openai"` |
| `generate_sora` | Nao | `true` | Gerar videos B-Roll Sora (OpenAI) |
| `hook_line1` | Nao | `null` | Texto linha 1 do hook (IA gera se nao passar) |
| `hook_line2` | Nao | `null` | Texto linha 2 do hook (IA gera se nao passar) |
| `zoom_levels` | Nao | `[1.0, 1.5, 1.0, 1.6]` | Padrao de zoom alternado dos segmentos |
| `caption_color` | Nao | `null` | Cor Hexadecimal da legenda e banner do hook |
| `caption_position` | Nao | `null` | Posicao da legenda: `"middle"`, `"below_middle"`, `"bottom"` |
| `denoise_audio` | Nao | `true` | Aplica reducao de ruido no audio do video |
| `music_url` | Nao | `null` | URL da trilha sonora (YouTube, Drive, mp3 ou `"none"`. Se não informado, não adiciona música) |
| `music_volume` | Nao | `0.15` | Volume da musica de fundo (0.0 a 1.0) |
| `visual_filter` | Nao | `null` | Preset de cor: `"vibrant"`, `"cinematic"`, `"vintage"`, `"cool"`, `"b&w"` |
| `brightness` | Nao | `0.0` | Ajuste de brilho (-1.0 a 1.0) |
| `contrast` | Nao | `1.0` | Multiplicador de contraste (0.0 a 10.0) |
| `saturation` | Nao | `1.0` | Multiplicador de saturacao (0.0 a 10.0) |
| `sharpness` | Nao | `0.0` | Intensidade do filtro de nitidez (0.0 a 2.0) |

Resposta (HTTP 202):
```json
{"job_id": "uuid-do-job", "status": "processing"}
```

### GET /status/:job_id
Consulta progresso de um job.

```bash
curl -H "Authorization: Bearer SUA_SERVICE_SECRET" \
  https://SEU_DOMINIO/status/uuid-do-job
```

Resposta (em progresso):
```json
{
  "job_id": "uuid",
  "status": "processing",
  "progress": 42,
  "step": "generating_sora_videos"
}
```

Resposta (concluido):
```json
{
  "job_id": "uuid",
  "status": "completed",
  "progress": 100,
  "step": "done",
  "result": {
    "video_url": "https://supabase.../REELS_FINAL.mp4",
    "duration": 66.4,
    "resolution": "1080x1920",
    "hook_text": "GESTOR DE TRAFEGO\nVAI ACABAR",
    "transcript": "Se voce e o intermediario..."
  }
}
```

Steps possiveis (em ordem):
```
downloading_video → converting_cfr → removing_silences → transcribing →
analyzing_content → generating_hook_images → generating_overlay_images →
generating_sora_videos → generating_sfx → building_hook_frames →
editing_video → applying_image_overlays → building_sfx_track →
generating_captions → burning_captions → uploading → done
```

### GET /jobs
Lista jobs. Filtros opcionais.

```bash
# Todos os jobs de um usuario
curl -H "Authorization: Bearer SUA_SERVICE_SECRET" \
  "https://SEU_DOMINIO/jobs?user_id=id-do-usuario"

# Filtrar por status
curl -H "Authorization: Bearer SUA_SERVICE_SECRET" \
  "https://SEU_DOMINIO/jobs?status=completed&limit=10"
```

---

## Pipeline de Edicao — Detalhes Tecnicos

### 1. Download + VFR→CFR
- Baixa video da URL
- Converte Variable Frame Rate → Constant 30fps (evita desync audio/video)

### 2. Remocao de Silencios
- Usa `auto-editor` com margem de 0.15s
- Se auto-editor nao disponivel, pula esse passo

### 3. Transcricao (Whisper)
- OpenAI Whisper API (model: whisper-1)
- Se video > 25MB, extrai audio primeiro (aac 64k)
- Retorna: palavras com timestamps + texto completo

### 4. Analise de Conteudo (GPT-4o-mini)
- Analisa transcricao e gera plano de edicao:
  - 2 linhas de hook (CAPSLOCK, impactante)
  - 5-8 segmentos tematicos (max 85s total)
  - 2 prompts pra imagens hook (16:9, foto real)
  - 3 prompts pra videos Sora (cutaway)
  - 8+ prompts pra overlay images (Estilo CapCut com fusão)

### 5. Geracao de Imagens (Gemini / OpenAI)
- Gemini 3 Pro Image Preview ou DALL-E 3
- 2 imagens hook + 8+ overlay images geradas em **paralelo (ThreadPoolExecutor)**
- Retry: 3 tentativas com fallback para OpenRouter em caso de rate-limit
- Falha graceful: overlay que falha é pulado sem parar a edição

### 6. Geracao de Videos Sora
- OpenAI Sora 2 API
- 3 videos em paralelo
- Polling a cada 15s (max 5 min)
- Download + resize pra resolucao alvo

### 7. Montagem do Hook (PIL)
- **Imagem decorativa** no topo (16:9)
- **Video** embaixo (comeca em img_h = W*9/16)
- **Banner laranja** flutuando entre os dois:
  - Cor: RGB(255, 140, 0) (ou customizada via `caption_color`)
  - Dimensao: 85% da largura, 11.5% da altura
  - Rounded corners: 2.5% da largura
  - 2 linhas de texto Impact (branco)
  - Linha 2 maior que linha 1 (pra impacto)
  - Centralizado verticalmente no banner

### 8. Edicao de Video (FFmpeg)
- **Hook clip**: 5s (imagem A 0-2.5s + imagem B 2.5-5s)
  - 3-layer composite: hook frame bg + live video + banner crop on top
- **Segmentos com zoom**: scale up + crop center (corte seco)
- **Concatenacao**: hook + segmentos
- **Sora cutaways** sobrepostos com:
  - Ken Burns (zoom lento 1.0→1.06x)
  - CrossFade in/out de 0.3s

### 9. Image Overlays (Estilo CapCut — Passa Única)
- **Vídeo Base**: O vídeo original roda em tela cheia no fundo.
- **Sobreposição**: A imagem gerada pela IA é inserida na faixa inferior (ocupando 60% da tela).
- **Fusão (Feather)**: Aplica-se uma máscara de degradê (gradient mask) de 600px no topo da imagem para fundi-la suavemente com o vídeo, sem linhas duras.
- **Single-Pass FFmpeg**: Todas as sobreposições são aplicadas em **uma única passagem de filtro** (`filter_complex`), sem re-codificar o vídeo múltiplas vezes.

### 10. Captions Karaoke (ASS)
- Reutiliza os timestamps da 1ª transcrição Whisper com remapeamento (`remap_ts`), **eliminando a 2ª chamada da API**.
- Formato ASS v4+ (23 campos)
- Karaoke: `{\kf<duracao>}palavra` — destaca palavra por palavra
- Fonte Helvetica Neue, outline 5, posição customizável (`middle`, `below_middle`, `bottom`)

### 11. Mix de Audio (3 faixas)
- **Voz**: volume 1.0 (original)
- **Musica** (epic_games.mp3 ou customizada): volume configurável (default 0.15)
- **SFX** (pops nas transicoes): volume 0.35
- Fallback graceful: se musica/SFX nao disponivel, adapta

### 12. Parametros de Saida (Otimizados para CPU)
- Codec: H.264 (libx264)
- Preset: `superfast`
- CRF: `22` (alta velocidade com fidelidade visual perfeita)
- Bitrate audio: 128k/192k AAC
- Pixel format: yuv420p
- Flag: movflags +faststart (streaming otimizado)
- FPS: 30
- Resolucao: 1080x1920

---

## Fluxo de Autenticacao

1. Chamadas na API usam Bearer token no header:
   ```
   Authorization: Bearer SUA_SERVICE_SECRET
   ```
2. O servico aceita tanto `SERVICE_SECRET` quanto `SUPABASE_SERVICE_ROLE_KEY`
3. As chaves OpenAI e Gemini vem da tabela `user_settings` do Supabase (por user_id)
4. O usuario precisa ter suas chaves cadastradas antes de usar o servico

---

## Tabelas no Supabase

### reels_jobs
| Coluna | Tipo | Descricao |
|--------|------|-----------|
| id | UUID | PK, gerado pelo servico |
| user_id | TEXT | ID do usuario |
| status | TEXT | processing / completed / failed |
| progress | INTEGER | 0-100 |
| step | TEXT | Etapa atual |
| video_url | TEXT | URL do video original |
| result | JSONB | Resultado final (video_url, duration, etc) |
| error | TEXT | Mensagem de erro se falhou |
| created_at | TIMESTAMPTZ | Quando criou |
| updated_at | TIMESTAMPTZ | Ultima atualizacao |

### user_settings
| Coluna | Tipo | Descricao |
|--------|------|-----------|
| id | UUID | PK |
| user_id | TEXT | ID do usuario (unique) |
| openai_api_key | TEXT | Chave OpenAI |
| gemini_api_key | TEXT | Chave Gemini |
| instagram_username | TEXT | (opcional) |
| instagram_full_name | TEXT | (opcional) |
| instagram_profile_pic_url | TEXT | (opcional) |

---

## Troubleshooting

### Video nao renderiza
- Verificar se FFmpeg esta instalado: `ffmpeg -version`
- Verificar se as chaves da API estao corretas em `user_settings`

### Hook com banner cortado
- O banner usa dimensoes proporcionais (85% largura, 11.5% altura)
- Fonte Impact precisa estar em `fonts/Impact.ttf`

### Vídeo no overlay (Estilo CapCut)
- O vídeo principal fica no fundo em tela cheia.
- A imagem cobre a faixa inferior com fusão suave (feather) de 600px.

### Video lavado/branco
- Verificar se o video original e HDR. O servico nao faz conversao HDR→SDR
- Se necessario, converter antes: `ffmpeg -i input.mp4 -vf "zscale=..." output.mp4`

### Sora timeout
- Videos Sora tem timeout de 5 minutos
- Se falhar, o pipeline continua sem os cutaways

### auto-editor nao encontrado
- O servico funciona sem auto-editor (pula remocao de silencios)
- Pra instalar: `pip install auto-editor`

---

## Docker (Producao)

O Dockerfile inclui tudo necessario:

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir auto-editor

COPY . .
EXPOSE 3001

CMD ["gunicorn", "--bind", "0.0.0.0:3001", "--timeout", "600", "--workers", "1", "app:app"]
```

**Notas**:
- `--workers 1`: Cada job consome muita RAM/CPU. Nao escalar workers.
- `--timeout 600`: Jobs podem levar ate 10 minutos.
- A imagem Docker fica ~500MB (ffmpeg + python + deps)

---

## Custos das APIs (estimativa por video)

| API | Uso | Custo estimado |
|-----|-----|----------------|
| Whisper | 1 transcricao (~1 min) | ~$0.006 |
| GPT-4o-mini | 1 analise de conteudo | ~$0.01 |
| Gemini | 10 imagens (2 hook + 8 overlay) | ~$0.05 |
| Sora 2 | 3 videos de 4s | ~$0.45 |
| **Total** | | **~$0.51/video** |

Sem Sora (`generate_sora: false`): ~$0.06/video

---

## Limitacoes

- **Video final**: maximo ~90s (5s hook + 85s conteudo)
- **Resolucao**: sempre 1080x1920 (vertical)
- **Workers**: 1 por instancia (processar 1 video por vez)
- **Timeout gunicorn**: 600s (10 min) — jobs muito longos podem falhar
- **Sem HDR→SDR**: videos HDR podem sair lavados
- **Sora**: pode demorar ate 5 min pra gerar, ou falhar por sobrecarga
