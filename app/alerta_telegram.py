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

from app import db
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


def _ultima_do_lead(conversa) -> str:
    """Trecho da última mensagem do lead, pra quem lê o alerta ter contexto."""
    for m in reversed(conversa.history):
        if isinstance(m, dict):
            papel, conteudo = m.get("role"), m.get("content")
        else:
            papel = "user" if getattr(m, "type", "") == "human" else "assistant"
            conteudo = getattr(m, "content", None)
        if papel != "user":
            continue
        if isinstance(conteudo, list):
            conteudo = " ".join(b.get("text", "") for b in conteudo if isinstance(b, dict))
        texto = (conteudo or "").strip().replace("\n", " ")
        if texto:
            return texto[:140] + ("…" if len(texto) > 140 else "")
    return ""


def _montar_evento(conversa, tipo: str, motivo: str = "") -> str:
    titulos = {
        "humano": "🙋 Lead precisa de ATENDIMENTO HUMANO",
        "pagando": "💰 Lead chegou no PAGAMENTO (recebeu o link de adesão)",
        "quente": "🔥 Lead quente PARADO (conversou e sumiu)",
    }
    linhas = [titulos[tipo], conversa.lead_name or "Lead sem nome"]
    if motivo:
        linhas.append(motivo)
    if conversa.lead_phone:
        linhas.append(f"WhatsApp: {conversa.lead_phone}")
    link = _link_whatsapp(conversa.lead_phone)
    if link:
        linhas.append(f"Chamar: {link}")
    if conversa.lead_origem:
        linhas.append(f"Origem: {conversa.lead_origem}")
    ultima = _ultima_do_lead(conversa)
    if ultima:
        linhas.append(f"Última msg do lead: {ultima}")
    return "\n".join(linhas)


async def avisar_evento(conversa, tipo: str, motivo: str = "") -> None:
    """Alerta imediato quando o lead ENTRA em 'precisa de humano' ou 'pagamento'.
    Uma vez por lead e por tipo (chave tipo:session_id, gravada no banco, então
    um deploy não repete). Nunca derruba o chat: qualquer erro só vira log."""
    if not disponivel():
        return
    chave = f"{tipo}:{conversa.session_id}"
    if chave in _ja_avisadas:
        return
    try:
        enviou = await asyncio.to_thread(_enviar, _montar_evento(conversa, tipo, motivo))
        if enviou:
            _ja_avisadas.add(chave)
            await asyncio.to_thread(db.registrar_alerta, chave, time.time())
            logger.info("ALERTA_TELEGRAM_OK %s %s", tipo, conversa.session_id[:8])
    except Exception:
        logger.exception("Falha no alerta de Telegram (%s) — o chat segue normal", tipo)


_tarefas_alerta: set = set()


def disparar(conversa, tipo: str, motivo: str = "") -> None:
    """Agenda o alerta em segundo plano (não faz o lead esperar o Telegram)."""
    tarefa = asyncio.create_task(avisar_evento(conversa, tipo, motivo))
    _tarefas_alerta.add(tarefa)
    tarefa.add_done_callback(_tarefas_alerta.discard)


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
        # Lead quente parado (01/10): conversou de verdade (nome/WhatsApp + algumas
        # mensagens), ainda está em "conversando" e sumiu há 30 min a 12 h.
        if conversa.estagio == "conversando" and conversa.lead_phone and len(conversa.history) >= 6:
            chave = f"quente:{conversa.session_id}"
            parado = agora - conversa.ultima_mensagem_em
            if chave not in _ja_avisadas and ESPERA_MIN_SEGUNDOS <= parado <= JANELA_MAX_SEGUNDOS:
                await avisar_evento(conversa, "quente", f"Parado há {int(parado // 60)} min")
            continue
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
            # 01/10: grava no banco que já avisou — um redeploy não zera isso
            # mais e o mesmo lead nunca é avisado duas vezes.
            await asyncio.to_thread(db.registrar_alerta, conversa.session_id, time.time())
            logger.info("ALERTA_TELEGRAM_OK lead parado %s", conversa.session_id[:8])


async def loop_alertas() -> None:
    """Roda pra sempre em segundo plano; nunca deixa uma falha derrubar o servidor."""
    if not disponivel():
        logger.warning("Alerta de Telegram desligado (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID não configurados).")
        return
    # Antes de varrer: prepara a tabela e carrega quem já foi avisado (se o
    # banco falhar, segue só com a memória, como antes).
    await asyncio.to_thread(db.inicializar_alertas, ESPERA_MIN_SEGUNDOS)
    _ja_avisadas.update(await asyncio.to_thread(db.carregar_alertas))
    logger.info("Alerta de Telegram ligado (lead parado em aguardando pagamento). Já avisados: %d", len(_ja_avisadas))
    while True:
        try:
            await _varrer_uma_vez()
        except Exception:
            logger.exception("Falha na varredura do alerta de Telegram (segue tentando)")
        await asyncio.sleep(INTERVALO_VARREDURA)
