import os

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
PRIMARY_MODEL = os.environ.get("PRIMARY_MODEL", "claude-haiku-4-5-20251001")

# 02/10 (otimização de custo): a busca na web do bot ficou DESLIGADA por padrão.
# Cada busca é cobrada à parte e o prompt já traz todos os planos e preços da
# Federal; a busca só trazia risco de preço de concorrente errado. Pra religar,
# criar a variável BUSCA_WEB=1 no Render (sem deploy de código).
BUSCA_WEB = os.environ.get("BUSCA_WEB", "0").strip() == "1"

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
# 01/10 (auditoria): sem valor padrão — o antigo estava no GitHub. Se a
# variável faltar no Render, o painel e o export de leads ficam FECHADOS.
OPERATOR_PASSWORD = os.environ.get("OPERATOR_PASSWORD", "")

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

# Arquivo/planilha externa de leads FINALIZADOS (27/09, pedido Diegão via
# Sandro): quando um lead é marcado "concluido", os dados dele são gravados
# aqui e removidos da base viva (CONVERSAS/Postgres) — preserva o histórico
# pra contato futuro sem deixar a base ativa crescendo pra sempre com quem
# já converteu. Formato CSV por enquanto (upgrade natural: Google Sheets via
# service account, mesma credencial pendente do checklist do MCP de
# planilha — até lá isso já resolve o pedido sem depender dela).
ARQUIVO_LEADS_CSV_PATH = os.environ.get("ARQUIVO_LEADS_CSV_PATH", "leads_arquivados_federal_connect.csv")

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

# API de Conversões da Meta (CAPI, 28/09) — manda os mesmos eventos do Pixel
# também pelo SERVIDOR (não só pelo navegador do lead). Reforça o sinal pra
# quem usa iPhone/ad blocker/navegador que bloqueia o Pixel — achado na
# análise da campanha: o Pixel de navegador nunca perde evento por bloqueio
# de rede do lado do servidor. Gerar em Gerenciador de Eventos > Pixel
# Federal Connect > Configurações > API de Conversões > Gerar token de
# acesso. Sem essa variável, tudo continua funcionando igual — CAPI só fica
# desligado (o Pixel de navegador sozinho já funcionava antes disso).
META_CAPI_ACCESS_TOKEN = os.environ.get("META_CAPI_ACCESS_TOKEN", "").strip()
# Código de teste (opcional) — só usado quando testando em Eventos de Teste
# no Gerenciador de Eventos. Deixar vazio em produção.
META_CAPI_TEST_EVENT_CODE = os.environ.get("META_CAPI_TEST_EVENT_CODE", "").strip()

# Alerta de lead parado em "aguardando pagamento" (01/10, app/alerta_telegram.py).
# Bot "FLYER Avisos" do Diegão. Vazio = alerta desligado, resto igual.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# Service account do Google Cloud (28/09, projeto FLYER-Automacao) — dá
# acesso de leitura/escrita direto em planilhas e pastas do Drive que forem
# compartilhadas com o e-mail dela como Editor. Usado por app/google_service.py.
# Valor esperado: o conteúdo INTEIRO do arquivo JSON da chave, como string
# (cola o JSON todo numa variável de ambiente só). Vazio = os recursos que
# dependem dela (edição de planilha existente, busca de imagem de plano no
# Drive) ficam desativados sem quebrar o resto do projeto.
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")

# Pasta do Drive onde ficam as imagens de plano e os vídeos explicativos do
# chat (30/09, ver app/midias.py). Padrão: "FLYER — Tudo (compartilhado com
# Service Account)". O arquivo é achado pelo NOME (ex: plano_claro.png).
DRIVE_PASTA_MIDIAS_ID = os.environ.get(
    "DRIVE_PASTA_MIDIAS_ID", "1if5DJUdDbOhd1saPvAmFTBW-DLcLQOXO"
).strip()
