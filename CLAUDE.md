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

---

# 🎯 PAINEL DE VALIDAÇÃO — O QUE AUDITAR (ler ISTO ao catalogar resultados)

> **Handoff do dono:** quando ele pedir "audita/cataloga os resultados", este é o mapa do que cada
> livro de sombra está TESTANDO e como julgar. **Regra-mãe (skill §5):** nada vira demo sem passar o
> GATE da Etapa 9 → **N ≥ 50 · exp R > 0 · PF ≥ 1,3 · exp R positiva nas DUAS metades (split-half)**.
> Winrate engana; a métrica honesta é **expectância em R**. Sempre olhar **N junto** (winrate de 5
> trades não é winrate). **Nunca calibrar e validar no mesmo período.** Ferramentas: `/relatorio` (aba
> "🎯 Aprovação para demo" roda o gate automático), `/analitico` (forex, USD), `/b3/analitico` (B3, BRL),
> `/auditoria` (dossiê das perdedoras por padrão de falha) e `python -m sistema_forex.auditoria_estatistica`.

**Como o laboratório está montado:** cada `(variante × estratégia × par × TF)` é um LIVRO de sombra
independente sobre o preço real ao vivo. As variantes são grupos COMPARÁVEIS — a leitura é sempre
"exp R de um livro vs. exp R do outro", isolando UMA coisa por comparação:

| Grupo | O que testa | Como julgar (além do gate N≥50/PF≥1,3/split) |
|---|---|---|
| **A_ORIGINAL** (controle) | as 9 estratégias "cruas" | é a linha de base — todo resto se compara contra A |
| **B_FUZZY_PURO** | Fuzzy Wyckoff fiel ao PDF (entrada) | exp R de B vs A por (estratégia×TF); `fuzzy_puro_v1` (maré 60) vs `fuzzy_puro_lima_v1` (maré 76 = mais seletiva) |
| **C_HIBRIDA** | A + camada fuzzy que VETA/soma (entrada) + saída fuzzy | aba **A vs C** no /relatorio: dos setups que a C bloqueou, quantos eram perdedores (prejuízo EVITADO) vs vencedores (lucro PERDIDO) → benefício líquido USD |
| **C_CORRE** | MESMAS entradas da C, mas SEM corte fuzzy ("deixa correr") | **C_CORRE vs C_HIBRIDA** isola SÓ a saída. Se C_CORRE > C_HIBRIDA → o corte fuzzy capa os vencedores cedo → aposentar a saída fuzzy |
| **D_LINHAS** | dinâmica das curvas de score (divergência/pullback/flip/exaustão) | 4 estratégias-puras; ver se alguma tem edge ISOLADO antes de cruzar com as originais |
| **E_SENTINELA** | força contínua micro/macro + leque (Sync Line do PDF) | 3 estratégias; validar por expectância como "5º dado" comparativo |
| **F_BREAKOUT** | rompimento da abertura de Londres (1º edge validado OOS) | 2 saídas × M15/H1: `_v1` (deixa correr) vs `_prot_v1` (trava +2p após +10p). Comparar exp R (proteção deve suavizar a curva SEM comer o edge) e M15 vs H1 |
| **B3 (WIN/WDO)** | mesma matriz, pregão 09:15–16:00, P&L em BRL | livro TOTALMENTE isolado (`mercado='b3'`); auditar em `/b3/analitico` e `/b3/auditoria` |

**Perguntas abertas que a sombra vai responder (não concluir antes da amostra):**
1. **Forex tem edge?** A 1ª auditoria deu exp **−0,114 R** (negativa) e 0 células no gate → GOLD e M1
   foram removidos das operações. Reauditar se o forex enxuto vira positivo em ALGUMA célula.
2. **F_BREAKOUT confirma OOS ao vivo?** Foi validado no histórico (+0,3–0,4 R); a sombra ao vivo é o
   teste de fogo. É o candidato nº 1 a demo se o gate passar.
3. **A saída inteligente (C) bate a saída crua (A/C_CORRE)?** Ver A vs C e C_CORRE vs C_HIBRIDA.
4. **B/C melhoram a entrada sobre A?** Comparar exp R por (estratégia×TF).
5. **B3 > forex?** A B3 vinha mais forte (PF 1,65 numa amostra); confirmar com N maior.

**Armadilha de múltiplos testes (skill §5, Deflated Sharpe):** testamos CENTENAS de células → algumas
passam por SORTE. O `auditoria_estatistica` já estima `falsos_esperados ≈ testadas × 0,05` e exige
split-half como deflator. **Desconfiar de célula aprovada com N mínimo e confiança "média".**

⚠️ **SL/saída de D_LINHAS e E_SENTINELA ainda é o ATR genérico** — o stop estrutural por estratégia
(guiado por MAE/MFE) é calibração SEPARADA, só depois que a entrada mostrar edge (não chutar — skill §2).

---

# 🔎 AUDITORIA COMPLETA DO CÓDIGO (16/07) — ~25 bugs achados e CORRIGIDOS

Auditoria adversarial de TODO o sistema (5 revisões paralelas por área + verificação manual de cada
achado + testes de regressão). **244 testes passando.** O que foi corrigido (ordem de gravidade):

**Corrompiam DADOS (afetavam a sombra em curso):**
1. **Backfill gravava o candle EM FORMAÇÃO** e o OR IGNORE o congelava p/ sempre (a cada push/redeploy!)
   → pivots/PDH-PDL/S-R de D1-W1/fuzzy cacheado contaminados. Fix: backfill descarta a última barra e usa
   **INSERT OR REPLACE** → o próximo deploy SANEIA os parciais antigos dentro da profundidade do backfill.
2. **Restart duplicava decisões** (~20/livro por push; N inflado no gate, executor podia reabrir sinal
   atrasado) → `decisao.watermark_inicial` semeia `ultimo_visto` do banco; executores descartam decisão
   com atraso > `ENTRADA_MAX_ATRASO_S` (300s) — também mata o trade-fantasma com tick velho pós-downtime.
3. **/relatorio e o GATE da Etapa 9 misturavam B3 (BRL) com forex (USD)** — podiam até sugerir célula da
   B3 p/ o `EXEC_REAL_*` do forex → filtro de `mercado` em `_carregar_trades`/`a_vs_c`/`distribuicao_
   bloqueio`/`auditar`. Dashboard `/` e `executor._equity` idem; dossiê filtra `simulado=1`.
4. **F_BREAKOUT: trade-lixo na borda das 17h** (candle que FECHA às 17:00 entrava e era fechado em
   segundos, −spread sistemático na célula candidata nº 1) → o candle precisa fechar ANTES do fim da janela.
5. **Análise "por sessão" deslocada 3h** (rótulos UTC com hora do servidor) → buckets no relógio do servidor.
6. **MAE do trade estopado** subestimado (gravava o do tick anterior) → registra a excursão até o stop.

**Mudaram a SEMÂNTICA de estratégia (bugs de lógica — livros medem agora a tese declarada):**
7. **Motor fuzzy tinha BURACO**: impulso forte com volume mediano → score 50 (= doji) e saltava a ~100
   cruzando vol=1.2 → regras complementares (contínuo; rally/absorção/exaustão preservados nos testes).
8. **`forca_serie`/`score_acima` asof pela ABERTURA** do candle (score do H1 das 10h — com dados até 11h —
   atribuído às 10:05 no replay/linha FORCA) → asof pelo **FECHAMENTO** (histórico = o que o vivo via).
9. **`qualidade_sr` inflava rejeição**: consolidação raspando a BORDA da banda contava N toques + N
   "rejeições" sem nunca alcançar o nível → visita contínua = 1 toque; rejeição exige FURAR o nível.
10. **Leque (D)**: rápida×lenta comparadas por POSIÇÃO de array (instantes diferentes — o "reengate"
    disparava pelo movimento da LENTA) → `score_acima` alinhado asof aos candles do TF de operação.
11. **Exaustão (D)**: o OR com a VWAP fazia "banda ±2σ" aceitar qualquer preço acima da VWAP → banda de
    verdade (VWAP só fallback). **Divergência (D)**: re-entrada serial no mesmo padrão → só entra no
    candle em que o swing novo é confirmado. **`_ultimo_evento` sem M1** (contexto de estrutura era
    micro-BOS de M1 = ruído). Confluência S/R exige TF distinto. `fecha_gap`: conf renomeada
    `pavio_contrario` (o que ela realmente mede).

**Robustez/modo real (latentes, corrigidos antes de ligar demo):**
12. `positions_get` None (erro) tratado como carteira vazia → reconciliação fabricava fechamentos de TODAS
    as posições reais → agora levanta MT5Erro. Reconciliação usa o preço do **DEAL de saída** (não o tick).
13. **Sem reconexão da ponte** (proxy RPyC morto após redeploy do mt5 → catálogo parava em silêncio) →
    `reconectar()` nas duas pontes + handlers nos 4 loops + retry no arranque do executor_b3.
14. **Curado abriria até 3 ordens reais do mesmo setup** (A + espelhos C_HIBRIDA/C_CORRE, mesma estratégia)
    → real só Variante A no curado; full-real exclui os espelhos.
15. **Offset servidor↔UTC derivava com tick velho** (fim de semana congelava `_agora()`) → só aceita
    variação ±1h após definido (±12h de sanidade), forex e B3. `_checar_dia` só marca o dia com equity ok.
16. F_BREAKOUT e saídas B/C valem também p/ posição REAL (senão o livro real mediria OUTRA estratégia);
    `novo_sl` (proteção/aperto) agora É persistido (`_mover_sl` + `mover_sl` no broker quando real).
17. `_pip` com fallback 0.0001 em falha → cache por símbolo (JPY não fica 100× errado); pula o ciclo sem pip.
18. `valor_ponto` B3 por PREFIXO (WIN*/WDO* — env com outro sufixo zerava o P&L BRL em silêncio).
19. CLI `manutencao reset` apagava forex+B3 → agora `reset` = SÓ forex (igual ao botão); `reset-tudo`
    explícito. `_backup` fecha conexões; `restaurar` tolera .bak pré-migração.
20. Métricas: gate usa `n_com_r` (N honesto da exp R); split-half "estável" agora exige POSITIVA nas duas
    metades (antes ✅ até p/ célula consistentemente perdedora); `max_dd_pct` sobre o pico vigente no DD;
    `regime_log` só grava na mudança (era +46k linhas/dia); `forca_serie` com janela limitada (não varre a
    tabela inteira); índice redundante de `candles` dropado; alarme CRÍTICO se `SECRET_KEY` for o default.

**⚠️ CONSEQUÊNCIA METODOLÓGICA (ler antes de auditar resultados):** os fixes 7–11 mudam o comportamento
das entradas/scores → a amostra de sombra PRÉ-fix não é comparável à PÓS-fix (e a pré-fix estava
contaminada pelos bugs 1–4). **Recomendado: zerar os livros de sombra (🧹 no painel, forex e B3) após o
deploy desta auditoria e recomeçar a contagem do gate do zero.** Os `candles` são preservados e o backfill
do deploy já saneia os parciais congelados.

**Pendências CONHECIDAS e aceitas (não são bugs abertos):** rollover de série da B3 (WIN$N muda de
contrato ~1×/mês → gap artificial contamina fecha_gap/extremos/ATR por ~1 dia; descartar o dia da virada é
melhoria futura); `fecha_gap_v1` na B3 é N=0 estrutural (a escala de "pip" do gap é forex — precisa de
calibração própria, não chutar); OR do H1 no F_BREAKOUT = 1ª vela inteira (60min, igual ao estudo validado
— M15 e H1 não testam a MESMA OR); Telegram síncrono no loop de gestão (até 10s de atraso se indisponível);
scores fuzzy do 1º ciclo de um par têm referência curta (cache); B3: último candle do pregão só entra no
banco na manhã seguinte (design do coletor).

---

# 🕒 LINHA DO TEMPO POR ESTRATÉGIA (changelog — o que foi feito em cada uma e por quê)

> **Para que serve:** registro histórico de CADA estratégia — quando nasceu, que ajustes/melhorias levou,
> a MOTIVAÇÃO de cada mudança e se a sombra depois CONFIRMOU ou REFUTOU. Assim dá para, ao longo do tempo,
> julgar o que fez sentido, o que precisa voltar atrás e o que foi irrelevante. **Como manter:** a cada
> criação/ajuste de estratégia, acrescente 1 linha datada `AA/MM · o quê · POR QUÊ · efeito esperado` e,
> quando a sombra der veredito (Etapa 9), marque `✅ confirmou` / `❌ refutou` / `➖ inconclusivo (N baixo)`.
> Convenção de status: 🟢 rodando · 🧪 em teste (sombra) · 🅰️/🅱️ gêmeo A/B de outra · ⏸️ pausada.
>
> ⚠️ **Marco divisor (16/07):** a auditoria completa corrigiu ~25 bugs; os fixes 7–11 mudaram entradas/scores.
> **Toda amostra PRÉ-16/07 é incomparável à pós** — ao julgar histórico, separe antes/depois desse deploy.

## Variante A — as estratégias "cruas" (grupo de CONTROLE, nunca reescrever)
- **`confluencia_v1`** · Confluência (tendência/S-R) · 🟢
  - 12/07 · NASCEU na Fase 4 (1ª estratégia; peso de evidências S/R + estrutura, sem gates rígidos).
  - 13/07 · entrada por **rejeição no nível** virou CONFLUÊNCIA (soma no score), não gate — p/ não secar entradas.
  - 16/07 · herdou os fixes de fuzzy/S-R (asof, `qualidade_sr` sem inflar rejeição, confluência exige TF distinto).
- **`sweep_choch_v1`** · Caça-stops + reversão (liquidity sweep + CHoCH no M5) · 🟢
  - 13/07 · NASCEU (2ª estratégia; varre máx/mín, falha e fecha de volta = stop-hunt Wyckoff).
  - ⚠️ SL ainda é ATR 3× genérico; o stop estrutural (atrás do pavio) é calibração futura guiada por MAE/MFE.
- **`sweep_choch_abs_v1`** · gêmea A/B da caça-stops COM filtro de ABSORÇÃO · 🅰️🅱️🧪
  - 14/07 · NASCEU. MOTIVO: testar se exigir absorção (vol alto + corpo fraco na vela do sweep) melhora a
    expectância vs a `sweep_choch_v1` (controle intocado). Sombra decide.
- **`order_block_v1`** · Order block (reteste) · 🟢
  - 13/07 · NASCEU (detecção exige displacement/FVG, só M15/H1, zona fresca; entra no reteste + rejeição soft).
  - 16/07 · `fecha_gap`-style: nada aqui; herdou fixes gerais.
  - 18/07 · **auditoria em lote (54 trades C_HIBRIDA):** 28/28 perdedoras foram CONTRA de imediato (MFE<0,3R),
    só 3/54 com rejeição → a entrada por "só encostar na zona" é fraca. Levou ao gêmeo abaixo. ➖ (N baixo, pré-fix).
  - 18/07 · **2ª auditoria (26 trades C_CORRE, PÓS-fix 16/07):** wr 65% · exp **+0,26R** · PF 1,83 (positiva, mas
    N<30 → ➖ inconclusiva). DOIS achados: (a) **`entrada_adiantada` = 5/5 perdedoras −1R com MFE≈0** (nunca andaram
    a favor) → CONFIRMA a tese que originou o gêmeo `order_block_rej_v1` (a rejeição é o filtro certo). (b) **regime
    `lateral` é o dreno:** 14/26 trades (54%) mas exp **−0,035R** (empate negativo), enquanto transição (+0,82R),
    tendência_baixa (+0,60R) carregam TODO o edge — 2ª amostra a mostrar OB fraco no lateral (a 1ª deu −0,10R).
    NÃO gatear o controle (skill §5 data-snooping a N=26 + regra "aditivo/controle intocável"); regime segue REFORÇO.
    Achado de calibração (anotado, não mexido): MAE dos 17 ganhadores mediana −0,50R, só 3 precisaram > −0,65R → o
    stop ATR×3 é mais largo que o necessário (candidato a stop estrutural/aperto guiado por MAE, calibração futura).
  - 18/07 · **3ª auditoria — o CONTROLE puro (36 trades A_ORIGINAL, saída genérica):** wr 50% · exp **+0,018R**
    (~empate) · +0,93 USD. Leitura mais limpa que a C (a saída genérica NÃO capa os vencedores). **Veredito:
    ENTRADA fraca, mas estratégia NÃO quebrada** — o edge existe em contexto: **transição +0,29R · tend_alta +0,33R ·
    tend_baixa +0,15R** (positivos), e **`lateral` é o DRENO: 17/36 trades (47%!) exp −0,285R** (somaR −4,85 — sozinho
    joga o livro pra baixo). Entrada: **12/18 perdedoras foram contra de imediato** (MFE<0,3R) e **só 2/36 tinham
    rejeição** → 3ª amostra a confirmar que a OB "só encostar na zona" é fraca. **A SAÍDA da A é saudável:** vencedores
    R médio **+1,00** (MFE +1,29R), 8 saíram por CHoCH/giveback com r≥1 → o gestor genérico deixa correr (o problema
    NÃO é a saída, é a entrada). Por par: USDJPY +5,54R carrega, AUDUSD −4,61R afunda (overlap com regime). M15 (+0,10)
    > M5 (−0,05). **NENHUM ajuste novo no código:** (a) a melhoria da entrada já existe como `order_block_rej_v1` e
    ESTA amostra a reforça; (b) gatear `lateral` no controle é proibido (regra "controle intocável") e seria
    data-snooping a N=36 (skill §5); (c) o stop ATR×3 largo reaparece (ganhadores MAE −0,47R médio — não precisaram
    de tanto espaço) → segue como calibração futura guiada por MAE, não chutar.
- **`order_block_rej_v1`** · Order block + rejeição · 🅰️🅱️🧪
  - 18/07 · NASCEU. MOTIVO: o achado acima. MESMA detecção, mas SÓ entra se a vela REJEITAR a borda do bloco
    (pavio + fecha de volta). Efeito esperado: matar as perdedoras de reversão imediata. Sombra decide (Etapa 9).
  - 18/07 · a 2ª auditoria (C_CORRE, acima) REFORÇA a aposta: as 5 perdedoras puras da original eram todas
    `entrada_adiantada` (MFE≈0) — exatamente o que exigir rejeição deve barrar. Confirmar/refutar pela sombra do gêmeo.
  - 18/07 · a 3ª auditoria (controle A_ORIGINAL, 36 trades) REFORÇA de novo: 12/18 perdedoras contra de imediato,
    só 2/36 com rejeição. Espera-se que o gêmeo (a) barre essas perdedoras E (b) reduza naturalmente as entradas no
    `lateral` (rejeição confirmada é rara em range choppy) — a sombra dirá se recupera a expectância.
- **`pullback_tendencia_v1`** · Pullback na tendência · 🟢
  - 13/07 · NASCEU (a favor do H1; recua a S/R forte e a rejeição é o GATILHO obrigatório; OB coincidente reforça).
- **`fecha_gap_v1`** · Fechamento de gap · 🟢
  - 13/07 · NASCEU (fade do gap de sessão rumo ao fechamento anterior; momentum p/ o fill + espaço).
  - 16/07 · a "confluência rejeição" foi renomeada **`pavio_contrario`** (usava o próprio close como nível → o
    toque era trivial; ela media só um pavio grande). MOTIVO: honestidade na auditoria de confluências.
  - ⚠️ na B3 é **N=0 estrutural** (a escala de "pip" do gap é de forex; precisa calibração própria — não chutar).
- **`pullback_rompimento_v1`** · Pullback ao rompimento (break-and-retest, polaridade invertida) · 🟢
  - 13/07 · NASCEU (nível rompido por BOS vira suporte/resistência e rejeita no reteste).
  - 16/07 · afetada pelo fix `_ultimo_evento` **sem M1**: antes tomava a direção de um micro-BOS de M1 (ruído);
    agora o contexto de estrutura vem de M5+. MOTIVO: a tese é rompimento de estrutura real, não micro-swing.
- **`rompimento_extremos_v1`** · Rompimento máx/mín do dia (PDH/PDL + reteste) · 🟢
  - 13/07 · NASCEU (rompe a máx/mín do dia anterior e reteste com rejeição).
- **`pullback_medias_v1`** · Pullback a médias (EMA9/20 do TF acima) · 🟢
  - 13/07 · NASCEU (ETAPA 2; a favor da tendência, toque na EMA do TF superior; FVG/OB coincidente DOBRA o score).
- **`pivot_confluencia_v1`** · Pivot + confluência S/R · 🟢
  - 13/07 · NASCEU (ETAPA 2; fade de pivot que está a <ATR de zona S/R/OB + rejeição; lateral é o terreno natural).

## Variante B — Fuzzy Puro (fiel à didática do PDF; livro paralelo, não filtra a A)
- **`fuzzy_puro_v1`** · Fuzzy Puro (maré 60/verde), timing M1 · 🟢
  - 14/07 · NASCEU (ETAPA 5; pirâmide MTF estrita M15 maré / M5 correnteza / M1 timing; checklist 6 itens; std do candle).
  - 15/07 · ⚠️ **deixou de rodar no forex** quando o M1 saiu de `TFS_OPERACAO` (timing=M1). Segue na B3 (M1 lá).
- **`fuzzy_puro_lima_v1`** · gêmeo da maré FIEL ao PDF (Lima=76, mais seletiva) · 🅱️🧪
  - 14/07 · NASCEU (item 4 de fidelidade). MOTIVO: comparar maré 60 × 76 — os dados dizem se a mais seletiva rende mais.

## Variante C — Híbrida (A + camada fuzzy) e o experimento da SAÍDA
- **C_HIBRIDA** (espelha cada `entrou` da A com veto/soma fuzzy) · 🧪
  - 14/07 · NASCEU (ETAPA 6). Camada fuzzy VETA contradições claras (absorção contra, M15 contra, etc.) e soma a favor.
  - 14/07 · **saída própria plugada** (saída antecipada M5 fuzzy contra + aperto na exaustão) — antes a sombra
    catalogava tudo pela saída genérica. MOTIVO: a 1ª auditoria mostrou 100% das perdedoras saindo no stop cheio.
  - 14/07 · **fix de carência** (`HIBRIDA_SAIDA_MIN_CANDLES`): a saída antecipada disparava no 1º ciclo (fechava
    no mesmo minuto, −1 pip). Agora só fecha após ≥2 velas. MOTIVO: era o exit reagindo à FOTO da entrada.
  - 18/07 · **auditoria (54 trades):** 49/54 saíram pela "saída antecipada C" capando vencedores (MFE médio dos
    vencedores só +0,47R; um viu +10 pips DEPOIS do corte). Suspeita: o corte fuzzy come o lucro → ver C_CORRE. ➖.
- **C_CORRE** (MESMAS entradas da C, SEM o corte fuzzy) · 🧪
  - 15/07 · NASCEU. MOTIVO: isolar SÓ a saída — se C_CORRE > C_HIBRIDA, o corte fuzzy capa os vencedores e deve
    ser aposentado. É a ferramenta que responde ao achado de 18/07 acima.

## Família D_LINHAS — dinâmica das CURVAS de score (4 estratégias-puras) · 🧪
- 14/07 · NASCERAM as 4: **`fuzzy_divergencia_v1`** (esforço×resultado, Lei 2), **`fuzzy_pullback_leque_v1`**
  (recuo+reengate do leque na maré), **`fuzzy_sync_flip_v1`** (Sync amarelo→alinha), **`fuzzy_exaustao_v1`** (clímax rola).
- 16/07 · fixes que mudaram a TESE de 3 delas (mediam outra coisa antes): divergência **só no swing recém-confirmado**
  (era re-entrada serial); leque **alinhado asof** (comparava posições de array = instantes diferentes; o "reengate"
  disparava pela linha LENTA); exaustão **exige banda ±2σ de verdade** (o OR com a VWAP aceitava qualquer preço acima dela).
- ⚠️ SL/saída ainda é ATR genérico — stop estrutural por estratégia é calibração futura (só depois de mostrar edge).

## Família E_SENTINELA — FORÇA contínua (micro/macro) + LEQUE (3 estratégias) · 🧪
- 15/07 · NASCERAM as 3: **`sentinela_forca_v1`**, **`sentinela_divergencia_v1`**, **`sentinela_leque_v1`** (inspiradas
  no Sync Line do criador do PDF; lê a força contínua, não o score-nível).
- 15/07 · a LINHA da força virou **ACUMULADOR** (balança 0–100) em vez da média estática (quase plana). MOTIVO:
  o dono observou que "não balança como a do criador". Ajuste visual/de leitura, não muda a entrada.
- 16/07 · herdou o fix do `forca_serie` (asof pelo FECHAMENTO, não pela abertura — evita look-ahead no replay/linha).

## Família F_BREAKOUT — rompimento da abertura de Londres (1º EDGE validado OOS) · 🧪 (candidato nº 1 a demo)
- 15/07 · NASCERAM 2 livros × M15/H1: **`breakout_londres_v1`** (deixa correr) e **`breakout_londres_prot_v1`**
  (trava +2p após +10p). MOTIVO: único edge que passou fora da amostra (+0,3–0,4 R); as teses do trader (H4 flush,
  nível-imã) NÃO se sustentaram nos dados — o breakout de Londres sim. Stop ESTRUTURAL (a OR oposta), não ATR.
- 16/07 · fix do **trade-lixo na borda das 17h** (candle que fecha às 17:00 entrava e fechava em segundos, −spread);
  gestão passou a valer p/ posição REAL também; `novo_sl` (proteção) agora É persistido.

## B3 (WIN/WDO) — a MESMA matriz de estratégias, mercado isolado (BRL, pregão 09:15–16:00) · 🧪
- 14/07 · as estratégias (funções puras) foram reusadas sobre WIN/WDO via `decisao_b3`/`executor_b3` (livro
  `mercado='b3'`, ponte data-only da Genial). NÃO são estratégias novas — é a matriz A/B/C/… rodando na B3.
- ⚠️ pendências B3 conhecidas: rollover de série (~1 dia de gap/mês), `fecha_gap_v1` N=0 (escala de gap é forex).

## Decisões estruturais que afetam TODAS (não são estratégia, mas mudam o que roda)
- 13/07 · S/R forte só de **H1/D1/W1** (M5/M15 = ruído); força por toques/rejeição/recência/peso do TF.
- 15/07 · **GOLD fora de `PARES`** e **M1 fora de `TFS_OPERACAO`** (pós-auditoria de 1657 trades: forex exp −0,114R,
  o "+224 USD" era ilusão do ouro; M1 ralo pelo custo). Consequência: Variante B some do forex (timing=M1).
- 16/07 · auditoria completa (~25 bugs). Recomendado **zerar os livros de sombra** após o deploy e recomeçar o gate.

---

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
**FIX de carência (14/07, sombra B3 "fechou com um tick, não deixou andar"):** a saída antecipada C /
técnica B disparava no PRIMEIRO ciclo de `gerir` após a abertura — o M5 fuzzy no instante da entrada
está contra e fechava a ordem no mesmo minuto (−1 pip, MAE ~−0.02R, stop NUNCA tocado). Não era escala
(a calibração 8b.1 está boa): era o exit reagindo à FOTO da entrada, não a uma mudança de contexto.
Agora `gestao_saida_variante` só FECHA depois que a posição viveu ≥ `HIBRIDA_SAIDA_MIN_CANDLES` (default 2)
velas do SEU TF (`idade_candles` calculado nos dois executores via `config.MINUTOS_TF`); o aperto de stop
na exaustão (só aproxima) continua valendo desde o início. Aditivo, Variante A intocada, forex + B3.
Sem a carência a comparação A×C era inútil (C sempre raspava −1 pip). Env `HIBRIDA_SAIDA_MIN_CANDLES`.

## Experimento C_CORRE — "deixa correr" × corte fuzzy (15/07, motivado pela 1ª auditoria real da B3)
A auditoria de 180 trades reais (B3, 14/07: PF 1,65, +R$1052) revelou o vazamento da SAÍDA: a **saída
antecipada da Variante C dominou (132/180 trades) mas rendeu só +1,91/trade**, enquanto quem foi
DEIXADO CORRER (giveback estrutural "reversão cedeu R do pico") rendeu **+56,75** (n=8, 100% wr) e o
fechamento do pregão +16,73 (n=26) — ou seja, o corte fuzzy CAPA os vencedores cedo. (2º achado: **M1
é ralo** — negativo/fino em TODAS as estratégias pelo custo/spread; M5/M15 carregam o edge. M1 segue
só observação.) Para MEDIR isso limpo (sem contaminar a C que roda), `decisao.avaliar_par` agora gera,
p/ cada decisão da C, um GÊMEO `variante=C_CORRE` com a MESMA entrada; no executor o C_CORRE **não** está
no set `("B_FUZZY_PURO","C_HIBRIDA")` da `gestao_saida_variante` → cai no gestor genérico (stop + giveback,
SEM corte fuzzy). Assim o /relatorio compara `C_HIBRIDA` (corta cedo) × `C_CORRE` (deixa andar) isolando
SÓ a saída (mesmas entradas). Env `EXPERIMENTO_CORRE_HABILITADO` (default on). Testes: gêmeo gerado
(`test_multitf`) + `gestao_saida_variante("C_CORRE")` é no-op (`test_estrategias`). ⚠️ Depois de dias de
amostra: se C_CORRE > C_HIBRIDA, aposentar a saída fuzzy e deixar os vencedores correrem; a "prejuízo
pequeno" (stop estrutural apertado) é uma calibração SEPARADA (não confundir com este teste de saída).
⚠️ Aperto de stop da exaustão é in-memory (não persiste em `sl_servidor`); some num restart do executor
(aceitável na sombra). Próximo passo de auditoria: comparar exp. de C (com saída nova) vs A no /relatorio.

## Como rodar / testar / publicar
- Testes (sem pytest): `python -m sistema_forex.tests.test_gestao` (idem `test_estrategias`,
  `test_indicadores`, `test_multitf`, `test_grafico`, `test_auditoria`, `test_manutencao`,
  `test_fuzzy`, `test_relatorio`, `test_auditoria_estatistica`, `test_b3`). **244 testes, todos passando.**
  Rodar sempre antes de commitar.
- Compilar: `python -m py_compile sistema_forex/*.py sistema_forex/web/*.py`.
- Publicar = commit + `git push -u origin <branch>` → Dokploy redeploya sozinho.
- Env sensíveis (senha do painel, VNC, MT5) só no Environment do Dokploy — nunca no git.

## Fidelidade ao PDF Fuzzy Wyckoff + volume real na B3 (14/07)
Rodada de correções de fidelidade ao PDF didático (escolhidas pelo dono), ADITIVAS/desligáveis:
- **Item 1 — bandas de cor:** `fuzzy_score.estado_por_score` alinhado ao PDF (lima 76+ · **verde 56–75** ·
  **branco 46–55** · **fúcsia 26–45** · vermelho ≤25). Só COR das velas + componente EV da sync (não
  bloqueia) — entradas usam o score numérico.
- **Item 4 — A/B da maré (Variante B):** `avaliar_fuzzy_puro` ganhou o parâmetro `estrategia`; a `decisao`
  dispara DOIS livros paralelos no TF de timing — `fuzzy_puro_v1` (maré 60/verde, atual) e
  **`fuzzy_puro_lima_v1`** (maré 76/Lima, fiel ao PDF). Comparáveis no /relatorio (Lima seca sinais →
  os dados dizem se rende mais). Env `FUZZY_B2_HABILITADA`/`FUZZY_B2_MARE_MIN`.
- **Item 5 — VWAP no pregão B3:** `analise._inicio_sessao_vwap` ancora a VWAP diária na ABERTURA DO
  PREGÃO p/ B3 (`VWAP_B3_ANCORA_HORA`=9h no relógio do servidor Genial), meia-noite p/ forex.
  ⚠️ assume que o relógio do servidor Genial mostra a hora local do pregão — validar com os candles.
- **Item 6 — volume REAL na B3:** coluna `candles.real_volume` (contratos; migração idempotente guardada),
  `mt5_bridge_b3` devolve real_volume, `gravar_candles` persiste (NULL no forex). `niveis_vwap` e
  `fuzzy_score.atualizar_par` usam `COALESCE(NULLIF(real_volume,0), tick_volume)` → B3 lê o volume Wyckoff
  verdadeiro (absorção/exaustão/VWAP), forex inalterado (cai no tick_volume).
- **NÃO feito (dono adiou):** item 2 (gatilho de rompimento no checklist da Variante B — MUDA entradas).
  Testes por item em `test_fuzzy`/`test_estrategias`/`test_b3`.

## Família D_LINHAS — estratégias pela DINÂMICA das linhas de score (14/07)
4º cenário comparável (A original · B fuzzy puro · C híbrida · **D_LINHAS**), ADITIVO/desligável. As
A/B/C leem o score como NÍVEL estático; a D lê o MOVIMENTO das curvas por TF. 4 estratégias PURAS
(`estrategias.py`, testadas), cada uma um livro de sombra próprio (`variante=D_LINHAS`), rodando por
(par, TF de operação):
- **`fuzzy_divergencia_v1`** (A): esforço×resultado (Lei 2 Wyckoff) — preço faz topo↑ mas o score faz
  topo↓ (na banda +1σ VWAP) → venda; espelho p/ compra no fundo. Reversão.
- **`fuzzy_pullback_leque_v1`** (B): na maré M15, a linha RÁPIDA (TF op) recua contra a LENTA (TF acima)
  e REENGATA cruzando de volta, no valor da VWAP → continuação a favor da tendência.
- **`fuzzy_sync_flip_v1`** (C): Sync sai de amarelo e ALINHA (verde/vermelho) neste candle, com maré a
  favor e rompendo a VWAP → estouro nascente.
- **`fuzzy_exaustao_v1`** (D): score preso no extremo (≥80/≤20) por N velas e ROLA na banda ±2σ →
  fade de clímax.
Snapshot ganhou `serie_op` (high/low/close+score alinhados do TF op, JOIN candles×fuzzy_scores),
`score_acima` (linha do `TF_ACIMA`) e `sync_ult` (2 últimos estados p/ o flip). Sem look-ahead (swings
só usam velas fechadas). Envs: `DIVERGENCIA_/PULLBACK_LEQUE_/SYNC_FLIP_/EXAUSTAO_HABILITADA`, `LINHAS_*`,
`LEQUE_*`, `EXAUSTAO_*`. `MAX_POS_SOMBRA` 400→800 (mais livros). ⚠️ SL/saída ainda é o genérico (ATR);
o "deixar correr + prejuízo pequeno" (stop estrutural apertado) é a PRÓXIMA calibração, guiada por
MAE/MFE por estratégia — não chutar (skill §2). Nada vira demo sem a Etapa 9 (N≥50 + split-half).
**DECISÃO (14/07):** deixar a sombra RODAR e coletar amostra antes de mexer em qualquer coisa. As 7
(na verdade 10) estratégias da Variante A **NÃO foram reescritas** — seguem como CONTROLE intocado
(princípio governante); a D_LINHAS é família NOVA/standalone, não uma versão das originais. **Parado
p/ depois da amostra:** (1) stop estrutural por estratégia guiado por MAE/MFE; (2) possível `D_HIBRIDA`
= gêmeo de cada original filtrado pela DINÂMICA das linhas (como a C faz com o fuzzy estático) — só se
as 4 linhas-puras mostrarem edge isolado (evita armadilha de múltiplos testes espalhando dezenas de
livros novos). Ordem de auditoria: 4 linhas-puras primeiro → depois cruzar com as originais.

## Família E_SENTINELA — FORÇA contínua (micro/macro) + LEQUE (15/07, ideia do "Sentinela" do PDF)
5º cenário comparável (A · B · C · D_LINHAS · **E_SENTINELA**), ADITIVO/desligável. Inspirado no
"Sentinel_Sync_Line" do criador do PDF (prints do WINQ26): em vez do score como nível (A/B/C) ou do
movimento das linhas de score (D), lê a **FORÇA CONTÍNUA** — `fuzzy_score.forca_sync` devolve `micro`
(média dos score−50 de M1/M5), `macro` (M15/H1), `forca` 0–100 (50 neutro) e `estado` verde/vermelho/
**amarelo=divergência micro×macro** (o "ATENÇÃO"). Mostra a força construindo/divergindo ANTES da cor
virar (nossa Sync antiga era só 3 estados). ⚠️ o PDF NÃO dá a fórmula numérica do Sentinela — esta é
nossa versão fiel ao princípio, p/ VALIDAR por comparação na sombra. **A LINHA plotada é um ACUMULADOR**
(`acc = acc*decay + (micro+macro)`, `forca = 50+50·softsign(acc/escala)` em `forca_serie`) — não a média
estática (que ficava quase plana em ~50 e o dono reclamou que "não balança como a do criador"); assim a
linha SOBE na alta e CAI na baixa, balançando 0–100 como o Sync Line do Sentinela. Envs `SENT_FORCA_DECAY`
(memória) e `SENT_FORCA_ESCALA` (amplitude) p/ calibrar visualmente. `forca_inst` guarda o nível estático. `leque_spread` = amplitude entre
as 4 linhas (fan; comprimido=mola, aberto=tendência). `forca_serie` (asof dos 4 TFs, sem look-ahead)
alimenta o painel e as estratégias. **Linha no gráfico:** o `/api/candles` devolve `scores["FORCA"]`
(0–100, no TF do gráfico) → 5ª linha BRANCA e mais grossa no sub-painel de scores, comparável às 4
linhas de TF (pedido do dono: validar por comparativo visual). **3 estratégias** (`variante=E_SENTINELA`):
`sentinela_forca_v1` (força alinhada cruza o limiar rompendo a VWAP), `sentinela_divergencia_v1`
(micro×macro divergem → fade a favor do macro no extremo da banda) e `sentinela_leque_v1` (leque
comprime e EXPANDE na direção da força). Snapshot ganhou `forca`/`forca_serie`. Envs `SENTINELA_HABILITADA`,
`SENT_FORCA_MIN`, `SENT_LEQUE_ESTREITO/LARGO`, `SENT_FORCA_JANELA`, `SENT_*_HABILITADA`. `MAX_POS_SOMBRA`
800→1200. Testes em `test_fuzzy` (forca_sync/leque/asof) + `test_estrategias` (as 3). ⚠️ SL/saída segue o
genérico; validar por expectância na sombra antes de concluir (skill §5) — é o "5º dado" p/ comparar.

## Order block + rejeição — gêmeo A/B da entrada (18/07, motivado pela auditoria da C_HIBRIDA)
A 1ª auditoria em lote da `order_block_v1` (livro C_HIBRIDA, 54 trades exportados pelo raio-X-zip) mostrou
o vazamento da ENTRADA: **28/28 perdedoras foram CONTRA de imediato** (MFE < 0,3R — nunca andaram a favor),
só 3/54 tinham a confluência `rejeicao` e o pior regime era `lateral` (n=30, exp −0,10R). Ou seja: a OB
entra a mercado quando o preço só ENCOSTA na zona (dentro de `nivel_prox_atr×ATR`), sem confirmação — vira
moeda pro alto que muitas vezes já sai contra. Fix ADITIVO (controle intocado): **`order_block_rej_v1`** =
MESMA detecção da `order_block_v1`, mas SÓ entra se a vela **REJEITAR a borda do bloco** (pavio + fecha de
volta — `exigir_rejeicao=True`). Livro de sombra próprio e comparável à original (como a `sweep_choch_abs_v1`
é da `sweep_choch_v1`); nasce nos livros A/C_HIBRIDA/C_CORRE automaticamente. `avaliar_order_block` ganhou o
parâmetro `estrategia`; env `OB_REJ_HABILITADA` (default on). Testes em `test_estrategias` (entra só com
rejeição; a original entra na mesma vela = o gêmeo é mais seletivo). ⚠️ A sombra decide se a rejeição
recupera a expectância (Etapa 9) — NÃO é conclusão do N=54 (amostra pequena e pré-fix). **Achado paralelo
(não-código):** na C_HIBRIDA, 49/54 saíram pela "saída antecipada C (M5 fuzzy contra)" capando os vencedores
(MFE médio dos vencedores só +0,47R; um trade viu +10 pips DEPOIS de a C cortar) → é o corte fuzzy comendo o
lucro, exatamente o que o **C_CORRE** já mede (deixa correr × corta). Comparar C_HIBRIDA × C_CORRE no /relatorio.

## Família F_BREAKOUT — rompimento da abertura de Londres (15/07, 1º EDGE validado OOS)
6º cenário comparável (A · B · C · D_LINHAS · E_SENTINELA · **F_BREAKOUT**), ADITIVO/desligável. É a
PRIMEIRA estratégia que nasceu de um **estudo histórico validado FORA DA AMOSTRA** (não de teoria): a
exploração cética dos candles coletados (H1+M15) mostrou que o movimento grande do forex nasce na
**abertura de Londres** — um breakout da faixa de abertura (opening range) rende **+0,3–0,4 R líquido de
spread**, com PLATÔ (não pico de overfit) e edge em vários pares. As teses do trader (candle H4 com
abertura=fechamento anterior + reversão; nível como imã/S-R) NÃO se sustentaram nos dados; o breakout de
Londres sim. **Como funciona** (`decisao._or_londres`, sem look-ahead): mede a FAIXA (máx/mín) das velas
entre `BREAKOUT_OR_HORA` (10h servidor = 07:00 UTC/abertura de Londres) e +`BREAKOUT_OR_MIN` (45min); o
**PRIMEIRO fechamento do dia** que rompe a faixa (dentro da janela até `BREAKOUT_FIM_HORA`=17h) entra na
DIREÇÃO do rompimento. Não prevê direção (o rompimento dá) e **deixa correr**. **Stop ESTRUTURAL** = a OR
oposta (`sl_pips` = amplitude da faixa, gravado no `dados_json` da decisão; o `executor._abrir` lê e usa no
lugar do ATR genérico). **2 livros × 2 TFs (M15/H1) = 4 combinações**, `variante=F_BREAKOUT`, forex-only,
**USDCAD excluído** (pedido do dono): `breakout_londres_v1` (sem proteção — corre até o fim da janela, máx
expectância) e `breakout_londres_prot_v1` (mesma ENTRADA; a SAÍDA trava +`BREAKOUT_PROT_LOCK_PIPS` (+2p)
depois que o MFE atinge +`BREAKOUT_PROT_TRIGGER_PIPS` (+10p = "100 pipetes"), deixando o resto correr — o
estudo mostrou que o B/E cru é raspado pelo spread, +2p é a posição válida do stop de proteção; a proteção
é ~neutra em expectância mas SUAVIZA a curva). **Executor:** `gerir_breakout` (PURA) fecha no fim da janela
de Londres e aplica a proteção só no `_prot_v1`; o F_BREAKOUT **pula o gestor genérico** (giveback/BE/tempo
cortariam o runner — "deixa correr"). O stop (OR/protegido) é emulado pelo bloco de emergência do executor.
Envs `BREAKOUT_HABILITADA` (on), `BREAKOUT_TFS`=M15,H1, `BREAKOUT_EXCLUI`=USDCAD, `BREAKOUT_OR_HORA`/`_MIN`/
`_FIM_HORA`, `BREAKOUT_OR_MIN_PIPS` (faixa degenerada), `BREAKOUT_PROT_TRIGGER_PIPS`/`_LOCK_PIPS`. Testes:
`test_estrategias` (entrada/spread, gestão fim-de-janela + proteção só no prot) + `test_multitf`
(`_or_londres` detecta o 1º rompimento + sl_pips). **236 testes, todos passando.** ⚠️ Assume que a hora do
servidor XM (UTC+3) põe Londres às 10h — validar com os candles; ajustar `BREAKOUT_OR_HORA` se o fuso diferir.
Comparar exp. na sombra (F com/sem proteção × M15/H1) antes de qualquer promoção (Etapa 9).

## Forex enxugado — GOLD e M1 fora das operações (15/07, pós-auditoria de 1657 trades)
A 1ª auditoria real do forex mostrou o forex MUITO mais fraco que a B3: exp **−0,114 R** (negativa!),
0 células passando no gate da Etapa 9, e o "+224 USD" era **ilusão do GOLD** (A tinha exp −0,151 R mas
+1,12 USD/trade — dois vencedores gigantes de ouro puxavam o dólar). Além disso o **M1 é ralo** (as
células M1 estáveis no split-half eram TODAS negativas — custo/spread come o alvo). Pedido do dono:
- **GOLD REMOVIDO de `config.PARES`** (params ficam inertes em `PARAMS_SIMBOLO`; reincluir = voltar na lista/env).
- **M1 REMOVIDO de `config.TFS_OPERACAO`** (agora `M5,M15`). O M1 SEGUE COLETADO (`TFS_COLETA`) e alimenta
  a pirâmide fuzzy/sync — só não gera mais operação no forex. ⚠️ Consequência: a **Variante B** (fuzzy_puro,
  timing=M1) deixa de rodar no forex. A **B3 usa `TFS_OPERACAO_B3` próprio e NÃO é afetada** (M1 segue lá).
⚠️ `PARES`/`TFS_OPERACAO` são env-overridáveis: se o Dokploy setar esses envs com GOLD/M1, o default do
código não vale — conferir/atualizar o Environment do Dokploy também.

## Pares monitorados (sombra) — 13/07 (GOLD removido em 15/07, ver acima)
`config.PARES` (env-configurável no Dokploy): `EURUSD#, GBPUSD#, USDCAD, USDJPY#, AUDUSD#, GBPJPY#`.
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
0. **AO CATALOGAR RESULTADOS: ler o "🎯 PAINEL DE VALIDAÇÃO" no topo deste arquivo** — ele diz, por
   grupo (A/B/C/C_CORRE/D_LINHAS/E_SENTINELA/F_BREAKOUT + B3), o que cada livro testa e como julgar
   (gate N≥50 · exp R>0 · PF≥1,3 · split-half). Rodar a aba "🎯 Aprovação para demo" do /relatorio.
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
**FIX de fidelidade ao manual fuzzy (14/07, dono: "não tem linha de vwap, não tem volume financeiro na
B3"):** a VWAP e as bandas eram gravadas como `niveis` (UM valor) → o gráfico as desenhava como linha
horizontal chapada, não a CURVA que o manual/Wyckoff lê. Agora `indicadores.vwap_serie` (PURA/testada)
acumula VWAP+bandas candle-a-candle **resetando na âncora da sessão** (`analise._inicio_sessao_vwap`:
meia-noite no forex, abertura do pregão na B3) e o `/api/candles` devolve `vwap` (curvas vwap/sup1/inf1/
sup2/inf2) — a VWAP saiu do bloco `niveis` (não duplica). Além disso, **histograma de volume no rodapé**:
`/api/candles` devolve `volume` por candle usando `COALESCE(real_volume, tick_volume)` → na **B3 é o
volume financeiro/Wyckoff REAL (contratos)**, no forex é tick_volume; barras verde/vermelho por alta/baixa
em escala própria `volume` (rodapé ~15%). `grafico.html`: `serieVolume` (histograma) + `serieVwap`+4 bandas
(curvas na régua de preço). `_buscar_candles` agora traz tick_volume/real_volume. Testes: `vwap_serie`
acumula/reseta por sessão + guardas (`test_indicadores`). **231 testes, todos passando.**

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
**✅ Sub-etapa 8b.1 — CALIBRAÇÃO DE ESCALA (14/07, "comece a calibração analisando os candles que entrarem").**
Resolvido o bloqueio (a): `calibracao_b3.py` DERIVA a escala de WIN/WDO DOS CANDLES já coletados (nunca chuta —
lição GOLD). Funções PURAS testadas: `passo_preco` (TICK real via GCD da grade de preços → WIN=5, WDO=0,5),
`estatisticas_tf` (por TF: range da vela med/p90/máx, ATR med/p90, spread — tudo em pontos), `sugerir_params`
(piso/teto de SL + `tamanho_pip` p/ `PARAMS_SIMBOLO`, dimensionados pela REGRA DO OURO: SL nunca menor que uma
vela p90, teto largo p/ o ATR×3 mandar). `valor_ponto` (BRL, fato de contrato em `config_b3.VALOR_PONTO_B3` —
WIN R$0,20/pt, WDO R$10/pt — base do P&L da sombra) confirmável via `mt5_bridge_b3.info_simbolo` (novo leitor
DATA-ONLY de `symbol_info`: tick_size/tick_value → valor-por-ponto). Entregue: **dossiê** (`dossie_texto`, o
dono cola no chat), **CLI** (`python -m sistema_forex.calibracao_b3 [par] [--json] [--broker]`) e **seção no
painel `/b3`** (`_dados_b3.calibracao`, guardada — não quebra o painel se faltar dado). Params `CALIB_*`.
Testes em `test_b3.py` (9 casos: tick WIN/WDO, percentil, estatísticas, regra do ouro, sem-dados, do banco).
**190 testes, todos passando.** ⚠️ **Falta rodar contra o banco REAL da VPS** (poucos candles de WIN/WDO ainda)
→ conferir o dossiê no `/b3` conforme a coleta cresce e então fixar os valores em `PARAMS_SIMBOLO_B3`.

**✅ Sub-etapa 8b.2 — SOMBRA DA B3 LIGADA (14/07, bloqueio (b) resolvido — "ative as estratégias p/ a B3").**
Estrategista + executor de SOMBRA da B3 no ar (= os "resultados das estratégias" que o dono quer), ADITIVO e
ISOLADO do forex. Peças: (1) coluna **`mercado`** (`forex`/`b3`, default `forex`, migração idempotente) em
`decisoes`/`trades` → ISOLA os livros: o executor do forex filtra `mercado='forex' OR NULL` (WIN/WDO nunca caem
na ponte errada), o da B3 só lê `mercado='b3'`. (2) **`decisao_b3.py`** — reusa a MESMA `decisao.avaliar_par`
(estratégias são funções puras/agnósticas; `avaliar_par` ganhou `mercado`/`sessao_utc`/`spread_max` opcionais,
forex intocado) sobre `config_b3.sombra_pares()` × `TFS_OPERACAO_B3` (M1/M5/M15), grava decisões `mercado='b3'`.
(3) **`executor_b3.py`** — simula ao vivo com o tick da ponte **data-only** da Genial (`mt5_bridge_b3`; impossível
enviar ordem por construção) e **P&L em BRL** (`config_b3.lucro_brl` = pontos × valor-por-ponto × contratos).
**Escala (tick/piso/teto de SL) DERIVADA dos candles** via `calibracao_b3` com TTL (`CALIB_REFRESH_S`) — regra do
ouro: sem candles suficientes, o par NÃO abre (log + skip), nunca insta-estopa. Reusa `gestao` pura + as leituras
agnósticas do `executor` do forex (`_atr`/`_evento_saida`/`_regime_atual`/`pode_abrir`/`_abrir_trade`/`_fechar_trade`);
carimba abertura/fechamento na hora do servidor da **Genial** (offset próprio; `_fechar_trade` ganhou
`fechamento_utc` p/ não usar o offset 0 do container B3). Gestão de saída por variante (B/C) igual ao forex.
(4) **Deploy:** serviços `estrategista_b3` + `executor_b3` nos dois composes; `.env.example` com a seção da sombra
(`B3_SOMBRA_HABILITADA`, `TFS_OPERACAO_B3`, `CONTRATOS_B3`, polls). (5) **Painel `/b3`:** `estrategias_ligadas`
reflete a config, conta decisões/trades por `mercado='b3'` e soma o **P&L em BRL** (`pnl_brl`). Env: `B3_SOMBRA_HABILITADA`
(default on), `TFS_OPERACAO_B3`, `SESSAO_B3` (permissivo 0–24 até confirmar o fuso da Genial), `SPREAD_MAX_B3`,
`CONTRATOS_B3`, `MAX_POS_SOMBRA_B3`, `PARAMS_SIMBOLO_B3` (override de escala, vazio = calibração manda). Testes em
`test_b3.py` (+11: P&L BRL compra/venda WIN/WDO, isolamento decisões/trades por mercado, legado NULL=forex, escala
da calibração/override/sem-dados). **201 testes, todos passando.** ⚠️ Auditar no `/b3` conforme o pregão roda:
conferir que a escala calibrada não insta-estopa e reconciliar a régua de spread (pontos/10). `EXEC_REAL` NUNCA
se aplica à B3 (ponte data-only). **Demais pendentes 8b+:** tabela `correlacao_b3`, painel MACRO, **veto de
correlação SÓ no B3** (NUNCA no forex), alerta de rollover. ⚠️ `gestao._moedas` não parseia metal/índice —
tratar antes de qualquer correlação WIN/WDO/GOLD.

**✅ Sub-etapa 8b.3 — ANÁLISE + AUDITORIA DA B3 (mesma riqueza do forex) + FIX do gráfico (14/07, pedido do
dono "não tem todas as análises e auditoria nessa página").** (1) **Bug do gráfico corrigido:** clicar "Ver
gráfico" em WIN/WDO devolvia `{"detail":"par/tf inválido"}` porque `/grafico` e `/api/candles` validavam só
`config.PARES` (forex). Agora aceitam também `config_b3.pares_ativos()` (`_pares_validos`/`_tfs_validos`) e um par
da B3 troca só entre símbolos/TFs da B3. (2) **Isolamento por mercado (correção):** `_analitico` (web) e
`auditoria._buscar_perdedores`/`dossie_perdedores` ganharam `mercado='forex'|'b3'` — antes liam TODOS os trades,
então a B3 vazava no /analitico e /auditoria do forex; agora `forex` (default, legado NULL=forex) exclui a B3 e
vice-versa. (3) **Páginas próprias da B3, reusando os templates ricos:** `/b3/analitico` + `/api/b3/analitico`
(curva de capital, por estratégia/TF/regime/sessão/par/motivo, Estratégia×TF, MAE/MFE — em **BRL**) e
`/b3/auditoria` + `/api/b3/auditoria` (dossiê das perdedoras classificadas por falha + raio-x em pips, pronto p/
colar na IA). `analitico.html`/`auditoria.html` parametrizados por `|default` (título/sub/moeda/api_url/base/nav
`b3`) — o forex fica intocado. Nav da `/b3` aponta p/ Análise B3 / Auditoria B3. Raio-x (`/trade/{id}`,
`/api/raiox/{id}`) já é agnóstico de mercado (funciona p/ WIN/WDO; preço em `.5f` fica feio no índice, cosmético).
Teste novo: isolamento forex×b3 no dossiê (`test_auditoria`). **202 testes, todos passando.** ⚠️ Cosmético a
calibrar depois: casas decimais do preço no raio-x visual da B3.

**✅ Sub-etapa 8b.4 — FIX do TICK-FANTASMA DE LEILÃO (14/07, dono: "lucro alto, algo errado na matemática").**
A sombra da B3 mostrava P&L absurdo (R$105 mil, PF 62.8) concentrado em 2 células de N minúsculo (Caça-stops M1
n=3 = R$71k; Fuzzy B M1 n=4 = R$35k), com **MFE médio de 251.99 R e 99.19 R** (impossível — MFE normal é 0–3 R).
**Causa-raiz:** `mt5_bridge_b3.tick_atual` devolvia `bid`/`ask` crus, incluindo os **0.0 que o MT5 retorna nas
fases de LEILÃO/pré-abertura/rolagem** de WIN/WDO. Um `ask=0` fechando uma posição **vendida** registra
`entrada − 0` = o valor CHEIO do contrato como lucro (WIN 178000 × R$0,20 ≈ R$35 mil num trade). Assimetria que
explica tudo: o stop emulado só protege a COMPRA (preço 0 ≤ SL → −1R), então a cotação-fantasma corrompe **só as
vencedoras vendidas** — por isso as perdedoras pareciam sãs (−1R limpo) e o "lucro" era todo fictício. **Correção
(aditiva, defensiva):** `mt5_bridge_b3.tick_valido(bid,ask)` (PURA: exige bid>0, ask>0, ask≥bid) → `tick_atual`
devolve `None` numa cotação inválida = AUSÊNCIA de preço (o executor já trata None: espera o próximo tick, nunca
fecha no fantasma). Mesma guarda posta no `mt5_bridge.tick_atual` do forex (GOLD fim de semana). **Limpeza dos
dados corrompidos:** `manutencao.reset-b3` (`resetar_b3`) apaga SÓ o livro `mercado='b3'` (BACKUP antes), forex e
`candles` intactos → rodar na VPS + redeploy para a sombra da B3 recomeçar limpa. Testes: `tick_valido` (6 casos)
+ `reset_b3` isola o livro. **204 testes, todos passando.** ⚠️ Ainda **pendente rodar na VPS:** `python -m
sistema_forex.manutencao reset-b3` + redeploy (o painel só volta a fazer sentido depois de zerar as vencedoras
fantasmas já gravadas).

**✅ Sub-etapa 8b.5 — HORÁRIO DE PREGÃO DA B3 + P&L FLUTUANTE AO VIVO (14/07, pedido do dono).** Dois ajustes,
o de horário SÓ para a B3 (o forex 24/5 fica intocado). (1) **Janela FINA de negociação da B3** (precisão de
MINUTOS — o forex usa hora cheia): `config_b3` ganhou `JANELA_ABERTURA_B3` (default **09:15–16:00**, envs
`B3_ABERTURA_INICIO`/`FIM`) e `B3_FECHAMENTO_FORCADO_MIN` (**17:30**, env `B3_FECHAMENTO_FORCADO`), com as puras
`_hhmm_para_min`/`minuto_do_dia`/`dentro_janela_abertura`/`hora_de_fechar_pregao` (relógio do servidor da Genial =
hora do candle/executor). **Abrir só 09:15–16:00** (volume cai ao fim da tarde): `decisao_b3.um_ciclo` pula a
avaliação do candle fora da janela (nenhuma entrada gerada). **Fechar à força às 17:30** (a corretora zera as
posições no fim do pregão, independentemente do resultado): `executor_b3._encerrar_pregao` (chamado no topo do
`ciclo`, antes de gerir/entrar) fecha TODAS as posições B3 abertas com o **motivo catalogável**
`MOTIVO_FECHAMENTO_PREGAO="fechamento do pregao (17:30)"` (aparece no /analitico "por motivo"); usa o tick vivo ou,
sem cotação após o pregão, o último close coletado (`_preco_encerramento`). (2) **P&L FLUTUANTE ao vivo** (forex E
B3): coluna `trades.r_atual`/`lucro_atual` (migração idempotente) atualizada a cada ciclo de gestão (só quando o R
arredondado muda — não martela o banco) via `executor._persistir_ao_vivo`. B3 = BRL puro (`config_b3.lucro_brl`);
forex = USD por `usd_por_pip` (calculado UMA vez por posição via `order_calc_profit`, cacheado no dict → não
martela a ponte). O painel (dashboard + `/b3`) mostra **R atual + P&L (USD/BRL)** por posição aberta e o **total
flutuante** (`flutuante_usd`/`flutuante_brl`). Testes em `test_b3` (+7: janela/fechamento/hhmm/minuto/persistência/
fechamento forçado 17:30). **219 testes, todos passando.** ⚠️ Assume que o relógio do servidor da Genial mostra a
hora LOCAL do pregão (mesma premissa do `VWAP_B3_ANCORA_HORA`) — validar com os candles; ajustar os envs se o fuso
diferir.

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
