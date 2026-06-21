# Documentação da API - Reels Service

Esta documentação detalha como integrar o **Reels Service** (Processador Automático de Vídeos) com o seu sistema **Roteiriza**.

A API foi projetada de forma assíncrona. Isso significa que você envia a requisição de edição e ela retorna imediatamente com um `job_id`. O processamento pesadíssimo acontece no servidor em segundo plano, e você usa o `job_id` para consultar o progresso.

---

## 1. Autenticação

Todas as rotas da API são protegidas e exigem um **Bearer Token** no Header `Authorization`. O Token é verificado e definido na variável de ambiente `SERVICE_SECRET` do seu servidor.

**Header Obrigatório:**
```http
Authorization: Bearer <SEU_SERVICE_SECRET>
```

---

## 2. Iniciar uma Edição de Vídeo

Endpoint responsável por colocar um novo vídeo na fila de processamento.

- **URL:** `/edit`
- **Método:** `POST`
- **Content-Type:** `application/json`

### Body da Requisição

| Parâmetro | Tipo | Obrigatório | Padrão | Descrição |
| :--- | :---: | :---: | :---: | :--- |
| `user_id` | `string` | Sim | - | O identificador do usuário do Roteiriza. Usado para buscar as chaves da OpenAI/Gemini/OpenRouter cadastradas no Supabase. |
| `video_url` | `string` | Sim | - | URL pública do vídeo bruto (.mp4). Suporta links diretos, Google Drive (`/file/d/...` ou `?id=...`), Dropbox e YouTube (somente para músicas). |
| `dynamic_editing` | `boolean` | Não | `true` | Se `true`, ativa a IA para achar cortes, colocar barras de hook e aplicar zooms dinâmicos. Se `false`, ativa o modo pass-through (rápido). |
| `remove_silences` | `boolean` | Não | `true` | Se `true`, usa o `auto-editor` para remover silêncios do áudio. |
| `generate_captions` | `boolean` | Não | `true` | Se `true`, transcreve com Whisper e aplica legenda karaokê. |
| `generate_overlays` | `boolean` | Não | `true` | Se `true`, gera imagens IA para sobrepor no vídeo (só funciona com `dynamic_editing: true`). |
| `image_provider` | `string` | Não | `"gemini"` | Qual IA gera as imagens: `"gemini"` ou `"openai"`. |
| `generate_sora` | `boolean` | Não | `true` | Habilita geração de B-Rolls via IA de vídeo (Sora/Luma/Runway). |
| `hook_line1` | `string` | Não | `null` | Força o texto da 1ª linha do Banner de Hook (ignora a IA). |
| `hook_line2` | `string` | Não | `null` | Força o texto da 2ª linha do Banner de Hook (ignora a IA). |
| `caption_color` | `string` | Não | `null` | Cor da legenda em Hexadecimal (ex: `"#FF0000"`, `"FF0000"`). Se `null`, o padrão é **branco**. |
| `caption_position` | `string` | Não | `null` | Posição vertical da legenda: `"middle"`, `"below_middle"`, `"bottom"` (padrão). |
| `denoise_audio` | `boolean` | Não | `true` | Se `true`, aplica filtro de redução de ruído neural (RNNoise) no áudio do vídeo. |
| `music_url` | `string` | Não | `null` | URL da trilha sonora: link do YouTube, Google Drive, Dropbox, link direto mp3/wav, nome de arquivo local em `music/`, ou `"none"` para desativar. Se `null`, usa a música padrão. |
| `music_volume` | `float` | Não | `0.15` | Volume da música de fundo (entre `0.0` e `1.0`). |
| `visual_filter` | `string` | Não | `null` | Preset criativo de cores: `"vibrant"`, `"cinematic"`, `"vintage"`, `"cool"`, `"b&w"`. `null` desativa. |
| `brightness` | `float` | Não | `0.0` | Ajuste de brilho (-1.0 a 1.0). Pode ser combinado com `visual_filter`. |
| `contrast` | `float` | Não | `1.0` | Multiplicador de contraste (0.0 a 10.0). |
| `saturation` | `float` | Não | `1.0` | Multiplicador de saturação (0.0 a 10.0). |
| `sharpness` | `float` | Não | `0.0` | Intensidade do filtro de nitidez, filtro `unsharp` (0.0 a 2.0). `0.0` desativa. |

### Exemplo de Requisição (Pass-through com filtro cinematográfico e legenda branca)
```bash
curl -X POST https://api-seuservidor.com/edit \
  -H "Authorization: Bearer SEU_SERVICE_SECRET" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "usuario-roteiriza-123",
    "video_url": "https://drive.google.com/file/d/SEU_ID/view?usp=sharing",
    "dynamic_editing": false,
    "remove_silences": true,
    "generate_captions": true,
    "caption_color": null,
    "caption_position": "below_middle",
    "denoise_audio": true,
    "music_url": "https://www.youtube.com/watch?v=OPugs48z2GU",
    "music_volume": 0.10,
    "visual_filter": "cinematic",
    "sharpness": 1.0
  }'
```

### Exemplo de Resposta (Sucesso)
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "processing"
}
```

---

## 3. Consultar Progresso do Vídeo

Após iniciar o processamento, seu frontend ou backend do Roteiriza pode bater nesta rota a cada 5 segundos para exibir o status e a barra de progresso para o cliente.

- **URL:** `/status/<job_id>`
- **Método:** `GET`
- **Cabeçalhos:** `Authorization: Bearer <SEU_SERVICE_SECRET>`

### Exemplo de Resposta (Em Andamento)
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "processing",
  "progress": 42,
  "step": "generating_captions"
}
```

### Exemplo de Resposta (Concluído com Sucesso)
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "progress": 100,
  "step": "done",
  "result": {
    "duration": 34.5,
    "hook_text": "TEXTO DO TOPO\nTEXTO DE BAIXO",
    "resolution": "1080x1920",
    "transcript": "Trecho inicial do que foi dito no vídeo...",
    "video_url": "https://seu-supabase.supabase.co/storage/v1/object/public/.../REELS_FINAL.mp4"
  }
}
```
> O arquivo final ficará disponível na propriedade `result.video_url`. É este o link que você deve salvar no banco do Roteiriza!

### Exemplo de Resposta (Falha)
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "failed",
  "progress": 28,
  "step": "generating_captions",
  "error": "OpenAI API key not configured in settings"
}
```

---

## Dica de Arquitetura no Roteiriza

1. O usuário do Roteiriza clica em **"Finalizar e Editar Vídeo"**.
2. Seu sistema dispara a requisição `POST /edit` para o servidor (Hetzner).
3. O servidor responde com um `job_id`.
4. O Roteiriza abre um Modal mostrando: *"O seu vídeo está na máquina de edição..."* com uma barra de progresso.
5. O Frontend dispara chamadas `GET /status/<job_id>` a cada 3~5 segundos para fazer a barra se mover (`progress`) e avisar em que etapa está (`step`).
6. Quando receber `"status": "completed"`, a tela de loading some e você mostra o vídeo retornado no `result.video_url`!
