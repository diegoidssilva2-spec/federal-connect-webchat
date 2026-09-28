"""
API de Conversões da Meta (CAPI) — 28/09.

Manda os mesmos eventos que o Pixel do navegador já dispara (Lead,
InitiateCheckout, CompleteRegistration, ViewContent, PageView, Contact),
só que pelo SERVIDOR. Achado na análise da campanha (28/09): o Pixel de
navegador nunca tinha reforço nenhum — perde gente com iPhone (ITP/App
Tracking Transparency), ad blocker, ou qualquer bloqueio de rede do lado
do navegador. Rodar os dois juntos (Pixel + CAPI) com o MESMO event_id em
cada evento faz a Meta deduplicar automaticamente (conta como um evento só,
não dobra a métrica) e escolhe o caminho que chegou primeiro/mais completo.

Cada função aqui é best-effort: se a chamada falhar (sem token configurado,
rede fora, Meta fora do ar), loga e segue — nunca derruba o chat por causa
disso. Chamar sempre via asyncio.to_thread (é uma chamada de rede síncrona).
"""
import hashlib
import json
import logging
import re
import time
import urllib.request
import uuid

from app.config import META_CAPI_ACCESS_TOKEN, META_CAPI_TEST_EVENT_CODE, META_PIXEL_ID

logger = logging.getLogger("federal-webchat")

_GRAPH_VERSION = "v21.0"
_URL_SITE = "https://federal-connect-webchat.onrender.com/"


def disponivel() -> bool:
    return bool(META_CAPI_ACCESS_TOKEN) and bool(META_PIXEL_ID)


def _hash_telefone(telefone: str | None) -> str | None:
    """
    Normaliza e faz hash SHA-256 do telefone, como a Meta exige (nunca manda
    dado pessoal em texto puro pro CAPI). Formato esperado: só dígitos, com
    código do país (55) na frente — nosso store.py já grava telefone BR sem
    o 55, então adiciona aqui se faltar.
    """
    if not telefone:
        return None
    digitos = re.sub(r"\D", "", telefone)
    if not digitos:
        return None
    if not digitos.startswith("55"):
        digitos = "55" + digitos
    return hashlib.sha256(digitos.encode("utf-8")).hexdigest()


def novo_event_id() -> str:
    """Gerado uma vez por evento e compartilhado com o fbq() do navegador (mesmo event_id) pra Meta deduplicar Pixel + CAPI."""
    return str(uuid.uuid4())


def enviar_evento(
    nome_evento: str,
    event_id: str,
    ip_cliente: str | None = None,
    user_agent: str | None = None,
    telefone: str | None = None,
) -> None:
    """
    Manda um evento pro CAPI. Chamar via asyncio.to_thread — é bloqueante.
    Silencioso se META_CAPI_ACCESS_TOKEN não estiver configurado (CAPI
    desligado, só o Pixel de navegador continua funcionando normal).
    """
    if not disponivel():
        return

    user_data = {}
    if ip_cliente:
        user_data["client_ip_address"] = ip_cliente
    if user_agent:
        user_data["client_user_agent"] = user_agent
    telefone_hash = _hash_telefone(telefone)
    if telefone_hash:
        user_data["ph"] = [telefone_hash]

    payload = {
        "data": [{
            "event_name": nome_evento,
            "event_time": int(time.time()),
            "event_id": event_id,
            "action_source": "chat",
            "event_source_url": _URL_SITE,
            "user_data": user_data,
        }],
        "access_token": META_CAPI_ACCESS_TOKEN,
    }
    if META_CAPI_TEST_EVENT_CODE:
        payload["test_event_code"] = META_CAPI_TEST_EVENT_CODE

    url = f"https://graph.facebook.com/{_GRAPH_VERSION}/{META_PIXEL_ID}/events"
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status >= 300:
                logger.warning("CAPI respondeu %s pro evento %s", resp.status, nome_evento)
    except Exception:
        logger.exception("Falha ao mandar evento %s pro CAPI (Pixel de navegador segue funcionando)", nome_evento)
