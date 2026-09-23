"""
Orquestrador do agente FLYER usando LangGraph.
Fase 0: state em memória (dict). Fase 1+ troca por checkpoint Postgres/Redis
sem mudar a lógica do grafo.
"""
from typing import TypedDict, Annotated, Literal, Optional
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
import anthropic

from app.config import ANTHROPIC_API_KEY, PRIMARY_MODEL
from app.knowledge_base import SYSTEM_PROMPT

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Ferramenta de busca na web nativa da Anthropic. É executada pelo servidor
# da Anthropic dentro da própria chamada (não exige um segundo round-trip
# manual) — o modelo decide sozinho quando usar, por exemplo quando o lead
# pergunta se o preço da Federal é mais barato que o de mercado.
WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
}


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    lead_phone: str
    lead_name: str | None
    qualification_count: int
    handoff_requested: bool


def _to_plain_dict(m) -> dict:
    """
    O LangGraph converte as mensagens do state em objetos BaseMessage
    (HumanMessage/AIMessage) internamente, mesmo se você inserir dicts.
    Esta função normaliza de volta pro formato que a API da Anthropic espera.
    """
    if isinstance(m, dict):
        return {"role": m["role"], "content": m["content"]}
    role = "assistant" if getattr(m, "type", "") == "ai" else "user"
    return {"role": role, "content": m.content}


def _extract_text(content_blocks) -> str:
    """
    Extrai apenas os blocos de texto da resposta. A resposta pode conter
    outros tipos de bloco (server_tool_use, web_search_tool_result) quando
    o modelo usa a ferramenta de busca — esses blocos são ignorados aqui,
    só o texto final é enviado ao lead.
    """
    return "".join(
        block.text for block in content_blocks if block.type == "text"
    )


def call_llm(state: AgentState) -> AgentState:
    """Chama o Claude Haiku (primário) com fallback tratado no gateway HTTP."""
    history = [_to_plain_dict(m) for m in state["messages"]]
    response = client.messages.create(
        model=PRIMARY_MODEL,
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=history,
        tools=[WEB_SEARCH_TOOL],
    )
    reply_text = _extract_text(response.content)

    handoff_triggers = ["sandro", "atendimento humano", "wa.me/5511940511444"]
    handoff = any(t in reply_text.lower() for t in handoff_triggers)

    return {
        "messages": [{"role": "assistant", "content": reply_text}],
        "handoff_requested": handoff,
    }


# ---------------------------------------------------------------------------
# Classificação automática de estágio do lead — baseada em marcos que já
# existem na própria cartilha (não é uma segunda chamada de IA, é
# reconhecimento de padrão no texto da resposta, pra não gastar crédito
# extra classificando).
# ---------------------------------------------------------------------------

_MARCOS_ESTAGIO = [
    # Ordem importa: checa do mais avançado pro menos avançado, e para no
    # primeiro que bater — evita "voltar" o estágio por engano.
    ("concluido", ["iniciar o atendimento", "assistente virtual", "protocolo automaticamente", "wa.me/5508008882629"]),
    ("aguardando_ativacao", ["nunca abra", "nunca insira o chip", "aguarde a confirmação"]),
    ("aguardando_pagamento", ["assinatura do contrato", "clicksign", "print da tela de confirmação"]),
    ("conversando", []),  # fallback — qualquer resposta normal da IA
]


def classificar_estagio(reply_text: str, handoff: bool) -> str:
    if handoff:
        return "aguardando_humano"
    texto = reply_text.lower()
    for estagio, marcadores in _MARCOS_ESTAGIO:
        if not marcadores:
            return estagio
        if any(m in texto for m in marcadores):
            return estagio
    return "conversando"


# ---------------------------------------------------------------------------
# Extração de perfil do lead (nome / telefone / notas) via IA.
#
# Antes isso era feito só com regex ("meu nome é X", "sou o X"), o que não
# pega jeito natural de falar tipo "o Bruno é motorista de aplicativo".
# Aqui é uma chamada separada e barata (Haiku, poucos tokens, só os últimos
# turnos da conversa) que lê o que já foi dito e devolve os dados estrutu-
# rados via tool-use. Roda depois de cada mensagem do lead pra manter o
# painel sempre atualizado, sem depender de a pessoa falar num formato fixo.
# ---------------------------------------------------------------------------

PERFIL_LEAD_TOOL = {
    "name": "salvar_perfil_lead",
    "description": (
        "Salva os dados de identificação e contexto do lead detectados na "
        "conversa de vendas até agora."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "nome": {
                "type": ["string", "null"],
                "description": "Primeiro nome do lead, se ele mencionou em algum momento (mesmo de forma indireta, ex: 'o Bruno é motorista'). null se ainda não souber.",
            },
            "telefone": {
                "type": ["string", "null"],
                "description": "Número de celular/WhatsApp do lead com DDD, só dígitos. null se ainda não souber.",
            },
            "origem": {
                "type": ["string", "null"],
                "description": "De onde o lead veio até essa conversa, se ele mencionou (ex: 'anúncio no Instagram', 'indicação de amigo', 'Google', 'já era associado e viu no grupo'). null se não souber ainda.",
            },
            "notas": {
                "type": ["string", "null"],
                "description": "Outras informações úteis sobre o lead mencionadas na conversa (profissão, cidade, operadora atual, principal objeção, etc.), resumidas em uma frase curta. null se não houver nada relevante ainda.",
            },
        },
        "required": ["nome", "telefone", "origem", "notas"],
    },
}


def extrair_perfil_lead_via_ia(history: list[dict]) -> dict:
    """
    Não inventa dado: se não tiver certeza, o próprio prompt instrui a
    IA a devolver null naquele campo. Nunca lança exceção pro chamador —
    em caso de falha (rede, parsing), devolve tudo null e quem chamou
    simplesmente não atualiza nada naquele ciclo.
    """
    historico = [_to_plain_dict(m) for m in history]
    # Só os últimos turnos — não precisa da conversa inteira pra achar
    # nome/telefone, e mantém a chamada rápida e barata.
    historico = historico[-12:]
    while historico and historico[0]["role"] != "user":
        historico = historico[1:]
    if not historico:
        return {"nome": None, "telefone": None, "origem": None, "notas": None}

    try:
        response = client.messages.create(
            model=PRIMARY_MODEL,
            max_tokens=200,
            system=(
                "Você extrai dados de identificação de um lead a partir de um "
                "trecho de conversa de vendas em português. Não invente nada: "
                "se não tiver certeza de um campo, responda null nele. Use "
                "sempre a ferramenta salvar_perfil_lead pra responder."
            ),
            messages=historico,
            tools=[PERFIL_LEAD_TOOL],
            tool_choice={"type": "tool", "name": "salvar_perfil_lead"},
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == "salvar_perfil_lead":
                dados = block.input
                return {
                    "nome": dados.get("nome"),
                    "telefone": dados.get("telefone"),
                    "origem": dados.get("origem"),
                    "notas": dados.get("notas"),
                }
    except Exception:
        pass
    return {"nome": None, "telefone": None, "origem": None, "notas": None}


def route_after_llm(state: AgentState) -> Literal["end", "handoff"]:
    return "handoff" if state.get("handoff_requested") else "end"


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("agent", call_llm)
    graph.set_entry_point("agent")
    graph.add_conditional_edges(
        "agent",
        route_after_llm,
        {"end": END, "handoff": END},
    )
    return graph.compile()


agent_graph = build_graph()


def _build_user_content(
    user_message: str,
    image_base64: Optional[str] = None,
    image_media_type: Optional[str] = None,
) -> list | str:
    """
    Monta o content da mensagem do lead. Se vier imagem (ex: print do
    contrato ClickSign), monta um content block com imagem + texto.
    Caso contrário, retorna só a string (formato mais simples, igual antes).
    """
    if not image_base64:
        return user_message

    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": image_media_type or "image/jpeg",
                "data": image_base64,
            },
        },
        {
            "type": "text",
            "text": user_message or (
                "O lead enviou esta imagem. Se for o print de confirmação "
                "do contrato ClickSign, valide nome, data e o texto 'assinou "
                "como contratante' conforme as regras da cartilha antes de "
                "confirmar o pagamento."
            ),
        },
    ]


def run_agent(
    lead_phone: str,
    lead_name: str | None,
    user_message: str,
    history: list[dict],
    image_base64: Optional[str] = None,
    image_media_type: Optional[str] = None,
) -> dict:
    """
    Ponto de entrada usado pelo webhook. `history` é a lista de mensagens
    anteriores da conversa (recuperada do Postgres na Fase 1; em memória na Fase 0).

    `image_base64` / `image_media_type`: preenchidos pelo meta_client.py quando
    a mensagem recebida do lead é uma imagem (ex: print do ClickSign). Quando
    None, o comportamento é idêntico ao anterior (só texto).
    """
    user_content = _build_user_content(user_message, image_base64, image_media_type)

    state: AgentState = {
        "messages": history + [{"role": "user", "content": user_content}],
        "lead_phone": lead_phone,
        "lead_name": lead_name,
        "qualification_count": 0,
        "handoff_requested": False,
    }
    result = agent_graph.invoke(state)
    reply = _to_plain_dict(result["messages"][-1])["content"]
    handoff = result.get("handoff_requested", False)
    return {
        "reply": reply,
        "handoff_requested": handoff,
        "updated_history": result["messages"],
        "estagio_sugerido": classificar_estagio(reply, handoff),
    }
