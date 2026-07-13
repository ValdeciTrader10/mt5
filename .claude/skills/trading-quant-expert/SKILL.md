---
name: trading-quant-expert
description: >
  Conhecimento expert de trader profissional + engenharia de sistemas quantitativos,
  específico para o sistema Forex M5 (sistema_forex/). USE SEMPRE ao mexer em
  estratégias (estrategias.py, decisao.py), risco/saída (gestao.py, executor.py),
  análise/indicadores (analise.py, indicadores.py), validação de resultados, métricas
  do painel (web/), ou ao decidir parâmetros de mercado (config.py). Traz princípios de
  edge, gestão de risco por R, gestão de trade (MAE/MFE, dar espaço), desenho de
  estratégia sem overfitting, validação estatística (modo sombra → walk-forward), SMC
  com rigor, e boas práticas de código de trading (viés look-ahead, timezone,
  determinismo, testes com trades conhecidos). Objetivo: cada mudança aumentar a
  expectância real, nunca a curva ajustada ao passado.
---

# Trader + Quant Dev — expert para o sistema Forex M5

Referência de decisão para enriquecer `sistema_forex/`. Regra-mãe: **toda alteração deve
aumentar a EXPECTÂNCIA fora da amostra, não embelezar a curva no passado.** Na dúvida entre
sofisticar e simplificar, simplifique — complexidade é dívida que o mercado cobra.

## 0. Princípios inegociáveis (herdados do MASMC + mercado)
1. **Custo mata edge fino.** No M5 o spread é fração grande do alvo. Sempre contabilize
   spread/comissão no P&L e no backtest. Monitorar spread médio por par (já no banco). Se
   `edge_bruto ≤ ~2× custo`, a estratégia é ruído. USDCAD aqui tem spread ~27 pts — trate
   com desconfiança extra.
2. **Sem look-ahead.** Nunca use dados do candle em formação nem informação futura para
   decidir. Decisão só em candle FECHADO. Indicador em `t` só vê `≤ t`.
3. **Demo/sombra primeiro.** Nada vira real sem amostra auditada (§5). `EXECUCAO_ATIVA=false`
   é o default sagrado.
4. **Pips por `price_open`/`price_current` do deal**, nunca diferença entre posições.
5. **Toda ordem com stop de servidor** (rede contra queda de VPS/net). Verificar margem antes
   de `order_send` (retcode 10019).
6. **Determinismo e idempotência.** Mesmo input → mesmo output. Reprocessar não duplica.

## 1. Gestão de risco e capital (o que separa amador de profissional)
- **Pense em R, não em pips/USD.** R = risco inicial da entrada (`|entrada − sl_inicial|`).
  Todo resultado em múltiplos de R. Já implementado em `gestao.r_por_risco` — use SEMPRE.
- **Expectância** = `winrate·médiaGanhoR − lossrate·médiaPerdaR`. É a única métrica que diz se
  a estratégia ganha dinheiro. Um sistema de 40% de acerto com 2,5R médio é excelente; um de
  70% com 0,3R médio (o bug dos centavos!) é perdedor após custo.
- **Sizing por risco fixo do capital**, não lote fixo, quando for escalar: `lote =
  (equity · risco%) / (dist_stop_pips · valor_pip)`. Na v1 lote fixo 0,01 é ok; ao validar,
  migrar para risco 0,25–0,5%/trade.
- **Drawdown manda.** Teto diário 5% (já em config). Pense também em DD máximo aceitável da
  série e em "recovery factor" = lucro_líquido / maxDD.
- **Correlação = risco escondido.** EURUSD, GBPUSD e USDCAD compartilham o USD. Duas compras
  de EUR e GBP ≈ uma posição dobrada short-USD. Limitar exposição líquida por moeda, não só
  nº de posições. (Enriquecimento pendente — ver §8.)
- **Kelly fracionário** como teto de agressividade, nunca cheio: use ¼ de Kelly no máximo.

## 2. Gestão de trade e SAÍDAS (onde o dinheiro é feito)
A entrada define o risco; a **saída define o retorno**. Erros clássicos e a postura correta:
- **Sair no ruído (o bug corrigido):** qualquer tick/BOS contrário fechando com centavos.
  Correto (já implementado em `gestao.avaliar_saida`): só proteger lucro já desenvolvido
  (`r ≥ SAIDA_ESTRUTURA_MIN_R`), ignorar estrutura do M5 (ruído), e **dar espaço** — se há
  distância até o próximo nível contrário e o sinal é fraco (BOS), segurar; CHOCH (reversão)
  encerra. Ajuste fino por `SAIDA_ESTRUTURA_*` em config.
- **MAE / MFE** (Maximum Adverse/Favorable Excursion): para CADA trade, o pior R contra
  (MAE) e o melhor R a favor (MFE) durante a vida da posição. É **a ferramenta** para calibrar
  stop e alvo com dados, não com achismo:
  - MAE dos vencedores → quanto de "calor" um trade bom aguenta → dimensiona o stop.
  - MFE dos perdedores/scratch → quanto o preço andou a favor antes de virar → revela alvos/
    trailing deixados na mesa (exatamente o "tinha espaço para desenvolver").
  Registrar MAE/MFE por trade e plotar distribuição é o próximo grande enriquecimento (§8).
- **Trailing por ESTRUTURA, não por distância fixa** (lição MASMC): stop atrás do último HL
  confirmado (compra) / LH (venda). Trailing colado = morte por ruído.
- **Break-even cedo demais também é ruim**: mover BE em ~1R é razoável; muito antes vira
  "morte por mil BEs". Prefira BE após confirmação estrutural, não só R.
- **Alvo x sem-alvo:** tendência sem alvo fixo (deixa correr, sai por estrutura); range e
  fecha-gap com alvo fixo. Nunca misture as duas filosofias na mesma estratégia.

## 3. Desenho de estratégia sem overfitting
- **Uma hipótese por estratégia**, com lógica de mercado ANTES do dado (por que o edge
  existe? liquidez, stop-hunt, retorno à média, momentum). Sem tese econômica → é mineração.
- **Poucos parâmetros e robustos.** Cada parâmetro extra é um grau de liberdade para ajustar
  ao passado. Prefira o que funciona numa FAIXA (plateau), não num pico. Teste sensibilidade:
  se ±20% no parâmetro desmonta o resultado, é overfit.
- **Filtro de regime é multiplicador de edge.** A mesma entrada em tendência vs. range tem
  sinais opostos. O `regime` (ADX/estrutura) já existe — condicione cada estratégia ao regime
  correto (doc §6.1). Registrar decisão + regime permite achar "estratégia × regime" vencedor.
- **Sessão importa.** Londres/NY (07–16 UTC) tem liquidez e movimento; Ásia é range. Filtrar
  entrada por sessão costuma melhorar expectância mais que refinar a entrada.

## 4. SMC com rigor (evitar "SMC de indicador")
- **BOS** = fechamento além do último swing NA direção da tendência (continuação). **CHoCH** =
  fechamento além do swing CONTRA a tendência (alerta de reversão). Primeiro CHoCH ≠ reversão:
  exigir CHoCH + novo swing confirmando (HL→LH). Para SAÍDA, tratar CHoCH como forte e BOS como
  fraco (já refletido na gestão).
- **Order Block válido** exige impulso com BOS + deixa FVG (displacement) + estar fresco (não
  mitigado). OB sem imbalance é candle qualquer. Detectar em M15/H1 (M5 gera ruído).
- **FVG** só vale com tamanho ≥ 0,3·ATR do TF; menor é ruído. Usar como (a) confirmação de
  displacement do OB e (b) zona de pullback a favor da tendência.
- **Liquidity sweep + CHoCH no M5** (rompe máx/mín, falha, fecha de volta) é a entrada de maior
  qualidade do sistema — priorizar na validação.
- **Operar M5 só na direção da estrutura do M15; H1 dá o viés.** Contra o H1 = pular ou meio
  risco. Essa hierarquia multi-TF é o filtro mais importante do sistema.

## 5. Validação — como saber se presta (sem se enganar)
- **Modo sombra → out-of-sample → walk-forward.** A sombra (Fase 4) gera decisões sem operar:
  audite-as antes de qualquer real. Depois, valide em janela NÃO usada para calibrar
  (out-of-sample) e, idealmente, walk-forward (recalibra em janela móvel, testa na seguinte).
- **Tamanho de amostra.** Conclusões por estratégia exigem **≥ 30 trades**, de preferência
  100+. Winrate de 3 trades não é winrate. Mostrar N junto de toda métrica (o painel já mostra).
- **Métricas que importam** (além de winrate, que engana):
  - **Profit factor** = ganho bruto / |perda bruta|. > 1,3 começa a interessar; < 1,0 perde.
  - **Expectância por trade** (em R e em USD) — a mais honesta.
  - **Sharpe/Sortino** dos retornos por trade (consistência; Sortino pune só o downside).
  - **Max drawdown** e **recovery factor**; **sequência máxima de perdas**.
  - **Distribuição de R** (histograma) e **MAE/MFE** — revelam stops/alvos mal postos.
  - **Custo total** (spread+comissão) como % do lucro bruto.
- **Monte Carlo / bootstrap** da sequência de trades → intervalo de DD e de resultado. Um
  sistema só é "aprovado" se sobrevive ao pior 5% das reordenações.
- **Cuidado com viéses:** look-ahead, survivorship (símbolos/dados só do que existiu),
  data-snooping (testar 50 variações e escolher a melhor = achar sorte). Corrigir p-valor por
  nº de tentativas (Bonferroni/DSR) se for garimpar.

## 6. Boas práticas de engenharia de sistemas de trading
- **Funções puras testáveis para a matemática** (indicadores, gestão) — como já está em
  `indicadores.py`/`gestao.py`. O I/O (MT5, banco) fica nas bordas. Testar com **trades/valores
  conhecidos** (ex.: R, ATR, pips com números à mão) antes de confiar.
- **Timezone é bug clássico.** Servidor XM é UTC+3; usar `tick.time` direto. Fronteiras de dia/
  sessão sempre explícitas em UTC. Pivôs no fechamento do D1 do servidor.
- **Idempotência**: `INSERT OR IGNORE` em candles; reprocessar análise não duplica níveis.
- **Concorrência**: SQLite em WAL; MT5 sob lock global (não é thread-safe). Uma ordem, um
  caminho.
- **Anti-spam de notificação** por flags booleanas (não por variável flutuante — bug MASMC).
- **Reset diário no topo do loop** (DD, contadores) — thread presa não pode pular o reset.
- **Nada de número mágico**: todo parâmetro em `config.py`, sobrescrevível por env (Dokploy).
- **Logue a decisão E a não-decisão com o motivo** — auditoria é o que transforma sombra em
  aprendizado.

## 7. Métricas que o painel/análise deve ter (norte do web/)
Já temos: winrate, PF, expectância, por estratégia/motivo/par, filtro de datas. Faltam, em
ordem de valor: **MAE/MFE por trade e distribuição**, **curva de capital (equity curve) e
drawdown**, **expectância por regime e por sessão**, **distribuição de R (histograma)**,
**tempo médio em posição** (ganhadores vs perdedores), **custo (spread) por estratégia**.

## 8. Roadmap de enriquecimento priorizado para ESTE repo
Aplicar incrementalmente, cada um com teste e sem quebrar o que roda:
1. **MAE/MFE por trade** — coletar no executor (tick-speed) `mae_r`/`mfe_r`, gravar em `trades`,
   e mostrar distribuição no `/analitico`. Destrava calibrar stop/alvo com dado. (Alto valor.)
2. **Curva de capital + drawdown** no `/analitico` (cumulativo por data).
3. **Expectância por REGIME e por SESSÃO** (cruzar `regime_log`/hora com resultado do trade) —
   revela "estratégia × contexto" que ganha; base para ligar/desligar estratégia por regime.
4. **Guarda de correlação/exposição por moeda** no executor (limitar net-USD, não só nº pos).
5. **Filtro de sessão e de spread na entrada** (não entrar com spread > limiar; priorizar
   Londres/NY) — costuma melhorar expectância mais que refinar sinal.
6. **Trailing por estrutura** (stop atrás do último HL/LH confirmado) além do giveback por R.
7. **Backtest/replay determinístico** sobre os candles do banco para pré-validar mudanças de
   estratégia antes da sombra ao vivo (walk-forward simples).
8. **Sizing por risco % do equity** ao migrar de lote fixo (quando aprovar em demo).

> Ao implementar qualquer item: hipótese clara → mudança cirúrgica → teste com números
> conhecidos → medir expectância fora da amostra → só então promover. Nunca ajustar parâmetro
> olhando o resultado passado e declarar vitória.

## 9. Referências (fontes reais, verificáveis)
Esta Skill foi escrita a partir do conhecimento consolidado abaixo + o contexto deste repo
(o doc de handoff e as lições do MASMC no `CLAUDE.md`). Não foi copiada de um repositório
externo; os princípios são o cânone público da área e podem ser conferidos nestas fontes:

- **Van Tharp — _Trade Your Way to Financial Freedom_.** Expectância medida em múltiplos de R
  e position sizing como o fator nº 1 de desempenho. (Base das §1 e §2.)
- **Robert Pardo — _The Evaluation and Optimization of Trading Strategies_ (Wiley).** Walk-forward
  analysis como padrão-ouro contra curve-fitting (in-sample × out-of-sample). (Base da §5.)
- **John Sweeney — _Maximum Adverse Excursion_ (Wiley, 1996).** MAE/MFE para calibrar stop e
  alvo com dados de excursão intra-trade, não com achismo. (Base do MAE/MFE em §2 e §8.)
- **David Aronson — _Evidence-Based Technical Analysis_ (Wiley).** Método científico + inferência
  estatística sobre sinais; alerta do viés de mineração de dados (data-mining bias). (Base da §3/§5.)
- **Bailey & López de Prado — _The Deflated Sharpe Ratio_ (2014).** Correção por seleção,
  overfitting de backtest e nº de tentativas; por que "achar a melhor de 50 variações" é sorte.
  (Base do cuidado com data-snooping em §5.)
- **Microestrutura de mercado / custo de execução** (spread, slippage, impacto) — literatura de
  market microstructure; aqui aplicada ao custo que corrói edge fino no M5 (§0.1).
- **SMC/ICT** (BOS, CHoCH, Order Block, FVG, liquidity sweep) — tradição de _Smart Money
  Concepts_; neste projeto seguimos as definições operacionais do doc de handoff (§4), com o
  rigor de exigir displacement/BOS para OB e confirmação para CHoCH.

> Observação honesta: os títulos/autores acima são obras conhecidas e públicas; use-os como
> ponto de partida para aprofundar. Onde uma decisão de parâmetro depender de evidência
> específica deste feed (XM/M5), a fonte de verdade é a nossa própria amostra em modo sombra —
> não a autoridade de um livro.
