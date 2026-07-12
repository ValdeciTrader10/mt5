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
  motor    Fase 2: lê candles → calcula níveis/estrutura/regime no banco
  estrateg Fase 4 (sombra): decide entradas por confluências → grava em decisoes
  web      painel FastAPI com login/senha  ── lê o banco, renderiza gráficos
  caddy    HTTPS (autoassinado no IP) + proxy reverso para o painel
```

## Estado atual (Fases 1 e 2)

Entregue:
- **Coletor (Fase 1):** backfill de 6 meses (M5/M15/H1/D1) + loop coletando a cada candle M5
  fechado, gravando spread. `sistema_forex/coletor_mt5.py`.
- **Motor de análise (Fase 2):** lê os candles e calcula a "memória" do sistema — suportes/
  resistências (clusterização de swings), FVGs, gaps, estrutura SMC (HH/HL/LH/LL, BOS/CHOCH)
  e o regime por ADX. Roda como serviço próprio, só sobre o banco (não fala com o MT5).
  Indicadores em funções puras e testadas. `sistema_forex/indicadores.py`, `sistema_forex/analise.py`.
- **Ponte MT5** com lock global (thread-safe), `verificar_margem()` e `preco_pip()` já prontos
  para as próximas fases. `sistema_forex/mt5_bridge.py`.
- **Banco** com o schema completo do sistema (candles + níveis + estrutura + regime + decisões
  + trades). `sistema_forex/db.py`.
- **Painel web** com login (bcrypt + sessão assinada), status da coleta, quadro de estrutura de
  mercado (regime + contagem de níveis por par) e gráfico de candles offline com os níveis
  S/R e as zonas de FVG desenhados. `sistema_forex/web/`, `sistema_forex/grafico.py`.
- **Stack Docker** (mt5 + coletor + motor + web + caddy). `deploy/`.

- **Estrategista (Fase 4 — modo sombra):** a cada candle M5 fechado, monta o snapshot do par
  (regime + níveis + estrutura) e decide entrada por confluências, com filtros de sessão e
  spread. Registra CADA decisão (entrou/não entrou + motivo) em `decisoes` — **sem enviar
  ordens**. Lógica pura e testada. `sistema_forex/estrategias.py`, `sistema_forex/decisao.py`.
  O painel mostra as decisões recentes ao vivo.

Testes: `python -m sistema_forex.tests.test_indicadores` e `... .test_estrategias`.

A implementar (próximas fases, doc §8):
- **Fase 5** — executor + **gestor de saída tick-speed** (força contrária, ~1s) + gestão ativa
  + Telegram completo, em conta **demo por 30 dias**. O modo sombra da Fase 4 gera as entradas
  que o gestor passa a gerenciar.

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
