# Instruções pro Claude — FLYER / Federal Connect

Lido automaticamente no início de toda sessão do Claude Code aberta nesta pasta.
Se esta cópia estiver dentro do repo e a sessão estiver aberta na pasta de cima,
copiar este arquivo pra raiz da pasta de trabalho.

## Quem é e como falar

- Diegão, fundador da FLYER (holding de marketing/produção digital). Fala por
  transcrição de voz: interpretar a intenção sem pedir repetição.
- Sempre em português. Sócio operacional: executar direto o que é óbvio, só
  parar pra dinheiro, autorização de terceiro, ambiguidade real ou ação
  irreversível.
- Antes de cada tarefa, dizer qual modelo é o ideal pra ela (Opus pra dado de
  produção, banco, decisão cara; Sonnet pro dia a dia; Haiku pra coisa trivial).

## Cérebro Mestre (Google Drive) — fonte da verdade

- Pasta raiz: id `1EuF9_MpYxeH_cY0sno_ZgcU7SQn49Zh0`.
- Toda sessão começa lendo `_INDICE_ATUAL.md` dessa pasta. Ele aponta a versão
  vigente de tudo (CARTILHA_MESTRE, CHECKLIST_GERAL, regras).
- Gatilhos: pasta `Gatilhos` (id `1bIvFzn0DxTA1CFxsMHZ9v9HzT_JWvRpj`). Vale a
  versão mais alta de cada assunto.
  - **"iniciar sistema"** (ou "iniciar modo FLYER"): ler índice, gatilhos,
    cartilha e checklist vigentes e responder no formato do GATILHOS_v18
    (✅ SISTEMA ATIVO, carregado, pastas, onde paramos, checklist, prioridade
    de hoje).
  - **"finalizar sistema"**: seguir o gatilho de finalizar vigente (v12 ou
    maior): resumo do dia por projeto, resumo geral, análise do dia,
    varredura de lixeira com links, e atualizar o `_INDICE_ATUAL.md`.
- Drive não edita arquivo de texto no lugar: atualizar = criar o novo com o
  conteúdo corrigido a partir do atual e mandar o antigo pra lixeira. Nunca
  reescrever de memória.
- **Se esta sessão não conseguir ler o Drive, dizer isso na hora.** Não fingir
  que carregou. Caminhos: conector do Google Drive, ou a service account
  `flyer-automacao-sa@flyer-automacao.iam.gserviceaccount.com` (a pasta do
  Cérebro Mestre precisaria ser compartilhada com ela como Editor; a chave
  JSON está com o Diegão).

## Regras duras (valem pra qualquer projeto)

1. Alterar só o que foi pedido. O resto do arquivo fica idêntico. Conferir o
   `git diff` linha por linha antes de dar como pronto.
2. Antes de subir qualquer coisa em produção: mapear a cadeia inteira e os
   erros irreversíveis (perda de dado de cliente acima de tudo). Nada
   destrutivo em banco de produção sem OK explícito do Diegão.
3. Disco de hospedagem (Render) some a cada deploy/reinício. Nunca excluir um
   dado antes de confirmar cópia num lugar durável.
4. Chave, senha e credencial nunca no git nem em documento compartilhado.
5. Tráfego pago: Pixel, eventos e UTM configurados antes de subir verba.
6. Todo aprendizado real vira skill/passo a passo reutilizável.
7. No fim de cada projeto: resumo de tudo que se aprendeu com ele (o que
   funcionou, erros, decisões), pra levar pros próximos projetos.
8. Cada projeto tem um `APRENDIZADOS_<PROJETO>.md` na pasta dele no Drive
   (linha do tempo de falhas e correções, o que funcionou, o que não pode
   mais errar, o que falta melhorar). Ler antes de mexer no projeto e
   acrescentar uma linha a cada falha corrigida ou melhoria. Federal Connect:
   id `1IFh0GMEvl-BajXJVucmfw6X6384zOwoi`.

## Federal Connect (este repo)

- Repo: github.com/diegoidssilva2-spec/federal-connect-webchat. Push na `main`
  dispara deploy no Render (serviço `federal-connect-webchat`), e o deploy
  reinicia o servidor.
- Banco: Postgres no Neon (`DATABASE_URL` no Render). Senha do painel:
  `OPERATOR_PASSWORD` no Render.
- Imagem de plano nunca vai pro banco: fica no Drive e é buscada na hora
  (`app/google_service.py`, liga com `GOOGLE_SERVICE_ACCOUNT_JSON`).
- Depois de cada mudança de código, sincronizar os arquivos alterados na pasta
  `CODIGO_federal-connect-webchat` do Drive (id `1X_kN6Gs3oH4QFIr_2s5WpV4Q3WfoWql7`).
- Commits terminam com a linha de coautoria do Claude.

## Prioridade aberta (27/09)

Ler `HANDOFF_sessao_local_PC_27-09.md` na pasta raiz do Cérebro Mestre:
incidente dos leads que sumiram do painel, investigar pelo Neon e pelos logs
do Render.
