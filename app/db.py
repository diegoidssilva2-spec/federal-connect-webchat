"""
Persistência em Postgres (Neon) — Fase 1.

Substitui o "tudo em memória" da Fase 0 por um banco de verdade, mas sem
reescrever a lógica do resto do projeto: o dicionário CONVERSAS em store.py
continua existindo e continua sendo o que main.py lê/escreve o tempo todo
(rápido, sem round-trip de rede a cada mensagem). Este módulo só entra em
dois momentos:
  1. Na subida do servidor — carrega tudo que já existia no banco pro
     dicionário em memória, então um restart/redeploy do Render não apaga
     mais nada.
  2. A cada mudança relevante numa conversa — grava (upsert) essa conversa
     no banco, em thread separada (I/O de rede não pode travar o chat).

Se DATABASE_URL não estiver configurada (ex: rodando local sem Neon), tudo
aqui vira no-op silencioso — o projeto continua funcionando exatamente como
antes (só em memória), pra não quebrar ambiente de dev.
"""
import json
import logging

from app.config import DATABASE_URL

logger = logging.getLogger("federal-webchat")

try:
    import psycopg
    from psycopg.types.json import Json
except ImportError:  # driver não instalado — só falha se DATABASE_URL setada
    psycopg = None
    Json = None


def disponivel() -> bool:
    return bool(DATABASE_URL) and psycopg is not None


def _conectar():
    return psycopg.connect(DATABASE_URL, autocommit=True)


def inicializar() -> None:
    """Cria a tabela se não existir. Chamado uma vez, na subida do app."""
    if not disponivel():
        logger.warning(
            "DATABASE_URL não configurada — rodando só em memória (Fase 0). "
            "Conversas somem a cada restart/redeploy."
        )
        return
    try:
        with _conectar() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversas (
                    session_id TEXT PRIMARY KEY,
                    lead_name TEXT,
                    lead_phone TEXT,
                    lead_notes TEXT,
                    lead_origem TEXT,
                    history JSONB NOT NULL DEFAULT '[]',
                    estagio TEXT NOT NULL DEFAULT 'novo',
                    humano_ativo BOOLEAN NOT NULL DEFAULT FALSE,
                    criada_em DOUBLE PRECISION NOT NULL,
                    ultima_mensagem_em DOUBLE PRECISION NOT NULL,
                    handoff_link_enviado BOOLEAN NOT NULL DEFAULT FALSE,
                    resumo_encerramento TEXT
                )
            """)
        logger.info("Banco Postgres (Neon) conectado e tabela 'conversas' pronta.")
    except Exception:
        logger.exception("Falha ao conectar/inicializar o banco — seguindo só em memória por ora.")


def carregar_todas() -> list[dict]:
    """Lê todas as conversas salvas — usado só na subida do app."""
    if not disponivel():
        return []
    try:
        with _conectar() as conn:
            cur = conn.execute("""
                SELECT session_id, lead_name, lead_phone, lead_notes, lead_origem,
                       history, estagio, humano_ativo, criada_em, ultima_mensagem_em,
                       handoff_link_enviado, resumo_encerramento
                FROM conversas
            """)
            colunas = [d.name for d in cur.description]
            return [dict(zip(colunas, linha)) for linha in cur.fetchall()]
    except Exception:
        logger.exception("Falha ao carregar conversas do banco — subindo vazio (só memória).")
        return []


def salvar(conversa) -> None:
    """Upsert de uma conversa inteira. Chamado via asyncio.to_thread pelo main.py."""
    if not disponivel():
        return
    # Nunca grava imagem em base64 no banco (custa espaço rápido demais no
    # plano gratuito) — troca por um marcador de texto, igual já é feito pro
    # painel. A IA já processou a imagem na hora (ex: validação de contrato),
    # não precisa dela de volta depois de um restart.
    historico_limpo = []
    for m in conversa.history:
        role = m.get("role") if isinstance(m, dict) else None
        conteudo = m.get("content") if isinstance(m, dict) else None
        if isinstance(conteudo, list):
            texto = " ".join(
                b.get("text", "") for b in conteudo
                if isinstance(b, dict) and b.get("type") == "text"
            ) or "[imagem enviada]"
            conteudo = texto
        if role is not None:
            historico_limpo.append({"role": role, "content": conteudo})

    try:
        with _conectar() as conn:
            conn.execute("""
                INSERT INTO conversas (
                    session_id, lead_name, lead_phone, lead_notes, lead_origem,
                    history, estagio, humano_ativo, criada_em, ultima_mensagem_em,
                    handoff_link_enviado, resumo_encerramento
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (session_id) DO UPDATE SET
                    lead_name = EXCLUDED.lead_name,
                    lead_phone = EXCLUDED.lead_phone,
                    lead_notes = EXCLUDED.lead_notes,
                    lead_origem = EXCLUDED.lead_origem,
                    history = EXCLUDED.history,
                    estagio = EXCLUDED.estagio,
                    humano_ativo = EXCLUDED.humano_ativo,
                    ultima_mensagem_em = EXCLUDED.ultima_mensagem_em,
                    handoff_link_enviado = EXCLUDED.handoff_link_enviado,
                    resumo_encerramento = EXCLUDED.resumo_encerramento
            """, (
                conversa.session_id, conversa.lead_name, conversa.lead_phone,
                conversa.lead_notes, conversa.lead_origem, Json(historico_limpo),
                conversa.estagio, conversa.humano_ativo, conversa.criada_em,
                conversa.ultima_mensagem_em, conversa.handoff_link_enviado,
                conversa.resumo_encerramento,
            ))
    except Exception:
        logger.exception("Falha ao salvar conversa %s no banco (segue só em memória)", conversa.session_id)


def excluir(session_id: str) -> None:
    if not disponivel():
        return
    try:
        with _conectar() as conn:
            conn.execute("DELETE FROM conversas WHERE session_id = %s", (session_id,))
    except Exception:
        logger.exception("Falha ao excluir conversa %s do banco", session_id)
