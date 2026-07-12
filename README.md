# Sistema de Trading Forex M5 — Motor de Análise + Executor

Sistema em Python que coleta dados do MT5, analisa (S/R, pivots, order blocks, FVGs,
estrutura SMC, gaps), classifica o regime de mercado, decide estratégias e executa no M5
com gestão ativa. Roda **100% em Docker na VPS Hostinger**, com um **painel web protegido
por login/senha** para acompanhamento.

> Projeto Empenho Contabilidade / Valdeci. Independente do MASMC v2 (referência de lições).
> Metodologia: Think Before Coding · Simplicity First · Surgical Changes · DEBUG desde a v1.

## Como o MT5 roda no Linux

Não existe MT5 nativo para Linux — a MetaQuotes só publica o terminal Windows. A solução é
rodá-lo sob **Wine** dentro de um container (imagem [`gmag11/MetaTrader5-Docker`](https://github.com/gmag11/MetaTrader5-Docker)),
que expõe o terminal no navegador (VNC, porta 3000) e a **API Python via `mt5linux`/RPyC**
(porta 8001). Os serviços Python deste projeto conectam nessa ponte.

## Arquitetura

```
VPS Hostinger (Docker) — um único docker-compose
  mt5      terminal MT5 sob Wine  ── :3000 (VNC) · :8001 (API Python, interna)
  coletor  Fase 1: coleta candles → mercado.db (SQLite, WAL, em volume)
  web      painel FastAPI com login/senha  ── lê o banco, renderiza gráficos
  caddy    HTTPS (autoassinado no IP) + proxy reverso para o painel
```

## Estado atual (fundação — Fase 1)

Entregue:
- **Coletor (Fase 1):** backfill de 6 meses (M5/M15/H1/D1) + loop coletando a cada candle M5
  fechado, gravando spread. `sistema_forex/coletor_mt5.py`.
- **Ponte MT5** com lock global (thread-safe), `verificar_margem()` e `preco_pip()` já prontos
  para as próximas fases. `sistema_forex/mt5_bridge.py`.
- **Banco** com o schema completo do sistema (candles + níveis + estrutura + regime + decisões
  + trades). `sistema_forex/db.py`.
- **Painel web** com login (bcrypt + sessão assinada), status da coleta e gráfico de candles
  offline (Plotly embutido). `sistema_forex/web/`.
- **Stack Docker** (mt5 + coletor + web + caddy). `deploy/`.

A implementar (próximas fases, doc §8):
- **Fase 2** — motor de análise (níveis/estruturas).
- **Fase 3** — gráfico com os níveis desenhados.
- **Fase 4** — regime + 9 estratégias em **modo sombra** (só registra decisões).
- **Fase 5** — executor + gestão ativa + Telegram completo, em conta **demo por 30 dias**.

## Regras inegociáveis (lições do MASMC)

- **Demo primeiro.** Nada de conta real antes de 30 dias de demo auditados.
- Toda ordem sai **sempre** com stop de emergência no servidor (3× ATR14 M5).
- Verificar **margem** antes de todo `order_send` (retcode 10019 destruiu o teste anterior).
- Contabilidade de pips por `price_open`/`price_current` do deal — nunca diferença entre posições.
- Todas as chamadas MT5 via **lock global**.
- **Drawdown diário máximo 5%.**

## Deploy

- **No Dokploy** (o painel de deploy da VPS): siga [`deploy/DOKPLOY.md`](deploy/DOKPLOY.md).
  Usa `deploy/docker-compose.dokploy.yml` (sem Caddy — o Dokploy já faz proxy/HTTPS).
- **Standalone** (Docker Compose puro, com Caddy): [`deploy/LEIA-ME.md`](deploy/LEIA-ME.md):

```bash
cd deploy
cp .env.example .env      # preencha PAINEL_SENHA (ou _HASH), SECRET_KEY, VNC_PASSWORD...
docker compose up -d --build
# logue no MT5 uma vez (túnel SSH → http://localhost:3000)
# acesse o painel em https://IP_DA_VPS
```

## Desenvolvimento local (sem Docker)

```bash
pip install -r sistema_forex/requirements.txt
python -m sistema_forex.db            # cria o banco
uvicorn sistema_forex.web.app:app --reload   # painel em http://localhost:8000
```
O coletor exige a ponte MT5 disponível (`MT5_HOST`/`MT5_PORT`).

## Parâmetros

Tudo centralizado em `sistema_forex/config.py` (valores iniciais do doc §9). Segredos
vêm de variáveis de ambiente (`.env`), nunca hardcoded.
