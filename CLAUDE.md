# Memória do projeto — Forex M5

## Autorização permanente (definida pelo dono em 12/07/2026)

- **Merge e publicação liberados sempre.** Todas as alterações podem ser mergeadas
  (PRs para `main`) e publicadas/deployadas **sem pedir confirmação**. Não perguntar
  antes de mergear PR, disparar redeploy ou publicar artefatos — apenas fazer e
  relatar o resultado.
- Continua valendo o bom senso: relatar o que foi feito, e ainda avisar/pausar se
  algo parecer destrutivo ou claramente fora do que foi combinado.

---

# ESTADO ATUAL (handoff — atualizado em 13/07/2026)

Sistema **rodando de ponta a ponta** em Docker na VPS Hostinger via **Dokploy**
(`deploy.empenhocontabilidade.com.br`), branch `claude/hostinger-vps-docker-web-lqpmk2`
(Trigger On Push → redeploy automático a cada push). MT5 **conectado** (conta XM DEMO
`336082748` / servidor `XMGlobal-MT5 9`). Painel: `http://187.77.235.95:8090`
(login `admin` / `PAINEL_SENHA`; aba **📈 Análise** = analítico de trades).

**Modo atual: SIMULAÇÃO sobre preço ao vivo** (`EXECUCAO_ATIVA=false`) — nenhuma ordem
real é enviada; posições virtuais gerenciadas com ticks reais. Só ligar real após auditar
a sombra (regra: demo/sombra primeiro).

## Arquitetura (um único docker-compose no Dokploy)
- **mt5**: imagem `gmag11/metatrader5_vnc:2.3` sob Wine + custom-init (`deploy/mt5/`).
  Expõe VNC (`:3100` no host, login VNC) e a API Python RPyC (`:8001`, interna).
- **coletor** (`coletor_mt5.py`): Fase 1 — candles M5/M15/H1/D1/**W1** em SQLite.
- **motor** (`analise.py`): Fase 2 — níveis (S/R, FVG, gaps), estrutura SMC, regime (ADX).
- **estrategista** (`decisao.py`): Fase 4 sombra — decide e registra (sem operar). Roda
  DUAS estratégias em paralelo por candle M5: `confluencia_v1` e `sweep_choch_v1` (cada uma
  grava sua própria linha em `decisoes`; o executor deduplica no nível de posição).
- **executor** (`executor.py`): Fase 5 — abre/gerencia posições (simulação ou real).
- **web** (`web/app.py`): painel + `/analitico`. Caddy NÃO é usado no Dokploy (o Traefik
  dele faz proxy). Compose do Dokploy: `deploy/docker-compose.dokploy.yml`.
- Banco: `sistema_forex/db.py` (SQLite WAL, migrações idempotentes em `_migrar`).

**3 bugs da imagem MT5 já corrigidos** (ver `deploy/mt5/`): (1) `mt5linux 1.0.3` sem
`-w` → fixado `mt5linux==0.1.9`; (2) RPyC do Wine é **5.2.3** → cliente fixado em
`rpyc==5.2.3` (Python 3.11); (3) numpy 2.x quebrava o MetaTrader5 → `forex-start.sh`
força `numpy<2` no Wine. Detalhes em `deploy/DOKPLOY.md`.

## Como rodar / testar / publicar
- Testes (sem pytest): `python -m sistema_forex.tests.test_gestao` (idem `test_estrategias`,
  `test_indicadores`). **24 testes, todos passando.** Rodar sempre antes de commitar.
- Compilar: `python -m py_compile sistema_forex/*.py sistema_forex/web/*.py`.
- Publicar = commit + `git push -u origin <branch>` → Dokploy redeploya sozinho.
- Env sensíveis (senha do painel, VNC, MT5) só no Environment do Dokploy — nunca no git.

## Regras inegociáveis (lições MASMC — NÃO repetir)
Verificar margem antes de order_send (retcode 10019); pips por `price_open`/`price_current`
do deal; toda ordem com stop de servidor; todas as chamadas MT5 sob lock global; DEBUG
desde a v1; DD diário máx 5%; anti-spam Telegram por flags; reset diário no topo do loop.

## Metodologia definida pelo DONO (seguir à risca)
- **Não engessar**: preferir modelo de CONFLUÊNCIA/score (peso das evidências), não muitos
  gates obrigatórios em AND — senão as entradas secam.
- **S/R fortes = H1, Diário (D1), Semanal (W1)**; M5/M15 são ruído e NÃO geram S/R. Força
  do nível por qualidade (toques, **rejeição**, respeito, recência, peso do TF). Só os
  melhores por par (`SR_MAX_POR_TIPO`).
- **S/R nunca INVALIDA** entrada de outra análise nem corta trade rodando. "Ativou uma
  estratégia, deixa o preço andar" → saída por força contrária só em **reversão (CHoCH)**,
  não em BOS de continuação nem por proximidade a nível.
- S/R serve para **confluência/pullback, reforço de order block, ponto de entrada**. A
  rejeição no nível (pavio ≥ 50% + fecha de volta) é CONFLUÊNCIA (soma no score); só é
  obrigatória se `EXIGIR_REJEICAO_SR=true` (default false).
- Gestão da posição roda a cada `GESTOR_POLL_S` (1s) com tick real (bid/ask). Não é
  evento-por-tick; para o M5 é adequado.

## O que já foi entregue (Fases 1–5 + ferramentas)
Fases 1–5 no ar (sombra); saída "com direito a desenvolver"; **dashboard analítico**
(ganho/perda, filtro de datas, por estratégia/motivo/par/regime/sessão, **MAE/MFE**,
**curva de capital + drawdown**); **guard de correlação por moeda**; S/R forte por
TF+qualidade; entrada por rejeição (confluência); **2ª estratégia `sweep_choch_v1`**
(liquidity sweep + CHoCH no M5 — função pura `estrategias.detectar_sweep_choch`, testada;
S/R como reforço, regime nunca gateia; params `SWEEP_*` em config, desligável por env).
Skill de conhecimento em `.claude/skills/trading-quant-expert/` (com referências e roadmap).

## PRÓXIMOS PASSOS (priorizados)
1. **Deixar a sombra rodar alguns dias** e auditar `/analitico` → especialmente
   **Por estratégia** e **Por regime** (o `lateral`/fade de S/R estava negativo).
2. ~~**Plugar a 2ª estratégia** (sweep de liquidez + CHoCH)~~ **✅ ENTREGUE** — `sweep_choch_v1`
   já roda na sombra em paralelo à `confluencia_v1`. Auditar no /analitico **Por estratégia**
   quando houver ≥30 trades dela. Nota p/ calibrar depois: o SL ainda é ATR (3×) genérico do
   executor; a reversão pós-sweep pede stop estrutural (atrás do pavio) — item 6 do roadmap +
   MAE/MFE por estratégia darão o número. Enquanto sombra, ATR basta para observar.
3. **Order block** com S/R/FVG como reforço (usar `meta` de qualidade dos níveis).
4. **Pullback em tendência**: entrar a favor do H1 quando o preço recua a S/R/OB forte e
   rejeita (reutilizar `estrategias.candle_rejeicao`).
5. Só depois de ≥30 trades/estratégia com expectância positiva na sombra: avaliar
   `EXECUCAO_ATIVA=true` em DEMO por 30 dias (nunca real antes disso).

Consultar SEMPRE a skill `trading-quant-expert` ao mexer em estratégia/risco/execução.
