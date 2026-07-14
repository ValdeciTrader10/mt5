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
  SETE estratégias em paralelo: `confluencia_v1` (Confluência), `sweep_choch_v1` (Caça-stops
  + reversão), `order_block_v1` (Order block), `pullback_tendencia_v1` (Pullback na tendência),
  `fecha_gap_v1` (Fechamento de gap), `pullback_rompimento_v1` (Pullback ao rompimento — reteste
  com inversão de polaridade: nível rompido por BOS vira suporte/resistência e rejeita) e
  `rompimento_extremos_v1` (Rompimento máx/mín do dia — PDH/PDL + reteste). **A/B da caça-stops:**
  `sweep_choch_abs_v1` é a GÊMEA da `sweep_choch_v1` — mesma detecção e mesmos gates, mas só entra
  se a vela da varredura mostrar ABSORÇÃO (volume alto + corpo fraco = esforço sem resultado, a
  leitura Wyckoff de que o smart money absorveu a liquidez; usa `fuzzy_score.flags_no_indice` no
  candle `i_sweep`, sem look-ahead). Livro de sombra INDEPENDENTE → a expectância das duas responde
  se a caça-stops rende mais COM ou SEM o filtro de absorção (a `sweep_choch_v1` fica intocada como
  controle). Env `SWEEP_ABS_HABILITADA`/`SWEEP_ABS_JANELA`. Cada uma grava sua
  própria linha em `decisoes`; o executor deduplica no nível de posição. Todas desligáveis por
  env (`*_HABILITADA`). **Multi-TF:**
  avalia por (par, **TF de operação**) para cada TF em `config.TFS_OPERACAO` (default
  `M1,M5,M15`) — cada TF é um LIVRO de sombra INDEPENDENTE (vela/ATR/janela do próprio TF;
  S/R/regime são contexto par-level). A decisão é marcada com `tf`. ⚠️ M1 é observação de
  sombra p/ comparar (no M1 o spread come o alvo — skill §0.1), nunca candidato a real.
- **executor** (`executor.py`): Fase 5 — abre/gerencia posições. Comportamento é POR POSIÇÃO
  (`p["real"]`), não global: gestão/SL-emulado/fechamento/reconciliação decidem por posição, então
  livros virtual e real coexistem. **3 modos:** (a) SIMULAÇÃO pura (default) — só sombra; (b)
  `EXECUCAO_ATIVA=true` — tudo real; (c) **PARALELO CURADO** (`EXECUCAO_REAL_CURADA=true`, só em
  DEMO) — a sombra cataloga TUDO (virtual) E um livro REAL dispara um GÊMEO só das combinações
  positivas (`EXEC_REAL_ESTRATEGIAS`=confluencia_v1,fecha_gap_v1 × `EXEC_REAL_TFS`=M5,M15; teto
  `MAX_POS_REAL`). Cada ordem real grava a comparação com a sombra: `preco_sinal` (assumido),
  `spread_entrada`, `derrapagem_pips` (fill real vs assumido) e `delay_s` (decisão→fill, via
  `decisoes.criada_utc`). `mt5_bridge.preco_fill` lê o price_open real; DD diário trava só o livro
  real; correlação só no real e se ligada. **Painel:** o `/analitico` separa o estudo (livro SOMBRA,
  simulado=1) da validação (livro REAL) e tem a aba **"Sombra vs Real"** — por (estratégia×TF) com
  par real, mostra exp. sombra vs exp. real, o **Δ exp.** (quanto o custo real comeu o edge) e as
  médias de derrapagem/spread/delay (`_sombra_vs_real`/`_exec_custo`). Enquanto não há trade real,
  sombra==todos (sem mudança). SL usa o
  ATR do TF que operou; trade marcado com `tf`. **Modo CATÁLOGO (sombra):** dedup por
  `(par, tf, ESTRATÉGIA, livro)` → cada estratégia roda a SUA posição virtual ao vivo em paralelo,
  gerida tick a tick; **sem trava de correlação** e **sem cap por livro** (só o teto amplo
  `MAX_POS_SOMBRA`), e o DD virtual **não trunca** o catálogo. As travas de risco por livro de
  TF (`MAX_POS_POR_PAR`/`MAX_POS_TOTAL`) e a correlação (`GUARDA_CORRELACAO`, **off** por
  pedido do dono) valem só no **modo real**. `pode_abrir` é função pura testada. Tick cacheado
  por ciclo (aguenta dezenas de posições sem martelar a ponte).
- **coletor**: agora coleta **M1** também (cap de backfill via `BACKFILL_M1_BARRAS`, default
  3000) e o loop dispara pelo TF de operação MAIS FINO (M1 chega ao banco a cada minuto).
- **Gráfico interativo** (`web/templates/grafico.html` + `/api/candles/{par}/{tf}`): substitui o
  Plotly estático no `/grafico/{par}/{tf}` (o antigo `grafico.grafico_html` ficou legado; o raio-X
  do trade segue em Plotly). Usa **TradingView lightweight-charts** (CDN, v4.1.7): candles com
  zoom/scroll/arrastar, crosshair OHLC, tela cheia (⛶), enquadrar (⤢), troca de par/TF sem recarregar
  e auto-refresh 5s. Linhas de S/R do motor como price lines. `time` = time_utc (hora do servidor).
  Precisão do eixo de preço por instrumento (5 casas forex, 3 JPY, 2 ouro — `casas()`).
  O dashboard embute via iframe (`allow="fullscreen"`) + "Abrir em tela cheia".
- **web** (`web/app.py`): painel + `/analitico` + **`/trade/{id}` ("Raio-X do trade")** +
  **`/auditoria` ("Auditoria IA")**. Caddy
  NÃO é usado no Dokploy (o Traefik dele faz proxy). Compose do Dokploy:
  `deploy/docker-compose.dokploy.yml`.
- **Raio-X do trade** (`grafico.grafico_trade_html`, rota `/trade/{id}`, link 🔍 na tabela do
  /analitico): gráfico sob demanda com o contexto antes/depois de cada trade (entrada/SL/saída,
  zona da vida do trade, níveis S/R+FVG do motor) + os fatos (pips/USD/R/MAE/MFE/regime/motivo),
  o "por que entrou" (score/confluências da decisão de origem — casada DIRETO pela FK
  `trades.decisao_id`, gravada na abertura; heurística por tempo só p/ trades antigos) e uma "Leitura" automática por
  MAE/MFE. Reconstruído do banco a cada acesso (o "futuro" se preenche conforme chegam candles),
  sem salvar PNG. Params `GRAFICO_TRADE_BARRAS_ANTES/DEPOIS`. Testes em `test_grafico.py`.
- **Auditoria IA** (`auditoria.py`, rotas `/auditoria` + `/api/auditoria`, aba "Auditoria IA"):
  resolve o pedido do dono de "uma forma de a IA auditar as perdedoras". O banco vive na VPS e o
  assistente não o acessa direto, então esta página **exporta** um DOSSIÊ compacto e auto-explicado
  das operações PERDEDORAS, já **classificadas por padrão de falha** via MAE/MFE (`classificar_perda`):
  `alvo_curto` (andou ≥1R e virou → calibrar SAÍDA), `devolveu_parcial` (0.5–1R), `entrada_adiantada`
  (foi contra de imediato → calibrar GATILHO), `perda_ordenada` (sem sinal de conserto → pesa p/
  RETIRAR) e `sem_dados`. Agrega por (estratégia × TF) com um **veredito** MANTÉM/CALIBRA SAÍDA/
  CALIBRA ENTRADA/RETIRA (`_veredito`), além de por regime/sessão/par/motivo. O botão **"Copiar
  dossiê para a IA"** gera um bloco Markdown (`dossie_texto`) que o dono cola no chat — é a ponte
  para eu revisar o que manter/mudar/retirar. Também há `python -m sistema_forex.auditoria [de] [ate]
  [--json]` (CLI) e `/api/auditoria?formato=texto`. Funções puras testadas em `test_auditoria.py`.
  **Raio-X TEXTUAL (a "visão do gráfico" p/ a IA):** além dos números, o dossiê embute, para as
  `AUDITORIA_RAIOX_TRADES` perdedoras mais recentes, os candles da janela antes/durante/depois em
  **pips relativos à entrada** (`raiox_dados`/`raiox_texto`, reaproveitando `grafico._janela_trade`
  e `analise.niveis_ativos` — mesma história do gráfico visual). Recomputa dos próprios candles os
  fatos que decidem a análise: **furou o SL e por quantos pips** (stop no ruído?), quanto andou a
  favor antes de virar (MFE), o pior contra (MAE) e **o que o preço fez DEPOIS da saída** (muito a
  favor = stop apertado/saída cedo; muito contra = entrada/estratégia errada), além dos níveis do
  motor perto da entrada. Pip exato via back-out `|saída−entrada|/|pips|` (respeita JPY/ouro). Assim
  a IA LÊ o price action e conclui sobre stop real / confirmação do padrão / ponto de entrada — não
  só os agregados. Qualquer trade sob demanda: `/api/raiox/{id}?formato=texto` (link 📄 na tabela) ou
  `python -m sistema_forex.auditoria raiox <id>`. Params `AUDITORIA_RAIOX_ANTES/DEPOIS/TRADES`.
  **Simulação "saída por invalidação"** (`simular_saida_invalidacao`/`resumo_invalidacao`, seção no
  dossiê): responde EMPIRICAMENTE "cortar o perdedor num padrão de reversão forte reduz o prejuízo?".
  Replay SEM look-ahead sobre as perdedoras — se um CHoCH OPOSTO (mesma detecção do motor, no TF do
  próprio trade = sinal mais rápido) confirmasse ANTES do stop (evento no swing i só conhecido em
  i+n), a que R sairia e quanto salvaria vs -1R. Agrega: `sem_sinal` (vira rápido, stop chega antes →
  cortar não ajuda, o problema é a ENTRADA), `com_sinal`, `salvaria`, `usd_salvo_total`. Descobre se
  vale mexer na saída ANTES de mexer. Param `AUDITORIA_INVALIDACAO_TRADES`.
- Banco: `sistema_forex/db.py` (SQLite WAL, migrações idempotentes em `_migrar`).
- **Reset de dados** (`manutencao.py`): `python -m sistema_forex.manutencao [status|reset]`. `reset`
  faz TUDO em ordem: (1) FECHA as posições do robô no broker (`fechar_posicoes_robo`, magic, p/ não
  ficarem órfãs); (2) BACKUP consistente (API de backup do SQLite); (3) apaga `trades`/`decisoes` +
  derivadas (`niveis`/`estrutura`/`regime_log`, que o motor regenera). **Preserva `candles`** (mercado).
  Depois: **redeploy** no Dokploy (executor reinicia sem estado velho). Usado em 13/07 p/ zerar os
  dados pré-fix-de-fuso e recomeçar limpo. Testes em `test_manutencao.py`. **Botão no painel:** a
  aba **Auditoria IA** tem "🧹 Zerar dados" → `POST /manutencao/reset` (guardado por login +
  confirmação digitada "LIMPAR" + `confirm()` JS); chama a mesma lógica (fecha posições + backup +
  limpa) e mostra o resultado + lembrete de redeploy.
- **Janela de negociação** = HORA DO SERVIDOR, `SESSAO_UTC=(4,21)` (env `SESSAO_INICIO`/`SESSAO_FIM`),
  alargada de (7,20) p/ 04:00–21:00 a pedido do dono (mais operações/horários p/ auditar). O nome
  `SESSAO_UTC` é legado; o valor é hora de servidor (o filtro usa a hora do candle=servidor).

**3 bugs da imagem MT5 já corrigidos** (ver `deploy/mt5/`): (1) `mt5linux 1.0.3` sem
`-w` → fixado `mt5linux==0.1.9`; (2) RPyC do Wine é **5.2.3** → cliente fixado em
`rpyc==5.2.3` (Python 3.11); (3) numpy 2.x quebrava o MetaTrader5 → `forex-start.sh`
força `numpy<2` no Wine. Detalhes em `deploy/DOKPLOY.md`.

## Gestão de saída POR VARIANTE — ligada (14/07, motivada pela 1ª auditoria de dados reais)
A 1ª auditoria da sombra (dossiê colado pelo dono) mostrou o vazamento nº 1: **100% das 156 perdedoras
saíram no STOP CHEIO (-1R)** (MFE médio das perdedoras só 0,3R) e a **simulação de invalidação** estimou
que uma saída estrutural antecipada salvaria ~2/3 delas (~0,88R cada). As saídas próprias de B/C já
existiam PURAS e testadas (Etapas 5/6) mas **não estavam plugadas** — a sombra catalogava tudo pela saída
genérica. Agora o `executor.gerir` chama `estrategias.gestao_saida_variante` **só para as posições virtuais
B/C** (a Variante A CONTROLE nunca passa por lá — segue no gestor genérico): **C_HIBRIDA** = saída
antecipada quando o M5 fuzzy vira contra (integração 5) + aperto de stop na exaustão (integração 6);
**B_FUZZY_PURO** = saída técnica na VWAP oposta. Contexto fuzzy/VWAP lido por par e **cacheado por ciclo**
(`_ctx_variante`, não martela o banco). ADITIVO e shadow-only: o relatório A vs C passa a MEDIR se a saída
inteligente melhora a expectância (antes A e C só diferiam na ENTRADA; agora C tem a saída desenhada).
Env `GESTAO_POR_VARIANTE` (default on), reusa `HIBRIDA_SAIDA_M5_MIN`/`HIBRIDA_STOP_APERTO`. Funções puras
+ wiring testados (`test_estrategias`: `gestao_saida_variante` C antecipada/exaustão, B técnica, A no-op).
⚠️ Aperto de stop da exaustão é in-memory (não persiste em `sl_servidor`); some num restart do executor
(aceitável na sombra). Próximo passo de auditoria: comparar exp. de C (com saída nova) vs A no /relatorio.

## Como rodar / testar / publicar
- Testes (sem pytest): `python -m sistema_forex.tests.test_gestao` (idem `test_estrategias`,
  `test_indicadores`, `test_multitf`, `test_grafico`, `test_auditoria`, `test_manutencao`,
  `test_fuzzy`, `test_relatorio`, `test_auditoria_estatistica`). **175 testes, todos passando.**
  Rodar sempre antes de commitar.
- Compilar: `python -m py_compile sistema_forex/*.py sistema_forex/web/*.py`.
- Publicar = commit + `git push -u origin <branch>` → Dokploy redeploya sozinho.
- Env sensíveis (senha do painel, VNC, MT5) só no Environment do Dokploy — nunca no git.

## Pares monitorados (sombra) — 13/07
`config.PARES` (env-configurável no Dokploy): `EURUSD#, GBPUSD#, USDCAD, USDJPY#, AUDUSD#, GBPJPY#, GOLD`.
- **GOLD** (ouro) adicionado a pedido do dono ("paga mais, maior risco, catalogar"). O ouro tem
  escala MUITO diferente do forex: pip≈0.01, move dólares por vela, spread ~20–50 pontos. Sem
  cuidado, o SL global (12–40 pips = só ~$0.40) insta-estoparia todo trade e o filtro de spread
  (2.0) barraria quase tudo. Por isso há **parâmetros por símbolo** (`config.PARAMS_SIMBOLO` +
  `param_simbolo()`): GOLD usa `sl_min_pips=800`, `sl_max_pips=6000` (~$8–$60) e `spread_max_pips=6.0`.
  ⚠️ LIÇÃO (13/07): o 1º cap do ouro (`sl_max=800`=$8) era MENOR que uma vela de ouro ($10–$40) →
  100% dos trades insta-estopavam (-1R). Regra: no ouro o SL tem de deixar o ATR×3 mandar (velas
  gigantes), teto largo. Auditar via /auditoria; ⚠️ o raio-X do ouro pode vir inconsistente se a
  coleta do ouro (recém-adicionado) ainda tiver pouco histórico — conferir contagem/timestamps.
  Threading: `decisao.avaliar_par` usa o spread por símbolo; `executor._abrir` usa os limites de SL
  por símbolo. Nome do símbolo resolvido por `ALIASES_SIMBOLO` (GOLD→tenta GOLD/GOLD#/XAUUSD/XAUUSD#);
  `coletor.resolver_simbolos` agora **pula** (com aviso) um símbolo que o broker não tem, sem derrubar
  o coletor. ⚠️ Se o ouro não aparecer no painel, conferir o nome real no terminal (VNC) e ajustar
  `ALIASES_SIMBOLO`/`PARES`. Ressalva: `spread_max_pips` está na régua interna pontos/10, então a
  coluna de spread do ouro no /analitico não é comparável 1:1 com a do forex (calibrar depois).
- Majors de spread razoável adicionados: **USDJPY#** e **AUDUSD#** (líquidos, spread baixo; usam os
  params globais — pip do JPY já sai certo em `tamanho_pip`). USDCHF/NZDUSD são opções extras.
- **GBPJPY#** adicionado a pedido do dono: cruzado VOLÁTIL e de spread mais largo (~25–40 pontos),
  então o cap global 2.0 quase não deixaria entrar — recebeu params próprios (`spread_max_pips=4.5`,
  `sl_max_pips=60`; `sl_min` no default). É o mais arriscado depois do ouro — catalogar e vigiar.
- ⚠️ Correlação: quase todos compartilham USD (e o ouro é anti-USD). Irrelevante na sombra (catálogo),
  mas `gestao._moedas("GOLD")` não sabe parsear metal — tratar antes de religar `GUARDA_CORRELACAO`
  para real.

## Fuso horário — trades carimbados na HORA DO SERVIDOR (MetaTrader) ✅ (13/07)
Era um bug: `candles.time_utc` = hora do SERVIDOR XM (UTC+3, cru de `r["time"]` do MT5), mas
`trades.abertura_utc`/`fechamento_utc` usavam `time.time()` (UTC) → `grafico._janela_trade`
desalinhava a janela ~3h (raio-X e simulação de invalidação com candles que não batem com a entrada).
**Correção (opção B, pedido do dono "horário do MetaTrader"):** o executor mede o offset servidor↔UTC
de um tick (`_atualizar_offset`, arredonda à hora; atualizado a cada `_tick` e no `carregar`) e
`_agora()` passa a devolver a HORA DO SERVIDOR → `abertura_utc`/`fechamento_utc` alinham com os candles.
Assim o filtro de sessão do `decisao` (hora do candle=servidor) e o `_sessao` do /analitico
(hora do abertura_utc=agora servidor) ficam CONSISTENTES, e o display mostra a hora do MetaTrader.
`decisoes.criada_utc` fica em UTC de propósito (só serve p/ `delay_s`, medido contra `time.time()`,
não contra `_agora()`). ⚠️ Trades ANTIGOS (pré-fix) têm `abertura_utc` em UTC → o raio-X deles ainda
desalinha; a guarda `janela_suspeita` (`simular_saida_invalidacao` descarta quando a vela de entrada
está >0.5R do preço de entrada) cobre isso. `mfe_r`/`mae_r` gravados TICK A TICK sempre foram a fonte
confiável. **DD diário e PDH/PDL também no relógio do servidor:** `_checar_dia`/`_equity` usam
`_agora()` (meia-noite do servidor, `_agora()%86400`) em vez de `datetime.now(UTC)` — consistente com
`fechamento_utc` (server); `_extremos_dia` já usava a hora do candle (server). Assim o sistema inteiro
opera no relógio do MetaTrader. (`decisoes.criada_utc` segue em UTC só p/ o `delay_s`.)

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
- **Confluência de S/R** (13/07): zonas onde topos/fundos de TFs diferentes se alinham (níveis do
  mesmo tipo dentro de `SR_CONFLUENCIA_ATR×ATR`) ganham força (`_marcar_confluencia` no motor;
  `+SR_CONFLUENCIA_BONUS×força` por vizinho; `meta.confluencia`). São os S/R que o preço mais
  respeita → as estratégias (pontuam pela força + rejeição) priorizam essas regiões. Soft/desligável
  (bônus 0). ⚠️ Validar na sombra que melhora a expectância — não é conclusão de um gráfico só.
- S/R serve para **confluência/pullback, reforço de order block, ponto de entrada**. A
  rejeição no nível (pavio ≥ 50% + fecha de volta) é CONFLUÊNCIA (soma no score); só é
  obrigatória se `EXIGIR_REJEICAO_SR=true` (default false).
- Gestão da posição roda a cada `GESTOR_POLL_S` (1s) com tick real (bid/ask). Não é
  evento-por-tick; para o M5 é adequado.

## O que já foi entregue (Fases 1–5 + ferramentas)
Fases 1–5 no ar (sombra); saída "com direito a desenvolver"; **dashboard analítico**
(ganho/perda, filtro de datas, por estratégia/**timeframe**/motivo/par/regime/sessão,
**MAE/MFE**, **curva de capital + drawdown**, **cruzamento Estratégia × timeframe** — responde
"qual estratégia rende melhor em qual TF", objetivo da sombra — e **motivo de saída normalizado**
para não fragmentar por r/direção); **operações de sombra independentes por TF (M1/M5/M15)** com
comparação "Por timeframe" no /analitico; **modo catálogo** (cada estratégia simula ao vivo sua
própria operação, várias simultâneas, sem trava de correlação na sombra); **guard de correlação por
moeda** (código mantido, `GUARDA_CORRELACAO` off — só religa p/ real); S/R forte por TF+qualidade;
entrada por rejeição (confluência); **7 estratégias na sombra**: `confluencia_v1`;
**`sweep_choch_v1`** (liquidity sweep + CHoCH no M5, `detectar_sweep_choch`); **`order_block_v1`**
(reteste de OB fresco M15/H1 + rejeição — detecção `indicadores.order_blocks` persistida como nível
`ob_bull`/`ob_bear`); **`pullback_tendencia_v1`** (a favor do H1: recua a S/R forte e rejeita);
**`fecha_gap_v1`** (fade do gap de sessão rumo ao fechamento anterior — momentum p/ o fill + espaço,
usa os níveis `gap_*` do motor); **`pullback_rompimento_v1`** (break-and-retest: nível rompido por
BOS vira polaridade invertida e rejeita); **`rompimento_extremos_v1`** (rompimento da máx/mín do dia
anterior/PDH-PDL + reteste com rejeição — `_extremos_dia` no D1). Todas: S/R/OB/regime como reforço,
nunca veto; rejeição é o gatilho nas de reversão/reteste; funções puras testadas; params por env.
Skill em `.claude/skills/trading-quant-expert/` (referências+roadmap).

## PRÓXIMOS PASSOS (priorizados)
1. **Deixar a sombra rodar alguns dias** e auditar `/analitico` → especialmente
   **Por estratégia**, **Por timeframe** (M1 vs M5 vs M15 — espera-se M1 pior pelo custo) e
   **Por regime** (o `lateral`/fade de S/R estava negativo).
2. ~~**Plugar a 2ª estratégia** (sweep de liquidez + CHoCH)~~ **✅ ENTREGUE** — `sweep_choch_v1`
   já roda na sombra em paralelo à `confluencia_v1`. Auditar no /analitico **Por estratégia**
   quando houver ≥30 trades dela. Nota p/ calibrar depois: o SL ainda é ATR (3×) genérico do
   executor; a reversão pós-sweep pede stop estrutural (atrás do pavio) — item 6 do roadmap +
   MAE/MFE por estratégia darão o número. Enquanto sombra, ATR basta para observar.
3. ~~**Order block** com S/R/FVG como reforço~~ **✅ ENTREGUE** — `order_block_v1` (detecção
   exige displacement/FVG, só M15/H1, zona fresca não mitigada; entra no reteste + rejeição).
4. ~~**Pullback em tendência** (a favor do H1, recua a S/R/OB e rejeita)~~ **✅ ENTREGUE** —
   `pullback_tendencia_v1` (rejeição é o gatilho obrigatório; OB coincidente é reforço).
4b. ~~**Codar as 3 estratégias que faltavam** para catalogar TUDO na sombra~~ **✅ ENTREGUE** —
   `fecha_gap_v1`, `pullback_rompimento_v1` e `rompimento_extremos_v1` já rodam em paralelo às
   outras 4 (total 7). Agora são 7 livros por TF (M1/M5/M15). Auditar cada uma no /analitico
   quando houver ≥30 trades. **Nota:** a nova aba **Estratégia × timeframe** é a ferramenta para
   escolher a melhor combinação (estratégia, TF) — era o pedido do dono.
5. Auditar as 7 estratégias no /analitico **Por estratégia** e **Estratégia × timeframe** conforme
   a sombra roda. Nota de calibração (comum a todas): o SL ainda é ATR (3×) genérico; OB, pullback,
   reteste e gap pedem stop estrutural (atrás da zona/pavio/nível) — item 6 do roadmap, guiado por
   MAE/MFE por estratégia.
6. Só depois de ≥30 trades/estratégia com expectância positiva na sombra: avaliar
   `EXECUCAO_ATIVA=true` em DEMO por 30 dias (nunca real antes disso). Provável que várias das 7
   fiquem negativas — a sombra existe justamente para cortar as ruins e manter as boas por TF.

Consultar SEMPRE a skill `trading-quant-expert` ao mexer em estratégia/risco/execução.

---

# ROADMAP MESTRE — LABORATÓRIO SOMBRA MULTI-VARIANTE (a partir de 13/07)

> Doc-fonte: `CONTEXTO_MESTRE_TRADING.md` (enviado pelo dono, consolida e SUBSTITUI os contextos
> antigos). Ele expande o sistema atual (que é essencialmente a **Variante A**) para um **laboratório
> de 3 variantes rodando em sombra ao mesmo tempo**: **A_ORIGINAL** (as estratégias como já estão —
> grupo de controle, não recriar), **B_FUZZY_PURO** (Fuzzy Wyckoff fiel à didática) e **C_HIBRIDA**
> (as estratégias + 7 integrações fuzzy). Objetivo: após 4–8 semanas de coleta, a auditoria estatística
> diz qual (variante × estratégia × par × TF × mercado) tem edge real e vai p/ demo. Inclui ainda
> **fuzzy_score**, **VWAP**, **Sync Line micro/macro**, **EV score**, **candles pintados** e um
> **módulo B3/WIN** (fase posterior). Metodologia: sombra antes de demo; nunca calibrar e validar no
> mesmo período; sem look-ahead. **Consultar a skill `trading-quant-expert` em TODA etapa de estratégia/risco.**

## PRINCÍPIO GOVERNANTE (definido pelo dono) — TUDO É ADITIVO, NADA É ALTERADO
Toda estratégia/variante nova é **acrescentada ao lado**, nunca reescreve as existentes. As 7
estratégias atuais (Variante A) são **grupo de controle intocável**. Cada combinação
`(variante × estratégia × par × TF)` é um **livro de sombra independente** rodando sobre o preço
real ao vivo. Até a Variante C (que LÊ o fuzzy para filtrar/ajustar as mesmas 9 estratégias) é uma
**cópia paralela** marcada `C_HIBRIDA` — a lógica interna da estratégia original NÃO é tocada.
Objetivo: catalogar o MÁXIMO de estratégias testadas em mercado real e, só ao fim, a auditoria
estatística decide o que vale ligar em demo/real. Nunca remover/alterar um livro para criar outro.

## PROTOCOLO DE EXECUÇÃO POR ETAPA (para o dono limpar o contexto entre passos)
O dono coda **uma etapa por vez** ("coda a ETAPA N"). Ao terminar CADA etapa, ANTES/junto do deploy:
1. rodar os testes + `py_compile`; 2. commit + push (Dokploy redeploya); 3. **atualizar ESTE roadmap**:
marcar a etapa `✅ FEITO` com 1 linha do que entrou (arquivos/tabelas/env) e o que ficou pendente;
4. relatar e liberar o dono p/ **limpar a conversa**. Assim cada sessão é curta e a memória carrega o estado.
Status: `⬜ pendente` · `🔧 em andamento` · `✅ feito`.

## AUDITORIA (ETAPA 0) — ✅ FEITO (13/07)
**JÁ PRONTO (= Variante A, forex, no ar):** coletor M1/M5/M15/H1/D1/W1 + spread; motor (ATR, ADX,
swings, estrutura SMC BOS/CHoCH, S/R por qualidade, FVG, order blocks, gaps, regime, máx/mín do DIA);
**7 estratégias** em sombra (`confluencia_v1`, `sweep_choch_v1`, `order_block_v1`, `pullback_tendencia_v1`,
`fecha_gap_v1`, `pullback_rompimento_v1`, `rompimento_extremos_v1`) catalogadas por TF (M1/M5/M15);
simulador de resultado tick-a-tick com MAE/MFE; executor (sombra + real curado paralelo demo);
/analitico + /auditoria + raio-X + simulação de invalidação; gráfico interativo (lightweight-charts).
**FALTA vs doc-mestre** (vira o roadmap abaixo): dimensão `variante`; pivots diários; EMAs/SMA;
máx/mín asiática/semana/**mês**; VWAP+bandas; **fuzzy_score**; **sync line**; **EV score**; painel de
scores + candles pintados; **Variante B**; **Variante C**; relatório multi-variante (split-half,
vw_performance); **módulo B3/WIN**. As "9 estratégias" do doc ≠ as 7 atuais: mapeiam ~7 (sr_m15≈
confluencia, smc_estrutura≈sweep_choch, order_block, fecha_gap, pullback_rompimento, max_min_m15≈
rompimento_extremos, tendencia≈pullback_tendencia); as 2 que faltavam (`pullback_medias` EMAs e
`pivot_confluencia` pivots) foram entregues na ETAPA 2 — matriz completa de 9 estratégias.

## ETAPAS (codar na ordem; cada uma = 1 pedido do dono)

**✅ ETAPA 1 — FEITO (13/07).** Fundação de dados do laboratório. (a) coluna `variante` em
`decisoes`/`trades` (default `A_ORIGINAL`, migração idempotente em `db._migrar`) → matriz agora é
(variante × estratégia × par × tf); a decisão carrega `variante` (`estrategias._decisao`), o
`decisao._gravar_decisao` a grava e o `executor._abrir_trade` a HERDA da decisão de origem. (b) novos
níveis no motor (`analise.niveis_periodo`, gravados em `niveis`): **pivots diários** PP/R1-3/S1-3
(`indicadores.pivots_classicos`, tipos `pivot_pp`/`pivot_r*`/`pivot_s*`) + **máx/mín** da sessão
ASIÁTICA (00–07 servidor, do M15), da SEMANA e do MÊS anteriores (dos D1; tipos `max/min_asia`,
`max/min_semana`, `max/min_mes`). Boundaries no relógio do servidor (último candle do par). (c)
**EMAs 9/20/45 + SMA50/200** puras em `indicadores.py` (`sma`/`ema`/`medias`). O `/api/candles` +
`grafico.html` (`estiloNivel`) desenham os novos níveis (pivot laranja, ásia roxo, semana azul, mês
ciano). Testes: `test_indicadores` (sma/ema/pivots), `test_multitf` (migração variante + niveis_periodo).

**✅ ETAPA 2 — FEITO (13/07).** Variante A completada p/ **9 estratégias** (sem tocar nas 7). Novas:
`pullback_medias_v1` (a favor da tendência, toque na EMA9/EMA20 do **TF acima** — `config.TF_ACIMA`;
FVG/OB coincidente DOBRA o score) e `pivot_confluencia_v1` (fade de pivot que está a <`PIVOT_SR_ATR`×ATR
de zona S/R/OB + rejeição; lateral = terreno natural). Ambas `variante=A_ORIGINAL`, desligáveis
(`MEDIAS_HABILITADA`/`PIVOT_HABILITADA`). Snapshot ganhou `pivots` (níveis `pivot_*`) e `medias_acima`
(EMAs do TF superior, `MEDIAS_JANELA=260`). Agora são **9 livros por TF** (M1/M5/M15). Funções puras
testadas em `test_estrategias` (8 novos casos). **106→120 testes, todos passando.**

**✅ ETAPA 3 — FEITO (13/07).** fuzzy_score.py + VWAP + tabelas base. (a) `indicadores.vwap_bandas`
(VWAP acumulada ponderada por volume + bandas ±kσ) e `analise.niveis_vwap` gravam os níveis
`vwap`/`vwap_sup1|inf1`/`vwap_sup2|inf2` do dia de SERVIDOR corrente (reset 00:00 servidor, TF
`VWAP_TF`=M5). (b) **`fuzzy_score.py`** (PURO+testado): `caracteristicas` (delta/range/vol/corpo/seq
normalizados pela referência recente, sem look-ahead) → `pontuar` (fuzzificação triangular + regras
SE-ENTÃO + defuzzificação por média ponderada) → score 0–100 + estado (lima/verde/branco/fúcsia/
vermelho) + flags **absorcao** (vol alto+corpo fraco), **exaustao** (clímax no fim de sequência longa
→ puxa o score p/ 50) e **transicao** (vela inverte sequência estabelecida). Cache por (par,tf,candle)
em `fuzzy_scores` via `atualizar_par` (janela deslizante, INSERT OR IGNORE). (c) **Sync Line** micro
(M1/M5)/macro (M15/H1) — `sync_line` verde/vermelho/amarelo + tabela `sync_line` (`atualizar_sync`).
(d) **EV score** (4 componentes: confluência+fuzzy+sync+localização VWAP) carimbado no `dados_json`
da decisão (`decisao._scores_ev`) — **NÃO bloqueia** (v1). Motor chama fuzzy/sync a cada ciclo
(`FUZZY_HABILITADO`); tabelas próprias não são apagadas pelo `_limpar_par` (cache preservado). Params:
`VWAP_*`, `FUZZY_*`, `SYNC_*_TFS`, `EV_HABILITADO`. Testes em `test_fuzzy.py` (rally→>76, absorção→flag,
exaustão→~50, VWAP, Sync, EV). **131 testes, todos passando.**

**✅ ETAPA 4 — FEITO (14/07).** Painel de scores no gráfico interativo. O `/api/candles/{par}/{tf}`
passou a devolver: **VWAP + bandas** (níveis `vwap`/`vwap_*` na régua de preço), a **cor de cada vela**
pelo estado fuzzy do TF do gráfico (`_fuzzy_por_candle` → color/borderColor/wickColor por candle),
as **séries de score** por TF (`_series_scores` → {tf:[{time,value}]}) e a **Sync Line** atual
(`_sync_atual`). O `grafico.html` desenha: linhas de score **M1/M5/M15/H1** num sub-painel próprio
(escala `scores`, margem inferior 20%, linhas de referência 24/50/76), VWAP dourada + bandas ±1σ/±2σ
(`estiloNivel`), **velas pintadas pelo estado fuzzy** (lima/verde/branco/fúcsia/vermelho, cor vinda do
backend) e a **Sync Line no rodapé** (chips micro/macro/combinado verde/vermelho/amarelo). Botão
**"Scores"** liga/desliga o sub-painel. Aceite (pendente): validação visual do dono em 3 dias distintos.

**✅ ETAPA 5 — FEITO (14/07).** Variante B (Fuzzy Puro) — grupo PARALELO/aditivo (nada da Variante A
foi tocado). `estrategias.avaliar_fuzzy_puro` (PURA) roda em SOMBRA marcada `variante=B_FUZZY_PURO`
(estratégia `fuzzy_puro_v1`), UMA vez por par no TF de **timing** (`FUZZY_B_TIMING_TF`=M1), com a
**pirâmide MTF estrita** lida do fuzzy: **M15=maré / M5=correnteza / M1=timing** (`_lado_fuzzy`). Usa
**desvio-padrão MANUAL dos 20 closes** (`indicadores.desvio_padrao`) para medir a FORÇA do candle-gatilho
(corpo ≥ `FUZZY_B_STD_K`×σ), a **VWAP+bandas** (localização de valor) e classifica o setup num **cenário
nomeado** (`classificar_cenario_fuzzy`): entra em **ESTOURO**/**PULLBACK_VWAP**, bloqueia em **EXAUSTÃO**/
**ABSORÇÃO DE TOPO** (todos logados). **Checklist de 6 itens** (maré/correnteza/timing/valor_vwap/força_std/
fluxo_limpo — compra/venda espelhados); entra com ≥`FUZZY_B_CHECKLIST_MIN` (5/6) + cenário de entrada +
gates (sessão/spread). Saída técnica **SMA50/VWAP oposta** = `saida_tecnica_fuzzy_puro` (PURA, pronta p/ o
executor plugar — a sombra hoje cataloga a saída pelo gestor genérico). `decisao.montar_snapshot` ganhou
`fuzzy` (pirâmide MTF) e `vwap`; `avaliar_par` chama a B só no timing TF. Params `FUZZY_B_*`. Testes em
`test_estrategias` (9 casos) + `test_indicadores` (desvio_padrão). **141 testes, todos passando.** Pendências
fiéis ao didático (encaixam na ETAPA 6/executor): a saída técnica e a "ordem-stop expira em 3 candles" são
detalhes de EXECUÇÃO ao vivo — a sombra cataloga a QUALIDADE DA ENTRADA (objetivo do laboratório) com a
saída genérica; a função de saída da B já está pronta e testada para plugar quando ligar a gestão por variante.

**✅ ETAPA 6 — FEITO (14/07).** Variante C (Híbrida) — grupo PARALELO/aditivo (nada da A/B foi tocado).
`estrategias.avaliar_hibrida` recebe CADA decisão "entrou" da Variante A e aplica a camada fuzzy como
LEITURA dos `fuzzy_scores`/VWAP (nada recalculado): (1) VETO de absorção contra no extremo da VWAP; (2)
M15 fuzzy — VETA se claramente contra a direção, soma se a favor; (3) virada de score (transição) na ZONA
(OB/S-R/pivot); (4) sweep validado por ESFORÇO (M5); (7) localização vs VWAP. Vetos = só as contradições
claras (não engessar). C só gera decisão quando a A entrou → o livro C é o subconjunto fuzzy-filtrado do A,
`variante=C_HIBRIDA`, diretamente comparável (A vs C). `decisao.avaliar_par` espelha as decisões da A;
`executor.pode_abrir` agora deduplica por (par, tf, estratégia, **VARIANTE**) → A e C são LIVROS separados
(a posição carrega `variante`; `MAX_POS_SOMBRA` 200→400). Saída da C (integrações 5 saída antecipada por M5
contra + 6 exaustão aperta stop) = funções PURAS `saida_antecipada_hibrida`/`ajuste_stop_exaustao` prontas
p/ o executor plugar (a sombra usa a saída genérica, igual à B). Params `HIBRIDA_*`. **Corrigido bug latente:**
o `main()` do `test_estrategias` ficava ANTES dos testes B/C → eles nunca rodavam; movido p/ o fim.
Testes em `test_estrategias` (9 casos C) + `test_multitf` (dedup por variante + espelho A→C).

**✅ ETAPA 7 — FEITO (14/07).** Relatório sombra multi-variante (`relatorio.py`, rotas `/relatorio` +
`/api/relatorio`, aba "Relatório", CLI `python -m sistema_forex.relatorio [de] [ate] [--json|semanal]`).
Tudo PURO/testável, lê o livro SOMBRA fechado (sem look-ahead): `ranking_celulas` (expectância em **R** por
CÉLULA variante×estratégia×TF×par, marcando N≥`RELATORIO_MIN_SINAIS`=30), `por_variante` (KPIs + equity/maxDD
A vs B vs C), `heatmap_estrategia_tf` (exp R por estratégia×TF dentro de cada variante), **`a_vs_c`** (casa as
decisões A↔C por (par,tf,estratégia,time_utc) + desfecho do trade A: dos setups que a C BLOQUEOU, quantos
eram perdedores = prejuízo EVITADO vs vencedores = lucro PERDIDO, e o benefício líquido em USD),
`distribuicao_bloqueio` (motivos dos vetos fuzzy) e **`split_half`** (exp R nas duas metades → edge estável ×
sorte). `resumo_semanal` envia o resumo curto ao Telegram (anti-spam). Template `relatorio.html` + nav em
todas as páginas. Testes em `test_relatorio.py` (8 casos). Aceite: 1º relatório auditável ✅.

**🔧 ETAPA 8 — Módulo B3/WIN — EM ANDAMENTO (retomada 14/07, com o MT5 da GENIAL).** O feed da B3 que
faltava agora vem de um **2º terminal MT5 na Genial** (conta REAL usada SÓ como fonte de cotações — sombra).

**✅ Sub-etapa 8a — FUNDAÇÃO DE DADOS (14/07):** WIN/WDO entrando no banco, ADITIVO (o forex XM não foi
tocado). Peças: (1) **`config_b3.py`** — conexão do 2º terminal (`MT5_B3_HOST`/`PORT`=mt5_b3:8001),
símbolos `PARES_B3` (default `WIN$N,WDO$N`) + `ALIASES_B3` (tenta WIN$, WINFUT…), `TFS_COLETA_B3`
(M1–D1), backfill/poll próprios, flag `B3_HABILITADO`, e `candidatos_simbolo` (pura, testada). (2)
**`mt5_bridge_b3.py`** — ponte para o terminal Genial com globais/lock PRÓPRIOS (não compartilha estado
com a ponte do forex) e **DATA-ONLY de propósito**: não existe abrir/fechar/mover_sl → impossível, por
construção, enviar ordem na conta real. Só connect/resolver/copy_rates/tick/ping. (3) **`coletor_b3.py`**
— gêmeo do `coletor_mt5`, usa a ponte B3 e reusa as funções puras `gravar_candles`/`contar`; grava na
MESMA tabela `candles` com `par`=símbolo B3 (não colide; o motor do forex itera `config.PARES` e não os
toca). (4) **Deploy:** serviços `mt5_b3` (VNC :3101, volume `mt5_b3_config`) e `coletor_b3` nos dois
composes; `.env.example` com a seção B3 (`MT5_B3_*`, `PARES_B3`). Testes em `test_b3.py` (6 casos:
candidatos, alvo/gatilho, coexistência WIN×forex no banco). **177 testes, todos passando.**
✅ **Ação do dono FEITA (14/07):** terminal Genial logado (`376363 GenialInvestimentos-PRD`, conta REAL só
p/ cotação) e os nomes REAIS confirmados na Market Watch = **exatamente `WIN$N` e `WDO$N`** (batem com o
`PARES_B3` padrão → nada a ajustar). VNC do `:3101` recusava a senha: o volume `/config` guardava uma senha
velha; corrigido renomeando o volume p/ `mt5_b3_config_v2` (disco novo re-inicializa com o `VNC_PASSWORD`
atual). O `coletor_b3` já roda → WIN/WDO começam a entrar no banco. Aceite 8a: WIN logando (contagem
crescendo, sem buracos) — conferir no banco/painel nos próximos ciclos.

**🔧 Sub-etapa 8b — PAINEL B3 SEPARADO + MOTOR na B3 (14/07, a pedido do dono "painel separado só p/ B3").**
Entregue o começo do 8b, ADITIVO e ISOLADO do forex: (1) **página própria `/b3`** (`web/templates/b3.html`
+ rota `/b3` e `/api/b3`, `_dados_b3` no app; link "🇧🇷 B3" no nav de todas as páginas) — mercado distinto,
P&L em BRL, por isso NÃO se mistura ao /analitico do forex. Mostra por símbolo: saúde da COLETA (candles por
TF), última cotação, e a análise do MOTOR (regime/ADX + contagem de S/R, FVG, OB, gaps, eventos). (2) **motor
ligado na B3**: `analise.um_ciclo` agora itera `config.PARES + config_b3.pares_ativos()` (helper novo, vazio se
`B3_HABILITADO=false`) → grava níveis/regime de WIN/WDO. É inócuo ao livro do forex porque o motor só grava
`niveis`/`regime_log`/`estrutura` e o executor NÃO age sobre isso. Testes: `test_b3.pares_ativos` + render do
painel verificado. **181 testes, todos passando.**
⚠️ **AINDA NÃO LIGADO (próximo passo do 8b):** estrategista + executor de SOMBRA da B3 (= os "resultados das
estratégias" que o dono quer). Bloqueios reais a resolver antes: (a) **calibração de escala** de WIN/WDO em
`PARAMS_SIMBOLO`/`tamanho_pip` (lição GOLD: stop < vela → 100% insta-stop; derivar do banco já coletado, não
chutar); (b) o executor usa a ponte do **forex** p/ tick/pip/lucro — a ponte B3 é **data-only** (sem
`calc_lucro`/`tamanho_pip`), então o shadow da B3 precisa de P&L PURO (valor-por-ponto, em BRL) e tick via
`mt5_bridge_b3` — fazer ISOLADO (novo caminho/serviço) p/ não tocar no executor do forex ao vivo. NÃO ligar
`decisao` na B3 antes disso (o executor pegaria as decisões e choraria na ponte errada). Painel já preparado:
`_dados_b3` mostra `estrategias_ligadas`/`n_trades` e troca o aviso automaticamente quando começarem a existir.
**Demais pendentes 8b+:** tabela `correlacao_b3`, painel MACRO, **veto de correlação SÓ no B3** (NUNCA no forex),
alerta de rollover. ⚠️ `gestao._moedas` não parseia metal/índice — tratar antes de qualquer correlação WIN/WDO/GOLD.

**✅ ETAPA 9 — FEITO (14/07).** Auditoria estatística — o GATE que decide, por dados, o que vai p/ demo.
`auditoria_estatistica.py` (PURO/testável, rotas via /relatorio + CLI `python -m sistema_forex.auditoria_estatistica
[de] [ate] [--json]`) lê o livro SOMBRA fechado e aplica, por CÉLULA (variante×estratégia×TF×par), os 4
critérios do doc-mestre + skill §5: **(1)** N ≥ `APROVACAO_MIN_SINAIS` (50), **(2)** exp R > 0, **(3)** PF ≥
`APROVACAO_PF_MIN` (1,3), **(4)** exp R positiva nas DUAS metades (`_split_half_celula`, guardião anti-sorte).
`avaliar_celula` (pura) devolve os critérios + veredito + **confiança** (alta só com N ≥ 2×mín E split estável;
senão média). **Armadilha de múltiplos testes** tratada explicitamente (skill §5, Deflated Sharpe): testamos
centenas de células → `falsos_esperados ≈ testadas × APROVACAO_PROB_ACASO` (0,05) e `multiple_testing_alerta`
quando as aprovadas não superam o acaso; o split-half obrigatório é o deflator prático. **Não liga NADA
sozinho** — `_config_sugerida` só EXPÕE as células aprovadas + o env sugerido (`EXEC_REAL_ESTRATEGIAS`/
`EXEC_REAL_TFS`) p/ o dono aplicar no Dokploy (só Variante A é promovível hoje; B/C aprovadas ficam listadas —
promover exige fiar o executor por variante, futuro). Aba **"🎯 Aprovação para demo"** no topo do /relatorio.
Env: `APROVACAO_MIN_SINAIS`/`APROVACAO_PF_MIN`/`APROVACAO_EXIGE_SPLIT_HALF`/`APROVACAO_PROB_ACASO`. Testes em
`test_auditoria_estatistica.py` (8 casos: cada critério reprova sozinho, split como deflator, múltiplos testes,
config sugerida). **163→171 testes, todos passando.** ⚠️ Só produz aprovações reais após 4–8 semanas de sombra
(N por célula). Pendência real (não-código): deixar a sombra rodar e reauditar; ligar em demo é decisão do dono.

**Regras que valem em todas as etapas:** nenhuma variante executa ordem real na sombra; chamadas MT5 sob
lock global; candle em formação nunca entra na análise; gravar spread do sinal; no forex NÃO coletar/usar
correlação (só B3); cache de scores por candle (CPU da VPS); nunca calibrar e validar no mesmo período.
