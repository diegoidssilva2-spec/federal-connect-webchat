# Federal Conect — Web Chat + Painel do Operador (MVP pro Sandro/Marcos testarem)

Site com chat próprio: o lead conversa com a IA direto no navegador, sem
depender da API oficial da Meta/WhatsApp. Sandro/Marcos acompanham tudo em
tempo real por um painel separado e podem assumir qualquer conversa.

## O que tem aqui

- **`/` (static/chat.html)** — página que o lead acessa (é pra onde a
  campanha vai direcionar o clique do anúncio)
- **`/painel` (static/admin.html)** — painel do operador, com senha
- **CRM leve embutido**: cada conversa tem um estágio (novo → conversando →
  aguardando humano → com humano → aguardando pagamento → aguardando
  ativação → concluído), visível e editável no painel — sem integração
  externa nenhuma por enquanto

## Como funciona o handoff (as duas formas, como você pediu)

1. **Automático pela IA**: quando a cartilha manda escalonar (ex: lead
   pede humano, reclamação, etc.), a conversa muda sozinha pro estágio
   "aguardando humano" e a própria resposta da IA já inclui o link do
   Sandro (`wa.me/5511940511444`) — igual já funcionava no WhatsApp.
2. **Manual pelo operador**: a qualquer momento, Sandro ou Marcos podem
   clicar em "Assumir conversa" no painel — a partir daí a IA para de
   responder automaticamente, e tudo que o operador digitar no painel
   chega direto pro lead no chat, em tempo real. Um botão "Devolver pra
   IA" reverte isso quando quiserem.

## Testar local (antes de subir em qualquer lugar)

```bash
cd federal-connect-webchat
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sua-chave-aqui"
export OPERATOR_PASSWORD="escolha-uma-senha"
uvicorn app.main:app --reload --port 8000
```

- Abra `http://localhost:8000/` numa aba — é a visão do lead
- Abra `http://localhost:8000/painel` em outra aba — é a visão do Sandro/Marcos
- Manda mensagem na aba do lead e veja aparecer em tempo real no painel
- Clica em "Assumir conversa" no painel e testa respondendo direto por lá

## Deploy no Cloud Run (quando o teste local aprovar)

Mesmo processo dos outros dois projetos (hamburgueria e agente WhatsApp):

```bash
echo -n "sua-chave-anthropic" | gcloud secrets create ANTHROPIC_API_KEY --data-file=-
echo -n "senha-forte-aqui" | gcloud secrets create OPERATOR_PASSWORD --data-file=-

gcloud run deploy federal-connect-webchat \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --min-instances=1 \
  --max-instances=1 \
  --set-secrets="ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,OPERATOR_PASSWORD=OPERATOR_PASSWORD:latest"
```

> De novo o mesmo aviso de sempre: `--min-instances=1 --max-instances=1`
> porque o estado das conversas ainda é em memória (mesma lógica do
> projeto anterior). Antes de rodar campanha paga de verdade com volume,
> migrar esse estado pra um banco de verdade.

Depois do deploy, a URL pública vira o link que a campanha vai usar (ex:
`https://federal-connect-webchat-xxxxx-uc.a.run.app`), e o painel fica em
`.../painel`.

## Limitações honestas deste MVP (pra vocês saberem o que ainda falta)

- **Senha única pro painel** — serve pra Sandro/Marcos testarem agora;
  antes de outras pessoas acessarem, precisa de login individual
- **Sem persistência**: se o servidor reiniciar, todas as conversas em
  andamento se perdem (mesmo aviso do outro projeto)
- **Confirmação de comprovante de pagamento**: a lógica de validar
  print/imagem (ClickSign) que já existe no `agent.py` funciona igual
  aqui — o lead pode mandar imagem? **Ainda não** — o chat web atual só
  manda texto. Anexar imagem no chat web é a próxima coisa a construir,
  se vocês quiserem manter esse fluxo de validação por print.
- **Sem "digitando..."** nem confirmação de leitura — cosmético, dá pra
  adicionar depois se fizer diferença na conversão.

## Próximos passos sugeridos

1. Testar local com Sandro/Marcos simulando lead + operador
2. Adicionar envio de imagem no chat (pro fluxo do print do ClickSign)
3. Deploy no Cloud Run
4. Só depois: subir campanha apontando pro link do chat
