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
import mimetypes
import re
import time

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query, Header

# 27/09: o Python desta imagem/SO não tem ".webp" no banco de tipos padrão
# (mimetypes) — o StaticFiles servia a logo do avatar como
# application/octet-stream em vez de image/webp, e alguns navegadores/
# webviews recusavam exibir a imagem por causa disso (avatar sumia). Registro
# explícito garante o Content-Type certo em qualquer ambiente.
mimetypes.add_type("image/webp", ".webp")
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from app.agent import run_agent, extrair_perfil_lead_via_ia, _to_plain_dict
from app.config import OPERATOR_PASSWORD, META_PIXEL_ID
from app import meta_capi
from app.store import (
    CONVERSAS, OPERADORES_CONECTADOS, Conversa,
    nova_conversa, obter, listar, tocar, compactar_conversa_encerrada,
    registrar_atualizacao_lead, inicializar_banco_e_carregar, salvar_no_banco,
    excluir_conversa, arquivar_lead_concluido,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("federal-webchat")

app = FastAPI(title="Federal Connect - Web Chat")


@app.on_event("startup")
async def _ao_subir():
    # Carrega tudo que já tava salvo no Postgres (Neon) pro dicionário em
    # memória — sem isso, todo redeploy do Render apagava as conversas em
    # andamento. Roda em thread pra não travar a subida do servidor.
    await asyncio.to_thread(inicializar_banco_e_carregar)


async def _persistir(conversa: Conversa) -> None:
    """Grava a conversa no banco em thread separada (I/O de rede)."""
    await asyncio.to_thread(salvar_no_banco, conversa)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def pagina_chat():
    # Injeta o ID do Pixel da Meta (env META_PIXEL_ID) no HTML. Vazio = o
    # script do Pixel nem carrega, o chat segue funcionando normal.
    with open("static/chat.html", encoding="utf-8") as f:
        html = f.read()
    pixel_id = META_PIXEL_ID if META_PIXEL_ID.isdigit() else ""
    # 27/09: sem isso, alguns navegadores/webviews de celular guardam a
    # página por conta própria (sem servidor mandar nenhuma instrução) e o
    # lead/operador fica preso numa versão de antes de um ajuste — foi o
    # motivo do Diegão não ver mudança nenhuma num teste. no-store força
    # buscar a versão atual toda vez; a página é pequena, não pesa.
    return HTMLResponse(
        html.replace("__META_PIXEL_ID__", pixel_id),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/painel")
async def pagina_painel():
    # Mesmo motivo do no-store na página do chat (ver pagina_chat acima).
    return FileResponse("static/admin.html", headers={"Cache-Control": "no-store"})


@app.get("/exportar-leads.csv")
async def exportar_leads(x_senha: str | None = Header(default=None)):
    # Senha vem no header X-Senha, não na URL — query string fica gravada em
    # texto puro nos logs de acesso do Render (achado no incidente de 27/09).
    if x_senha != OPERATOR_PASSWORD:
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


async def _atualizar_perfil_lead(conversa: Conversa) -> bool:
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
    telefone_antes = conversa.lead_phone

    if perfil.get("nome") and perfil["nome"] != conversa.lead_name:
        conversa.lead_name = perfil["nome"]
        mudou = True

    if perfil.get("telefone"):
        apenas_digitos = re.sub(r"\D", "", perfil["telefone"])
        if 10 <= len(apenas_digitos) <= 11 and apenas_digitos != conversa.lead_phone:
            conversa.lead_phone = apenas_digitos
            mudou = True

    # Origem vinda do link do anúncio (utm) é dado exato — a IA não sobrescreve.
    origem_do_anuncio = (conversa.lead_origem or "").startswith("anúncio:")
    if perfil.get("origem") and perfil["origem"] != conversa.lead_origem and not origem_do_anuncio:
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

    # True só na primeira vez que o WhatsApp do lead aparece — é o gatilho
    # do evento "Lead" do Pixel (um disparo por lead, sem duplicar).
    return bool(conversa.lead_phone) and not telefone_antes


async def _avisar_evento_pixel(conversa: Conversa, nome: str) -> None:
    """
    Dispara um evento (Lead, InitiateCheckout, CompleteRegistration, ...)
    nos DOIS canais — Pixel do navegador E API de Conversões (CAPI, 28/09) —
    com o MESMO event_id, pra Meta deduplicar automaticamente (conta como
    um evento só). CAPI reforça sinal pra quem tem Pixel de navegador
    bloqueado (iPhone, ad blocker) — achado na análise da campanha (28/09).
    """
    event_id = meta_capi.novo_event_id()

    ws = conversa.websocket_visitante
    if ws is not None:
        try:
            await ws.send_json({"type": "evento", "nome": nome, "event_id": event_id})
        except Exception:
            pass

    ip_cliente = ws.client.host if ws is not None and ws.client else None
    user_agent = ws.headers.get("user-agent") if ws is not None else None
    await asyncio.to_thread(
        meta_capi.enviar_evento,
        nome, event_id, ip_cliente, user_agent, conversa.lead_phone,
    )


# Ordem do funil pra detectar TRANSIÇÃO de estágio (evento dispara só na
# primeira vez que a conversa alcança aquele ponto, nunca de novo a cada
# mensagem seguinte que ficar no mesmo estágio ou mais à frente).
_ORDEM_ESTAGIO = [
    "novo", "conversando", "aguardando_humano", "com_humano",
    "aguardando_pagamento", "aguardando_ativacao", "concluido",
]


def _indice_estagio(estagio: str) -> int:
    return _ORDEM_ESTAGIO.index(estagio) if estagio in _ORDEM_ESTAGIO else -1


def _eventos_pixel_por_transicao(estagio_antes: str, estagio_depois: str) -> list[str]:
    """
    Mapeia transição de estágio pra eventos padrão do Pixel (28/09, pedido
    Diegão — dar mais sinal pro Facebook otimizar por quem avança de
    verdade no funil, não só por quem abre o chat):
    - InitiateCheckout: primeira vez que chega em "aguardando_pagamento"
      (a IA já mandou o lead pra plataforma de adesão/pagamento).
    - CompleteRegistration: primeira vez que chega em "aguardando_ativacao"
      OU "concluido" (contrato já assinado e validado — cadastro completo).
    """
    eventos = []
    if _indice_estagio(estagio_depois) >= _indice_estagio("aguardando_pagamento") > _indice_estagio(estagio_antes):
        eventos.append("InitiateCheckout")
    if _indice_estagio(estagio_depois) >= _indice_estagio("aguardando_ativacao") > _indice_estagio(estagio_antes):
        eventos.append("CompleteRegistration")
    return eventos


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


async def _enviar_mensagem_para_operadores_da_conversa(
    conversa: Conversa, remetente: str, texto: str, excluir_ws: WebSocket | None = None
):
    """
    `excluir_ws`: o próprio painel de quem mandou a mensagem já renderiza ela
    localmente na hora do clique (otimista, sem esperar o servidor) — sem
    excluir esse websocket do broadcast, o remetente recebia a mensagem de
    volta e ela aparecia duplicada na tela dele (bug reportado: "quando eu
    assumi uma conversa com o Sandro, estava mandando mensagem em
    duplicidade"). Os outros operadores conectados continuam recebendo
    normalmente.
    """
    payload = {
        "type": "mensagem",
        "session_id": conversa.session_id,
        "remetente": remetente,  # "lead" | "ia" | "operador"
        "texto": texto,
    }
    mortos = []
    for ws in OPERADORES_CONECTADOS:
        if ws is excluir_ws:
            continue
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
async def ws_chat(
    websocket: WebSocket,
    session_id: str | None = Query(default=None),
    origem: str | None = Query(default=None),
):
    await websocket.accept()

    conversa = obter(session_id) if session_id else None
    if conversa is None:
        conversa = nova_conversa()
        # utm_campaign/utm_content do link do anúncio (ex: "fb/app-entregas/
        # vivo-40g") — deixa o painel/CSV mostrar qual conjunto e qual imagem
        # trouxe cada lead, pra comparar de verdade o que converte.
        if origem:
            conversa.lead_origem = "anúncio:" + re.sub(r"[^\w\-/. ]", "", origem)[:80]
        # 27/09 (incidente "leads sumiram do painel"): antes a sessão só ia
        # pro banco quando o visitante mandava a 1ª mensagem — quem abria o
        # chat e não digitava aparecia no painel e sumia a cada redeploy, e
        # quem recarregava a página depois de um restart caía de novo na
        # boas-vindas. Agora grava já na abertura (upsert, não apaga nada).
        await _persistir(conversa)

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
            # Heartbeat do chat (27/09): mesmo esquema do painel — a conexão
            # do visitante morria calada (celular em segundo plano, rede) e
            # a mensagem digitada não saía. O chat manda ping e reconecta
            # sozinho se não receber pong.
            if data.get("tipo") == "ping":
                await websocket.send_json({"type": "pong"})
                continue
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
                if await _atualizar_perfil_lead(conversa):
                    await _avisar_evento_pixel(conversa, "Lead")
                await _persistir(conversa)
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

            # Manda pra conexão ATUAL do visitante (se ele reconectou enquanto
            # a IA pensava, a antiga já morreu) e não deixa uma falha de envio
            # impedir a gravação no banco logo abaixo — a resposta fica no
            # histórico e aparece quando ele reconectar.
            try:
                await (conversa.websocket_visitante or websocket).send_json(
                    {"type": "mensagem", "remetente": "ia", "texto": resposta}
                )
            except Exception:
                logger.warning("Falha ao entregar resposta da IA ao visitante %s (desconectado)", conversa.session_id)
            await _enviar_mensagem_para_operadores_da_conversa(conversa, "ia", resposta)

            # CRM leve: mesmo refresh de nome/telefone/origem/notas via IA
            # que roda no ramo "humano_ativo" acima — mantém o painel em dia
            # a cada mensagem, seja quem for que está respondendo o lead.
            if await _atualizar_perfil_lead(conversa):
                await _avisar_evento_pixel(conversa, "Lead")

            # Classificação automática — a IA decide o estágio sozinha,
            # ninguém no painel precisa clicar pra mudar isso manualmente.
            estagio_antes = conversa.estagio
            conversa.estagio = resultado["estagio_sugerido"]
            if resultado["handoff_requested"] and not conversa.handoff_link_enviado:
                conversa.handoff_link_enviado = True

            # Pixel (28/09, pedido Diegão): InitiateCheckout/CompleteRegistration
            # disparam na primeira vez que a conversa alcança cada marco —
            # dá ao Facebook sinal de quem avança de verdade no funil, não só
            # quem abre o chat (isso já existia só pro evento Lead).
            for evento in _eventos_pixel_por_transicao(estagio_antes, conversa.estagio):
                await _avisar_evento_pixel(conversa, evento)

            if conversa.estagio == "concluido":
                # 27/09 (pedido Diegão): lead finalizado é arquivado numa
                # planilha externa pra preservar o histórico. IMPORTANTE
                # (28/09, correção de segurança): NÃO exclui mais da base
                # viva (Postgres) automaticamente — o disco onde o CSV de
                # arquivo é gravado é efêmero no deploy (Render/Cloud Run),
                # some a cada restart/redeploy, então excluir daqui apagava
                # o lead de vez sem garantia nenhuma de que o arquivo
                # sobreviveu. Até a planilha externa ser um Google Sheets de
                # verdade (via service account, item já no checklist), o
                # lead só é compactado e marcado "concluido" — continua
                # visível no painel, sem risco de sumir.
                compactar_conversa_encerrada(conversa)
                arquivar_lead_concluido(conversa)
                await _persistir(conversa)
                await _broadcast_painel()
                continue

            await _persistir(conversa)
            await _broadcast_painel()

    except WebSocketDisconnect:
        # Só limpa se ainda for esta conexão — se o visitante já reconectou,
        # a queda tardia da conexão antiga não pode apagar a nova (senão a
        # mensagem do operador deixava de chegar nele).
        if conversa.websocket_visitante is websocket:
            conversa.websocket_visitante = None
        await _broadcast_painel()


# ---------------------------------------------------------------------------
# WebSocket do OPERADOR (Sandro/Marcos) — static/admin.html conecta aqui.
# Autenticação simples por senha (MVP de teste — trocar por login real
# antes de expor pra mais gente além de vocês dois).
# ---------------------------------------------------------------------------

@app.websocket("/ws/painel")
async def ws_painel(websocket: WebSocket):
    # A senha chega como 1ª mensagem ({"acao": "auth", "senha": ...}), não na
    # URL — query string fica gravada em texto puro nos logs de acesso do
    # Render (achado no incidente de 27/09).
    await websocket.accept()
    try:
        auth = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=10))
    except Exception:
        auth = {}
    if not isinstance(auth, dict) or auth.get("acao") != "auth" or auth.get("senha") != OPERATOR_PASSWORD:
        try:
            await websocket.close(code=4001)
        except Exception:
            pass
        return

    OPERADORES_CONECTADOS.add(websocket)
    await _broadcast_painel()

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            acao = data.get("acao")
            # Heartbeat do painel (27/09): no celular a conexão morre calada
            # (tela bloqueada / troca de rede) e o painel ficava clicando no
            # vazio. O painel manda ping a cada 20s e, sem pong, reconecta.
            if acao == "ping":
                await websocket.send_json({"type": "pong"})
                continue
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
                await _persistir(conversa)
                await _broadcast_painel()

            elif acao == "liberar_para_ia":
                conversa.humano_ativo = False
                conversa.estagio = "conversando"
                await _persistir(conversa)
                await _broadcast_painel()

            elif acao == "mudar_estagio":
                novo_estagio = data.get("estagio")
                if novo_estagio:
                    conversa.estagio = novo_estagio
                    if novo_estagio == "concluido":
                        # Mesmo arquivamento de quando a IA conclui sozinha
                        # (ver ws_chat) — aqui é quando o operador marca
                        # "concluído" manualmente no painel. Mesma correção
                        # de segurança de 28/09: não exclui mais da base
                        # viva (ver comentário em ws_chat acima).
                        compactar_conversa_encerrada(conversa)
                        arquivar_lead_concluido(conversa)
                        await _persistir(conversa)
                        await _broadcast_painel()
                    else:
                        await _persistir(conversa)
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
                await _enviar_mensagem_para_operadores_da_conversa(conversa, "operador", texto, excluir_ws=websocket)
                await _persistir(conversa)
                await _broadcast_painel()

            elif acao == "excluir":
                # Apaga de vez (memória + banco) — pra limpar conversas de
                # teste antes de uma campanha rodar valendo, sem misturar
                # com dado de lead de verdade no CRM/relatório.
                await asyncio.to_thread(excluir_conversa, session_id)
                await _broadcast_painel()

    except WebSocketDisconnect:
        OPERADORES_CONECTADOS.discard(websocket)


app.mount("/static", StaticFiles(directory="static"), name="static")
