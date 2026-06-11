"""
setup_supabase.py — aplica schema + cria bucket via Supabase Management API.
Execute: .venv/bin/python scripts/setup_supabase.py
"""

import os
import sys
import json
import requests

try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

if not SUPABASE_URL or not SERVICE_ROLE_KEY:
    print("❌  SUPABASE_URL ou SUPABASE_SERVICE_ROLE_KEY não definidos no .env")
    sys.exit(1)

HEADERS = {
    "apikey": SERVICE_ROLE_KEY,
    "Authorization": f"Bearer {SERVICE_ROLE_KEY}",
    "Content-Type": "application/json",
}

# ─── 1. SQL via /rest/v1/rpc não funciona para DDL.
#     Usamos o endpoint /pg/query da Management API (SQL direto).
# ─────────────────────────────────────────────────────────────────

SQL = """
-- ── reels_jobs ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reels_jobs (
    id          UUID        PRIMARY KEY,
    user_id     TEXT        NOT NULL,
    status      TEXT        NOT NULL DEFAULT 'processing',
    progress    INTEGER     DEFAULT 0,
    step        TEXT        DEFAULT 'starting',
    video_url   TEXT,
    result      JSONB,
    error       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS reels_jobs_user_id_idx ON reels_jobs (user_id);
CREATE INDEX IF NOT EXISTS reels_jobs_status_idx  ON reels_jobs (status);
CREATE INDEX IF NOT EXISTS reels_jobs_created_idx ON reels_jobs (created_at DESC);

-- ── user_settings ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS user_settings (
    id                        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                   TEXT        NOT NULL UNIQUE,
    openai_api_key            TEXT,
    gemini_api_key            TEXT,
    openrouter_api_key        TEXT,
    groq_api_key              TEXT,
    instagram_username        TEXT,
    instagram_full_name       TEXT,
    instagram_profile_pic_url TEXT,
    created_at                TIMESTAMPTZ DEFAULT NOW()
);
"""

def run_sql(sql: str) -> dict:
    """Executa SQL via endpoint /rest/v1/rpc/exec_sql (custom) ou via pg endpoint."""
    # Supabase expõe o endpoint de query via Management API (não via projeto REST).
    # Como só temos a service_role do projeto, usamos a extensão pg_net / rpc se existir,
    # ou criamos a função exec_sql temporariamente.
    #
    # Alternativa mais simples: usar o endpoint /pg/query com a key de projeto.
    url = f"{SUPABASE_URL}/rest/v1/rpc/exec_sql"
    resp = requests.post(url, headers=HEADERS, json={"sql": sql})
    return resp


def create_exec_sql_function():
    """Cria função auxiliar exec_sql para rodar DDL via RPC."""
    create_fn = """
    CREATE OR REPLACE FUNCTION exec_sql(sql text)
    RETURNS void
    LANGUAGE plpgsql
    SECURITY DEFINER
    AS $$
    BEGIN
        EXECUTE sql;
    END;
    $$;
    """
    # Não é possível criar função via RPC antes de ela existir.
    # Usaremos a API de query direta do Supabase (disponível via pg endpoint).
    pass


def apply_migration_via_rest(sql: str):
    """
    Aplica DDL via Supabase REST usando o endpoint de query direta.
    Supabase expõe /pg/query apenas na Management API (com token pessoal),
    não com service_role. Então usamos a abordagem de criar uma função RPC
    exec_sql via um INSERT especial no schema postgres.
    
    Alternativa real: usar psycopg2 com connection string.
    """
    # Tentar via endpoint não-padrão que alguns projetos têm habilitado
    url = f"{SUPABASE_URL}/rest/v1/rpc/exec_sql"
    resp = requests.post(url, headers=HEADERS, json={"sql": sql})
    return resp.status_code, resp.text


def check_table_exists(table: str) -> bool:
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    params = {"limit": "1", "select": "*"}
    resp = requests.get(url, headers=HEADERS, params=params)
    return resp.status_code != 404


def create_bucket(bucket_name: str, public: bool = True):
    url = f"{SUPABASE_URL}/storage/v1/bucket"
    resp = requests.post(url, headers=HEADERS, json={
        "id": bucket_name,
        "name": bucket_name,
        "public": public,
        "file_size_limit": None,
        "allowed_mime_types": None,
    })
    return resp.status_code, resp.json()


print("=" * 60)
print("  reels-service — Supabase Setup")
print("=" * 60)
print(f"\nURL: {SUPABASE_URL}")
print()

# ─── Verificar tabelas ────────────────────────────────────────────
print("─── Verificando tabelas existentes ───")
for table in ["reels_jobs", "user_settings"]:
    exists = check_table_exists(table)
    print(f"  {'✅' if exists else '❌'} {table}: {'existe' if exists else 'NÃO existe'}")

print()
print("─── SQL das migrations ───")
print("  As tabelas precisam ser criadas via Dashboard ou psql.")
print("  Copie o SQL abaixo e execute no Supabase SQL Editor:")
print()
print("  URL do SQL Editor:")
print(f"  https://supabase.com/dashboard/project/akhjbkruupfunuedujlv/sql/new")
print()

# ─── Criar bucket ────────────────────────────────────────────────
print("─── Criando bucket user-uploads ───")
status, data = create_bucket("user-uploads", public=True)
if status in (200, 201):
    print(f"  ✅ Bucket 'user-uploads' criado com sucesso (público)")
elif status == 409 or (isinstance(data, dict) and "already" in str(data).lower()):
    print(f"  ✅ Bucket 'user-uploads' já existe")
else:
    print(f"  ❌ Erro ao criar bucket: HTTP {status} — {data}")

print()
print("─── SQL para rodar no Dashboard ───")
print()
print(SQL)
print()
print("=" * 60)
print("  Próximo passo: abrir o SQL Editor e colar o SQL acima.")
print(f"  https://supabase.com/dashboard/project/akhjbkruupfunuedujlv/sql/new")
print("=" * 60)
