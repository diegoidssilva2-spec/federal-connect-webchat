"""
Alerta no Telegram de lead parado em "aguardando pagamento" (01/10).

Incidente 30/09: o Max recebeu o link de cadastro, não respondeu mais e ficou
parado em "aguardando pagamento" sem ninguém saber. Este módulo olha as
conversas em memória a cada minuto e, se alguma está nesse estágio há mais de
30 minutos sem mensagem nova, manda UM aviso pro Telegram do Diegão com o
nome, o telefone e o link do WhatsApp pra ele chamar o lead.

Não escreve no banco e não muda nenhuma conversa: só lê o dicionário em
memória (CONVERSAS) e guarda, também em memória, quais já foram avisadas. Num
redeploy essa lista zera, então um lead ainda parado pode ser avisado de novo
UMA vez — preferível a nunca avisar.

Desligado sozinho se TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não existirem no
Render (o resto do projeto segue igual). Nunca loga o token.
"""
import asyncio
import json
import logging
import re
import time
import urllib.request

from app.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from app.store import CONVERSAS

logger = logging.getLogger("federal-webchat")

ESPERA_MIN_SEGUNDOS = 30 * 60      # parado há mais disso = avisa
JANELA_MAX_SEGUNDOS = 12 * 3600    # parado há mais que isso = lead frio, não avisa
INTERVALO_VARREDURA = 60

_ja_avisadas: set[str] = set()


def disponivel() -> bool:
    return bool(TELEGRAM_BOT_TOKEN) and bool(TELEGRAM_CHAT_ID)


def _link_whatsapp(telefone: str | None) -> str | None:
    digitos = re.sub(r"\D", "", telefone or "")
    if len(digitos) < 10:
        return None
    if not digitos.startswith("55"):
        digitos = "55" + digitos
    return f"https://wa.me/{digitos}"


def _montar_texto(conversa, minutos: int) -> str:
    nome = conversa.lead_name or "Lead sem nome"
    linhas = [
        "💰 Lead parado em AGUARDANDO PAGAMENTO",
        f"{nome} — parado há {minutos} min",
    ]
    if conversa.lead_phone:
        linhas.append(f"WhatsApp: {conversa.lead_phone}")
    link = _link_whatsapp(conversa.lead_phone)
    if link:
        linhas.append(f"Chamar: {link}")
    if conversa.lead_origem:
        linhas.append(f"Origem: {conversa.lead_origem}")
    return "\n".join(linhas)


def _enviar(texto: str) -> bool:
    """Chamada de rede síncrona — rodar via asyncio.to_thread."""
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data=json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": texto}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status < 300
    except Exception as e:
        # Só o tipo do erro: a mensagem/URL da exceção pode conter o token.
        logger.warning("ALERTA_TELEGRAM_FALHOU %s", type(e).__name__)
        return False


async def _varrer_uma_vez() -> None:
    agora = time.time()
    for conversa in list(CONVERSAS.values()):
        if conversa.estagio != "aguardando_pagamento":
            continue
        if conversa.session_id in _ja_avisadas:
            continue
        parado = agora - conversa.ultima_mensagem_em
        if parado < ESPERA_MIN_SEGUNDOS or parado > JANELA_MAX_SEGUNDOS:
            continue
        enviou = await asyncio.to_thread(_enviar, _montar_texto(conversa, int(parado // 60)))
        if enviou:
            _ja_avisadas.add(conversa.session_id)
            logger.info("ALERTA_TELEGRAM_OK lead parado %s", conversa.session_id[:8])


async def loop_alertas() -> None:
    """Roda pra sempre em segundo plano; nunca deixa uma falha derrubar o servidor."""
    if not disponivel():
        logger.warning("Alerta de Telegram desligado (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID não configurados).")
        return
    logger.info("Alerta de Telegram ligado (lead parado em aguardando pagamento).")
    while True:
        try:
            await _varrer_uma_vez()
        except Exception:
            logger.exception("Falha na varredura do alerta de Telegram (segue tentando)")
        await asyncio.sleep(INTERVALO_VARREDURA)
