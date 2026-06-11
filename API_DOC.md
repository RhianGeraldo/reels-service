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
| `video_url` | `string` | Sim | - | URL pública ou assinada do vídeo bruto (.mp4) que será editado. |
| `dynamic_editing` | `boolean` | Não | `true` | Se `true`, ativa a inteligência artificial para achar cortes de 5s, colocar barras laranjas e aplicar zooms dinâmicos. Se `false`, o vídeo não sofre os cortes de AI (modo pass-through super rápido). |
| `remove_silences` | `boolean` | Não | `true` | Se `true`, passa pelo `auto-editor` para remover os respiros mortos do áudio. |
| `generate_captions` | `boolean` | Não | `true` | Se `true`, realiza a transcrição completa usando Whisper e aplica legenda karaokê na tela final. |
| `generate_overlays` | `boolean` | Não | `true` | Se `true`, o LLM vai planejar e gerar imagens com IA para jogar no meio do vídeo e ilustrar o que você fala (Só funciona se `dynamic_editing` for `true`). |
| `image_provider` | `string` | Não | `"gemini"` | Qual IA usar para gerar imagens (`"gemini"` ou `"openai"`). O sistema também possui fallback pro OpenRouter internamente. |
| `generate_sora` | `boolean` | Não | `true` | Habilita a geração e inserção de B-Rolls dinâmicos através de IA geradora de vídeo (Sora/Luma/Runway se conectados). |
| `hook_line1` | `string` | Não | `null` | (Opcional) Forçar o texto da primeira linha do Banner Laranja (Ignora o LLM). |
| `hook_line2` | `string` | Não | `null` | (Opcional) Forçar o texto da segunda linha do Banner Laranja (Ignora o LLM). |

### Exemplo de Requisição (Pass-through Básico: Somente limpar respiros + legendas)
```bash
curl -X POST https://api-seuservidor.com/edit \
  -H "Authorization: Bearer SEU_SERVICE_SECRET" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "usuario-roteiriza-123",
    "video_url": "https://meu-s3.com/video-bruto.mp4",
    "dynamic_editing": false,
    "remove_silences": true,
    "generate_captions": true,
    "generate_overlays": false
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
  "step": "generating_sora_videos"
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
  "step": "generating_hook_images",
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
