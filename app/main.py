"""
Federal Connect — Web Chat + Painel do Operador (Fase 0 / MVP de teste)

Substitui a via Meta Cloud API por um chat próprio: o lead cai direto no
site, conversa com a IA (mesmo cérebro de sempre — agent.py/knowledge_base.py
não mudaram nada), e o Sandro/Marcos acompanham tudo em tempo real pelo
painel do operador, podendo assumir a conversa quando quiserem.

Rodar local: uvicorn app.main:app --reload --port 8000
"""
import asyncio
import csv
import io
import json
import logging
import re
import time

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.agent import run_agent, extrair_perfil_lead_via_ia, _to_plain_dict
from app.config import OPERATOR_PASSWORD
from app.store import (
    CONVERSAS, OPERADORES_CONECTADOS, Conversa,
    nova_conversa, obter, listar, tocar, compactar_conversa_encerrada,
    registrar_atualizacao_lead,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("federal-webchat")

app = FastAPI(title="Federal Connect - Web Chat")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def pagina_chat():
    return FileResponse("static/chat.html")


@app.get("/painel")
async def pagina_painel():
    return FileResponse("static/admin.html")


@app.get("/exportar-leads.csv")
async def exportar_leads(senha: str = Query(...)):
    if senha != OPERATOR_PASSWORD:
        raise HTTPException(status_code=403, detail="Senha incorreta")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["session_id", "nome", "telefone", "origem", "estagio", "notas", "ultima_mensagem"])
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
    return Response(content=buffer.getvalue(), media_type="text/csv")


# ---------------------------------------------------------------------------
# Normaliza o histórico de uma conversa (que pode ter objetos LangChain
# misturados com dicts simples, por causa do reducer add_messages) num
# formato leve de {"remetente", "texto"} usado tanto pra reenviar histórico
# ao visitante que recarregou a página quanto pro painel do operador.
# ---------------------------------------------------------------------------

def _extrair_texto_mensagem(conteudo) -> str:
    if isinstance(conteudo, str):
        return conteudo
    if isinstance(conteudo, list):
        partes = [
            bloco.get("text", "")
            for bloco in conteudo
            if isinstance(bloco, dict) and bloco.get("type") == "text"
        ]
        texto = " ".join(p for p in partes if p)
        return texto or "[imagem]"
    return "[imagem]"


async def _atualizar_perfil_lead(conversa: Conversa) -> None:
    """
    Chama a extração via IA (agent.py) e funde o resultado na conversa —
    só sobrescreve um campo quando a IA devolveu algo não-nulo, então
    nunca "apaga" um nome/telefone/origem já capturado antes. Roda depois
    de toda mensagem do lead (seja a IA quem responde ou um humano que
    assumiu), pra manter o painel sempre em dia, mesmo quando a pessoa se
    apresenta de um jeito natural ("o Bruno é motorista de app", "vim pelo
    anúncio no Insta") em vez de um padrão fixo.

    A extração em si (chamada de IA) roda numa thread separada — é uma
    chamada de rede, não pode travar o loop do WebSocket enquanto o lead
    espera resposta.
    """
    perfil = await asyncio.to_thread(extrair_perfil_lead_via_ia, conversa.history)

    mudou = False

    if perfil.get("nome") and perfil["nome"] != conversa.lead_name:
        conversa.lead_name = perfil["nome"]
        mudou = True

    if perfil.get("telefone"):
        apenas_digitos = re.sub(r"\D", "", perfil["telefone"])
        if 10 <= len(apenas_digitos) <= 11 and apenas_digitos != conversa.lead_phone:
            conversa.lead_phone = apenas_digitos
            mudou = True

    if perfil.get("origem") and perfil["origem"] != conversa.lead_origem:
        conversa.lead_origem = perfil["origem"]
        mudou = True

    if perfil.get("notas") and perfil["notas"] != conversa.lead_notes:
        conversa.lead_notes = perfil["notas"]
        mudou = True

    # CRM leve (Regra Dura 15 / pedido do Diegão): todo lead com dado novo
    # é regravado no CSV em disco e disparado pro webhook, se configurado —
    # roda em thread separada (I/O de disco/rede) pra não travar o chat.
    if mudou:
        await asyncio.to_thread(registrar_atualizacao_lead, conversa)


def _historico_para_payload(conversa: Conversa) -> list[dict]:
    mensagens = []
    for m in conversa.history:
        try:
            d = _to_plain_dict(m)
        except Exception:
            continue
        texto_msg = _extrair_texto_mensagem(d.get("content"))
        remetente = "lead" if d.get("role") == "user" else "ia"
        mensagens.append({"remetente": remetente, "texto": texto_msg})
    return mensagens


# ---------------------------------------------------------------------------
# Broadcast pro painel do operador — qualquer mudança relevante (mensagem
# nova, conversa nova, mudança de estágio) manda um snapshot atualizado.
# ---------------------------------------------------------------------------

async def _broadcast_painel():
    snapshot = {
        "type": "lista",
        "conversas": [
            {
                "session_id": c.session_id,
                "lead_name": c.lead_name,
                "lead_phone": c.lead_phone,
                "lead_notes": c.lead_notes,
                "lead_origem": c.lead_origem,
                "estagio": c.estagio,
                "criada_em": c.criada_em,
                "humano_ativo": c.humano_ativo,
                "ultima_mensagem_em": c.ultima_mensagem_em,
                "total_mensagens": len(c.history),
                "resumo_encerramento": c.resumo_encerramento,
            }
            for c in listar()
        ],
    }
    mortos = []
    for ws in OPERADORES_CONECTADOS:
        try:
            await ws.send_json(snapshot)
        except Exception:
            mortos.append(ws)
    for ws in mortos:
        OPERADORES_CONECTADOS.discard(ws)


async def _enviar_mensagem_para_operadores_da_conversa(conversa: Conversa, remetente: str, texto: str):
    payload = {
        "type": "mensagem",
        "session_id": conversa.session_id,
        "remetente": remetente,  # "lead" | "ia" | "operador"
        "texto": texto,
    }
    mortos = []
    for ws in OPERADORES_CONECTADOS:
        try:
            await ws.send_json(payload)
        except Exception:
            mortos.append(ws)
    for ws in mortos:
        OPERADORES_CONECTADOS.discard(ws)


# ---------------------------------------------------------------------------
# WebSocket do VISITANTE (lead) — a página static/chat.html conecta aqui.
# ---------------------------------------------------------------------------

@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket, session_id: str | None = Query(default=None)):
    await websocket.accept()

    conversa = obter(session_id) if session_id else None
    if conversa is None:
        conversa = nova_conversa()

    conversa.websocket_visitante = websocket
    await websocket.send_json({"type": "sessao", "session_id": conversa.session_id})
    # Reconexão (recarregou a página / saiu e voltou): manda de volta o
    # histórico salvo no servidor, senão o chat parecia "esquecer tudo".
    # Se a lista vier vazia (sessão realmente nova), o cliente sabe que
    # deve mostrar a mensagem de boas-vindas.
    await websocket.send_json({"type": "historico", "mensagens": _historico_para_payload(conversa)})
    await _broadcast_painel()

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            texto_lead = data.get("texto", "").strip()
            imagem_base64 = data.get("imagem_base64")
            imagem_media_type = data.get("imagem_media_type")

            if not texto_lead and not imagem_base64:
                continue

            tocar(conversa)
            mensagem_espelho = texto_lead or "[imagem recebida — ex: comprovante/contrato]"
            await _enviar_mensagem_para_operadores_da_conversa(conversa, "lead", mensagem_espelho)

            # Se um humano já assumiu essa conversa, a IA NÃO responde —
            # a mensagem só fica registrada, esperando o operador digitar.
            if conversa.humano_ativo:
                conversa.history.append({"role": "user", "content": mensagem_espelho})
                if conversa.estagio not in ("com_humano",):
                    conversa.estagio = "com_humano"
                # CRM leve: reler a conversa e atualizar nome/telefone/origem/
                # notas via IA (não é regex fixo — pega jeito natural de
                # falar, tipo "o Bruno é motorista de app", e atualiza toda
                # vez que a pessoa der mais informação, não só na primeira).
                await _atualizar_perfil_lead(conversa)
                await _broadcast_painel()
                continue

            if conversa.estagio == "novo":
                conversa.estagio = "conversando"

            # Mesma lógica que já existia no meta_client.py pro print do
            # ClickSign no WhatsApp — o agent.py não muda nada, só recebe
            # a imagem em base64 do jeito que ele já espera.
            resultado = run_agent(
                lead_phone=conversa.session_id,  # não é telefone aqui, é o id da sessão
                lead_name=conversa.lead_name,
                user_message=texto_lead,
                history=conversa.history,
                image_base64=imagem_base64,
                image_media_type=imagem_media_type,
            )
            conversa.history = resultado["updated_history"]
            resposta = resultado["reply"]

            await websocket.send_json({"type": "mensagem", "remetente": "ia", "texto": resposta})
            await _enviar_mensagem_para_operadores_da_conversa(conversa, "ia", resposta)

            # CRM leve: mesmo refresh de nome/telefone/origem/notas via IA
            # que roda no ramo "humano_ativo" acima — mantém o painel em dia
            # a cada mensagem, seja quem for que está respondendo o lead.
            await _atualizar_perfil_lead(conversa)

            # Classificação automática — a IA decide o estágio sozinha,
            # ninguém no painel precisa clicar pra mudar isso manualmente.
            conversa.estagio = resultado["estagio_sugerido"]
            if conversa.estagio == "concluido":
                compactar_conversa_encerrada(conversa)
            if resultado["handoff_requested"] and not conversa.handoff_link_enviado:
                conversa.handoff_link_enviado = True

            await _broadcast_painel()

    except WebSocketDisconnect:
        conversa.websocket_visitante = None
        await _broadcast_painel()


# ---------------------------------------------------------------------------
# WebSocket do OPERADOR (Sandro/Marcos) — static/admin.html conecta aqui.
# Autenticação simples por senha (MVP de teste — trocar por login real
# antes de expor pra mais gente além de vocês dois).
# ---------------------------------------------------------------------------

@app.websocket("/ws/painel")
async def ws_painel(websocket: WebSocket, senha: str = Query(...)):
    if senha != OPERATOR_PASSWORD:
        await websocket.close(code=4001)
        return

    await websocket.accept()
    OPERADORES_CONECTADOS.add(websocket)
    await _broadcast_painel()

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            acao = data.get("acao")
            session_id = data.get("session_id")
            conversa = obter(session_id)
            if conversa is None:
                continue

            if acao == "abrir":
                await websocket.send_json({
                    "type": "historico",
                    "session_id": session_id,
                    "mensagens": _historico_para_payload(conversa),
                })

            elif acao == "assumir":
                conversa.humano_ativo = True
                conversa.estagio = "com_humano"
                await _broadcast_painel()

            elif acao == "liberar_para_ia":
                conversa.humano_ativo = False
                conversa.estagio = "conversando"
                await _broadcast_painel()

            elif acao == "mudar_estagio":
                novo_estagio = data.get("estagio")
                if novo_estagio:
                    conversa.estagio = novo_estagio
                    if novo_estagio == "concluido":
                        compactar_conversa_encerrada(conversa)
                    await _broadcast_painel()

            elif acao == "mensagem":
                texto = data.get("texto", "").strip()
                if not texto:
                    continue
                conversa.history.append({"role": "assistant", "content": texto})
                tocar(conversa)
                if conversa.websocket_visitante is not None:
                    try:
                        await conversa.websocket_visitante.send_json(
                            {"type": "mensagem", "remetente": "operador", "texto": texto}
                        )
                    except Exception:
                        logger.warning("Falha ao entregar mensagem do operador ao visitante (desconectado)")
                await _enviar_mensagem_para_operadores_da_conversa(conversa, "operador", texto)
                await _broadcast_painel()

    except WebSocketDisconnect:
        OPERADORES_CONECTADOS.discard(websocket)


app.mount("/static", StaticFiles(directory="static"), name="static")
