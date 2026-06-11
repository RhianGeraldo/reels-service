# PROGRESS — reels-service bring-up

> **Status:** serviço Flask roda 100% localmente; infraestrutura Supabase confirmada.
> **Próximo passo:** investigar bug `hook.mp4 not found` que causa falha no step `editing_video`.

---

## 1. O que já está pronto

- [x] `.venv` criado em `/home/rhiangeraldo/Desenvolvimentos/reels-service/.venv` (Python 3.13).
- [x] Dependências instaladas: `flask 3.1.0`, `gunicorn 23.0.0`, `numpy 1.26.4`, `Pillow 10.4.0`, `requests 2.32.3`, `python-dotenv 1.0.1`, `auto-editor 29.3.1`.
- [x] `ffmpeg 7.1.1` no PATH do sistema.
- [x] `.env` com todas as variáveis preenchidas e `SERVICE_SECRET` seguro (token 32 bytes).
- [x] Tabelas `reels_jobs` e `user_settings` existem no Supabase (confirmado).
- [x] Colunas `openrouter_api_key` e `groq_api_key` existem em `user_settings` (confirmado).
- [x] Bucket `user-uploads` existe no Supabase Storage (confirmado).
- [x] Smoke tests passando: `/health`, `/jobs` (401 sem auth, 200 com auth), `/edit` (validação).
- [x] `recover_orphaned_jobs()` na startup — marca como `failed` jobs presos em `processing` após restart.
- [x] Bug de autenticação corrigido em `check_auth()` (lógica invertida que rejeitava tokens válidos).
- [x] `mark_stuck_jobs()` adicionado ao `supabase_client.py`.

## 2. O que falta (ordem)

1. **Reiniciar Claude Code** nesta pasta e aprovar o MCP `supabase` (autenticação OAuth abre no browser na primeira chamada).
2. **Criar as tabelas** `reels_jobs` e `user_settings` (SQL abaixo) via MCP.
3. **Criar o bucket** `user-uploads` no Storage (público) via MCP ou Dashboard.
4. **Inserir um registro em `user_settings`** com as chaves OpenAI e Gemini para o `user_id` que será usado nos testes.
5. **Teste end-to-end**: `POST /edit` com `user_id` + `video_url` real → acompanhar `GET /status/:id` → conferir mp4 final no bucket.

## 3. SQL para o passo 2 (rodar via MCP `supabase`)

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

CREATE INDEX IF NOT EXISTS reels_jobs_user_id_idx ON reels_jobs (user_id);
CREATE INDEX IF NOT EXISTS reels_jobs_status_idx  ON reels_jobs (status);
CREATE INDEX IF NOT EXISTS reels_jobs_created_idx ON reels_jobs (created_at DESC);

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

> RLS pode ficar **off** nessas duas tabelas porque o serviço usa a chave `service_role`, que ignora RLS de qualquer jeito. Se você quiser ligar RLS depois (boa prática para o frontend), faça em policy separada — não vai afetar o `reels-service`.

## 4. Storage — passo 3

Criar bucket `user-uploads` (público). Via MCP (`storage.create_bucket`) ou no Dashboard.
Política recomendada: leitura pública, escrita só com `service_role` (já é o default quando o bucket é público sem policies adicionais).

## 5. Inserção do user_settings — passo 4

`user_id` é um TEXT (não está atado a `auth.users` por foreign key). Pode ser qualquer string única — exemplo `"rhian-dev"`.

```sql
INSERT INTO user_settings (user_id, openai_api_key, gemini_api_key)
VALUES ('rhian-dev', '<COLAR_CHAVE_OPENAI>', '<COLAR_CHAVE_GEMINI>')
ON CONFLICT (user_id) DO UPDATE
SET openai_api_key = EXCLUDED.openai_api_key,
    gemini_api_key = EXCLUDED.gemini_api_key;
```

## 6. Smoke test end-to-end — passo 5

```bash
# subir
.venv/bin/python app.py
# em outro terminal:
curl -s -X POST http://127.0.0.1:3001/edit \
  -H "Authorization: Bearer troque-isso-por-uma-string-secreta" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"rhian-dev","video_url":"https://.../video.mp4"}'
# → {"job_id":"<uuid>","status":"processing"}

curl -s -H "Authorization: Bearer troque-isso-por-uma-string-secreta" \
  http://127.0.0.1:3001/status/<uuid>
```

Espere `status: completed` e `result.video_url` apontando para o bucket `user-uploads`.

## 7. Onde retomar (quando o MCP voltar)

Comece chamando o tool `mcp__supabase__list_tables` (ou equivalente) para confirmar que as tabelas **não** existem ainda. Depois rode o SQL da seção 3 via `mcp__supabase__apply_migration` (ou `execute_sql`). Em seguida, crie o bucket (seção 4) e o registro de `user_settings` (seção 5 — pergunte ao usuário pelas chaves).

## Arquivos tocados nesta sessão

- `requirements.txt` — adicionado `python-dotenv==1.0.1`.
- `app.py` — adicionado `load_dotenv()` no topo (try/except por seguro).
- `.env` — adicionado `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SERVICE_SECRET`, `PORT` (os nomes que originalmente estavam, `NEXT_PUBLIC_*` e `SERVICE_ROLE`, foram mantidos).
- `.mcp.json` — criado pelo `claude mcp add` com o servidor `supabase`.
