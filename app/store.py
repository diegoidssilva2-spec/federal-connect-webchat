"""
Estado das conversas — CRM leve embutido.

Fase 0 (MVP de teste): tudo em memória, um processo só (mesma limitação já
documentada no projeto do WhatsApp oficial). Migrar pra Postgres é o mesmo
próximo passo de sempre, quando sair de teste pra produção real.
"""
import csv
import json
import logging
import os
import re
import time
import urllib.request
import uuid
from dataclasses import dataclass, field

from app.config import CRM_WEBHOOK_URL, LEADS_CSV_PATH

logger = logging.getLogger("federal-webchat")

# Padrões simples de auto-apresentação em português — não é IA, é
# reconhecimento de texto barato, sem custo de API extra por mensagem.
_PADROES_NOME = [
    re.compile(r"\bmeu nome[ée]?\s+([A-ZÀ-Ú][a-zà-ú]+)", re.IGNORECASE),
    re.compile(r"\bme chamo\s+([A-ZÀ-Ú][a-zà-ú]+)", re.IGNORECASE),
    re.compile(r"\baqui[ée]\s+o\s+([A-ZÀ-Ú][a-zà-ú]+)", re.IGNORECASE),
    re.compile(r"\bsou\s+o\s+([A-ZÀ-Ú][a-zà-ú]+)", re.IGNORECASE),
    re.compile(r"\bsou\s+a\s+([A-ZÀ-Ú][a-zà-ú]+)", re.IGNORECASE),
]


def extrair_nome(texto: str) -> str | None:
    for padrao in _PADROES_NOME:
        m = padrao.search(texto)
        if m:
            return m.group(1).capitalize()
    return None


# Telefone/WhatsApp do lead — aqui não é WhatsApp oficial, então o número
# não vem automático em lugar nenhum. Captura barata (regex, sem custo de
# IA) sempre que o lead digitar algo parecido com um número de celular BR.
_PADRAO_TELEFONE = re.compile(r"\(?\d{2}\)?[\s.-]?9?\d{4}[\s.-]?\d{4}")


def extrair_telefone(texto: str) -> str | None:
    m = _PADRAO_TELEFONE.search(texto)
    if not m:
        return None
    apenas_digitos = re.sub(r"\D", "", m.group(0))
    if 10 <= len(apenas_digitos) <= 11:
        return apenas_digitos
    return None


# Estágios do funil — o "CRM leve" é isso: cada conversa tem um status
# visível pro operador, sem precisar de sistema externo.
ESTAGIOS = [
    "novo",                # acabou de abrir o chat, ainda sem trocar mensagem
    "conversando",         # IA já está atendendo normalmente
    "aguardando_humano",   # pediu pra falar com humano / IA sinalizou handoff
    "com_humano",          # um operador assumiu a conversa
    "aguardando_pagamento",
    "aguardando_ativacao",
    "concluido",
]


@dataclass
class Conversa:
    session_id: str
    lead_name: str | None = None
    lead_phone: str | None = None
    lead_notes: str | None = None
    lead_origem: str | None = None  # de onde o lead veio (ex: "anúncio Instagram", "indicação")
    history: list[dict] = field(default_factory=list)
    estagio: str = "novo"
    humano_ativo: bool = False
    criada_em: float = field(default_factory=time.time)
    ultima_mensagem_em: float = field(default_factory=time.time)
    websocket_visitante: object = None
    handoff_link_enviado: bool = False
    resumo_encerramento: str | None = None


CONVERSAS: dict[str, Conversa] = {}
# Conexões websocket do painel do operador, pra broadcast de atualização em
# tempo real (lista de conversas + mensagens novas).
OPERADORES_CONECTADOS: set = set()


def nova_conversa() -> Conversa:
    sid = str(uuid.uuid4())
    conversa = Conversa(session_id=sid)
    CONVERSAS[sid] = conversa
    return conversa


def obter(session_id: str) -> Conversa | None:
    return CONVERSAS.get(session_id)


def listar() -> list[Conversa]:
    return sorted(CONVERSAS.values(), key=lambda c: c.ultima_mensagem_em, reverse=True)


def tocar(conversa: Conversa) -> None:
    conversa.ultima_mensagem_em = time.time()


def compactar_conversa_encerrada(conversa: Conversa) -> None:
    """
    Chamado quando a conversa é marcada como 'concluido'. Em vez de manter
    o histórico completo (que só cresce e nunca é mais usado depois de
    fechada), guarda um resumo mínimo — só o suficiente pro operador
    reconhecer o lead se ele voltar a falar no mesmo link antes do servidor
    reiniciar. Isso é o que mantém o "banco de dados" enxuto.

    Importante: o resumo NÃO é reinjetado na conversa com a IA (evita
    formato de mensagem inválido pra API) — é só uma anotação visível pro
    operador no painel. Se o lead voltar, a IA recomeça a conversa do zero,
    mas o operador já vê no painel que essa pessoa converteu antes.
    """
    total_mensagens = len(conversa.history)
    conversa.resumo_encerramento = (
        f"Conversa concluída em {time.strftime('%d/%m %H:%M', time.localtime())} "
        f"— {total_mensagens} mensagens trocadas."
    )
    conversa.history = []


# ---------------------------------------------------------------------------
# Persistência de leads — Fase 0 ainda é tudo em memória (reinicia o processo,
# perde a conversa em andamento), mas os DADOS do lead (nome/telefone/origem/
# estágio) não podem se perder junto. Toda vez que o perfil de um lead é
# atualizado, isso aqui: (1) regrava o CSV completo em disco, servindo de
# CRM leve que sobrevive a restart e já é importável em qualquer planilha;
# (2) dispara webhook opcional pra automação externa, se configurado.
# Nunca lança exceção pro chamador — falha de disco ou de rede aqui não pode
# derrubar a conversa com o lead.
# ---------------------------------------------------------------------------

_CAMPOS_CSV = ["session_id", "nome", "telefone", "origem", "estagio", "notas", "ultima_mensagem"]


def persistir_leads_csv() -> None:
    try:
        with open(LEADS_CSV_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(_CAMPOS_CSV)
            for c in listar():
                writer.writerow([
                    c.session_id,
                    c.lead_name or "",
                    c.lead_phone or "",
                    c.lead_origem or "",
                    c.estagio,
                    c.lead_notes or "",
                    time.strftime("%d/%m/%Y %H:%M", time.localtime(c.ultima_mensagem_em)),
                ])
    except Exception:
        logger.exception("Falha ao gravar %s (leads seguem só em memória)", LEADS_CSV_PATH)


def enviar_webhook_lead(conversa: Conversa) -> None:
    if not CRM_WEBHOOK_URL:
        return
    payload = json.dumps({
        "session_id": conversa.session_id,
        "nome": conversa.lead_name,
        "telefone": conversa.lead_phone,
        "origem": conversa.lead_origem,
        "estagio": conversa.estagio,
        "notas": conversa.lead_notes,
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            CRM_WEBHOOK_URL, data=payload, method="POST",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        logger.warning("Falha ao enviar webhook de lead pra %s (não bloqueia o atendimento)", CRM_WEBHOOK_URL)


def registrar_atualizacao_lead(conversa: Conversa) -> None:
    """Ponto único chamado pelo main.py sempre que o perfil do lead muda."""
    persistir_leads_csv()
    enviar_webhook_lead(conversa)

