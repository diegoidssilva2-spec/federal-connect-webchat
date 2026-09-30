# DOSSIÊ DE AUDITORIA — Federal Connect (webchat de vendas com IA)

Preparado pelo Opus em 29/09/2026 pra auditoria completa pelo Fable.
Fluxo combinado: **Fable audita (só lê, não altera nada) → Sonnet executa as
correções aprovadas → Opus ou Fable revisa o diff antes de subir.**

---

## 0. Instruções pro auditor

- Você é o auditor sênior. Leia o código inteiro deste repositório (app/,
  static/, Dockerfile, requirements.txt) e este dossiê. Não altere arquivos,
  não faça commit, não acesse produção.
- Objetivo: dizer se a arquitetura faz sentido, o que está fora do que faz
  sentido, e o que falta pra ficar seguro e robusto — com prioridade.
- Prioridade máxima do dono do negócio: **nunca perder dado de cliente** e
  **o bot nunca parar calado**. Depois: segurança, custo, robustez, qualidade
  de venda.
- Entregue o relatório no formato da seção 9. Seja concreto: arquivo e
  linha, cenário real de falha, correção mínima. Não reescreva o sistema.
- Leia também o mapa de aprendizados do projeto
  (`APRENDIZADOS_FEDERAL_CONNECT.md`, pasta "Federal Connect - IAC
  Atendimento" no Drive) — tem a linha do tempo de falhas já corrigidas; não
  reabra o que já foi resolvido, a não ser que a correção tenha furo.

---

## 1. Contexto de negócio

- Venda de planos de celular Vivo/Claro/TIM da Federal (associação parceira
  oficial das operadoras). O lead chega por anúncio da Meta, conversa com o
  bot no site, recebe link de cadastro, paga, manda print do contrato
  assinado, e a linha é ativada pela Federal. Um humano (Gabriel) dá
  suporte; operadores acompanham tudo num painel e podem assumir a conversa.
- Volume atual: baixo (dezenas de leads/dia), campanha de R$15/dia. Deve
  crescer.
- Operação feita por 1 pessoa (Diegão) + IAs. Manutenção precisa ser simples.

## 2. Arquitetura

```
Visitante (celular) ── static/chat.html ── WebSocket /ws/chat ──┐
                                                                 │
Operador ── static/admin.html ── WebSocket /ws/painel ──────────┤
                                                                 ▼
                                      app/main.py (FastAPI, 1 processo, Render)
                                        │   ├─ app/store.py  (CONVERSAS em memória + CSV em disco)
                                        │   ├─ app/db.py     (Postgres Neon — upsert por conversa)
                                        │   ├─ app/meta_capi.py (API de Conversões da Meta)
                                        │   └─ app/google_service.py (Sheets/Drive — ainda não ligado)
                                        ▼
                                      app/agent.py (LangGraph → Anthropic Claude Haiku 4.5,
                                                    ferramenta web_search, prompt com cache)
                                        └─ app/knowledge_base.py (prompt de sistema ~12 mil tokens)
```

- Deploy: push na `main` do GitHub → Render reconstrói (Dockerfile, Python
  3.12) e reinicia. Disco do Render é efêmero (some a cada deploy).
- Banco: Postgres no Neon, tabela única `conversas` (histórico em JSONB).
  Ao subir, carrega tudo pra memória; a cada mudança relevante faz upsert.
- Estado vivo (conexões WebSocket, travas por conversa) fica só em memória —
  o sistema assume **uma única instância**.

## 3. Mapa de arquivos

| Arquivo | Linhas | Função |
|---|---|---|
| app/main.py | ~670 | Rotas HTTP, WebSockets do chat e do painel, orquestração, tratamento de falha da IA, eventos Pixel/CAPI |
| app/agent.py | ~300 | Chamada à Anthropic via LangGraph, extração de perfil do lead (2ª chamada), classificação de estágio por palavra-chave |
| app/knowledge_base.py | ~830 | Prompt de sistema (roteiro de venda, planos, fluxo de cadastro, regras duras) |
| app/store.py | ~280 | Conversas em memória, CSV de leads, webhook opcional, arquivamento de concluídos |
| app/db.py | ~160 | Persistência no Postgres |
| app/meta_capi.py | ~110 | Envio de eventos server-side pra Meta (telefone com hash) |
| app/google_service.py | ~160 | Sheets/Drive via service account (preparado, não usado ainda) |
| app/config.py | ~95 | Variáveis de ambiente |
| static/chat.html | ~560 | Chat do visitante (reconexão automática, ping 20s, Pixel) |
| static/admin.html | ~790 | Painel do operador (lista, conversa, assumir, relatório, exportar CSV) |

## 4. Rotas

- `GET /` — página do chat (injeta o ID do Pixel).
- `GET /painel` — página do painel.
- `GET /exportar-leads.csv` — exporta leads; senha no header `x-senha`.
- `GET /health` — status simples. `GET /saude-ia` — estado da IA (último
  sucesso/falha e motivo), público.
- `WS /ws/chat?session_id=&origem=` — conversa do visitante.
- `WS /ws/painel` — operador; 1ª mensagem precisa ser `{"acao":"auth","senha":...}`.

## 5. Fluxos críticos (conferir cada um)

1. **Mensagem do lead:** chega no WS → espelha pro painel → (se humano
   assumiu, só registra) → IA em thread com trava por conversa → resposta →
   extração de perfil (nome/telefone) por IA → estágio por palavra-chave →
   eventos Pixel/CAPI → upsert no banco → atualiza painel.
2. **Falha da IA** (crédito, chave, sobrecarga): lead recebe aviso + link do
   humano; mensagem salva no histórico; conversa vai pra
   "aguardando_humano"; `/saude-ia` registra o motivo.
3. **Reconexão do visitante:** servidor reenvia o histórico; mensagem
   digitada durante a queda é reenviada.
4. **Operador assume a conversa:** IA para de responder; mensagens do
   operador vão pro visitante.
5. **Lead concluído:** compacta histórico, grava CSV (disco efêmero), NÃO
   apaga do banco (já causou quase-perda de dados — ver aprendizados).
6. **Deploy/restart:** tudo recarregado do Postgres.

## 6. Variáveis de ambiente (nomes — valores ficam no Render)

ANTHROPIC_API_KEY, PRIMARY_MODEL, DATABASE_URL, OPERATOR_PASSWORD,
LINK_ADESAO_FEDERAL, LINK_ATIVACAO_FEDERAL, CONSULTOR_HUMANO_NOME,
CONSULTOR_HUMANO_WHATSAPP_NUMERO, LEADS_CSV_PATH, ARQUIVO_LEADS_CSV_PATH,
CRM_WEBHOOK_URL, META_PIXEL_ID, META_CAPI_ACCESS_TOKEN,
META_CAPI_TEST_EVENT_CODE, GOOGLE_SERVICE_ACCOUNT_JSON.

## 7. Riscos que o Opus já suspeita (confirmar ou descartar, com prioridade)

**Custo / abuso**
- Não há limite de mensagens por sessão/IP nem de tamanho de imagem no
  WebSocket do chat. Um robô pode queimar o crédito da Anthropic (o crédito
  já acabou uma vez em 29/09) ou derrubar o servidor com imagens grandes.
- `web_search` ligado no bot: custo extra por busca e porta de entrada pra
  conteúdo externo não confiável no contexto.
- Cada mensagem gera 2 chamadas de IA (resposta + extração de perfil).

**Segurança**
- `OPERATOR_PASSWORD` tem valor padrão fixo no código (`config.py`) — se a
  variável sumir do Render, o painel abre com senha conhecida.
- Senha do painel comparada sem tempo constante e sem limite de tentativas
  (força bruta no WS e no `/exportar-leads.csv`, que exporta dado pessoal).
- WebSocket não confere a origem (Origin).
- Quem souber um `session_id` recebe o histórico inteiro daquela conversa
  (nome e telefone). Conferir se o id é aleatório forte (uuid4) e se isso
  basta.
- Prompt injection: o lead pode tentar fazer o bot revelar instruções,
  links internos ou mudar preço.

**Dados (LGPD e durabilidade)**
- Nome e telefone guardados sem política de retenção/exclusão a pedido.
- CSV de leads e de arquivados em disco efêmero (não é backup).
- Upsert da conversa inteira a cada mensagem (histórico cresce; custo e
  risco de sobrescrever com estado velho).

**Robustez**
- Arquitetura presa a 1 instância (estado em memória + travas locais).
- Dependências sem versão fixa (`>=`) — deploy pode quebrar sozinho.
- Estágio do funil decidido por palavra-chave no texto da resposta (frágil;
  ex: fluxo novo do chip físico não marca "aguardando_ativacao").
- Não há testes automatizados.
- Painel e chat dependem de WebSocket com ping de 20s — conferir
  comportamento com celular em segundo plano e rede ruim.

**Qualidade de venda (prompt)**
- Prompt de ~12 mil tokens com muitas seções que se sobrepõem — risco de
  regras contraditórias (já causou bugs). Avaliar se deveria ser mais curto
  e estruturado.

## 8. Perguntas diretas pro Fable

1. A arquitetura (FastAPI + WebSocket + memória + Neon + Render) faz sentido
   pro volume atual e pra 10x esse volume? O que quebra primeiro?
2. Qual o menor conjunto de mudanças que elimina o risco de perder dado de
   cliente e de o bot parar calado?
3. Como limitar abuso e custo da IA sem atrapalhar lead de verdade?
4. O que falta de segurança pra um sistema que guarda nome e telefone de
   clientes (LGPD)?
5. O prompt de sistema tem contradições? Vale reestruturar? Como?
6. LangGraph está agregando algo aqui ou é complexidade desnecessária pra uma
   chamada só?
7. O que você mudaria primeiro se fosse seu?

## 9. Formato do relatório

1. **Veredito geral** (5 linhas): faz sentido? nota de 0 a 10 pra
   segurança, robustez, custo e manutenção.
2. **Tabela de achados**, ordenada por prioridade:

| Prioridade | Área | Arquivo:linha | Problema | Cenário real de falha | Correção mínima | Esforço (P/M/G) | Quem executa |
|---|---|---|---|---|---|---|---|

   Prioridade: **P0** = pode perder dado/dinheiro/cliente agora; **P1** =
   risco alto, corrigir esta semana; **P2** = melhoria importante; **P3** =
   quando der. "Quem executa": Sonnet (código de produção) ou Haiku (só
   mudança mecânica: texto, configuração, versão de dependência).
3. **Respostas às perguntas da seção 8.**
4. **O que NÃO mexer** (o que está bom e deve ficar como está).

## 10. Fora do escopo

- Não mudar planos, preços ou regras de negócio (vêm da Federal).
- Não propor trocar de provedor de IA ou de hospedagem sem motivo forte.
- Campanha de anúncios (Meta Ads) não faz parte desta auditoria.
