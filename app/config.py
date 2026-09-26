import os

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
PRIMARY_MODEL = os.environ.get("PRIMARY_MODEL", "claude-haiku-4-5-20251001")

# Banco Postgres (Neon) — Fase 1. String de conexão do Neon, algo como
# "postgresql://usuario:senha@ep-xxx.neon.tech/neondb?sslmode=require".
# Sem essa variável configurada, o projeto continua rodando só em memória
# (Fase 0), sem quebrar nada — é só não ter persistência entre restarts.
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Link real de adesão/associado (do Marcos) — a cartilha sempre instruiu
# a IA a mandar esse link, mas ele nunca foi configurado de verdade até
# agora. Pode ser sobrescrito por variável de ambiente se mudar no futuro.
LINK_ADESAO_FEDERAL = os.environ.get(
    "LINK_ADESAO_FEDERAL",
    "https://federalassociados.com.br/pbi/cadastro/18575302062026110704"
)

# Link de ativação — pra onde o lead vai DEPOIS de pagar e assinar
# contrato, pra abrir o chamado de ativação do chip com a assistente
# virtual oficial da Federal (WhatsApp oficial deles, 0800 8882629,
# com mensagem pré-preenchida). Confirmado por Diegão em 20/09 —
# substitui o placeholder de homepage que estava aqui antes.
LINK_ATIVACAO_FEDERAL = os.environ.get(
    "LINK_ATIVACAO_FEDERAL",
    "https://wa.me/5508008882629?text=Vim%20do%20site%20da%20Federal%20Associados!"
)

# Senha simples pro painel do operador (Gabriel/Marcos). Trocar por login de
# verdade quando sair do MVP de teste.
OPERATOR_PASSWORD = os.environ.get("OPERATOR_PASSWORD", "federal2026")

# Consultor humano que assume a ativação de chip físico (handoff feito pela
# IA — ver knowledge_base.py, seção HANDOFF DE ATIVAÇÃO). Nome e WhatsApp
# configuráveis por variável de ambiente pra trocar sem precisar editar
# código (ex: se trocar de consultor de novo no futuro).
CONSULTOR_HUMANO_NOME = os.environ.get("CONSULTOR_HUMANO_NOME", "Gabriel")
CONSULTOR_HUMANO_WHATSAPP_NUMERO = os.environ.get(
    "CONSULTOR_HUMANO_WHATSAPP_NUMERO", "5513996254206"
)

# Persistência de leads em disco — CRM leve de hoje (Fase 0). Todo lead
# capturado (nome/telefone/origem/estágio) é regravado nesse CSV a cada
# atualização, então sobrevive a restart do servidor e já fica pronto pra
# ser importado por qualquer planilha/CRM real no futuro (MCP de planilha
# em desenvolvimento em outro projeto). Caminho pode ser trocado por env
# var se o volume de disco do deploy for outro.
LEADS_CSV_PATH = os.environ.get("LEADS_CSV_PATH", "leads_federal_connect.csv")

# Webhook opcional — se configurado, toda vez que um lead for atualizado o
# servidor manda um POST com os dados dele (JSON) pra essa URL. É o gancho
# pronto pra plugar automação externa (Zapier/Make/planilha do Google via
# Apps Script, etc) sem precisar mexer em código aqui de novo. Deixar vazio
# (padrão) simplesmente desativa o envio.
CRM_WEBHOOK_URL = os.environ.get("CRM_WEBHOOK_URL", "")

# Pixel da Meta (26/09) — ID do conjunto de dados criado no Gerenciador de
# Eventos. Sem essa variável o chat funciona igual, só não dispara Pixel.
# Eventos: PageView (abriu o chat), Contact (mandou a 1ª mensagem) e Lead
# (a IA capturou o WhatsApp do lead). Serve pra otimizar a campanha por
# lead de verdade e fazer remarketing de quem abriu e não conversou.
META_PIXEL_ID = os.environ.get("META_PIXEL_ID", "").strip()
