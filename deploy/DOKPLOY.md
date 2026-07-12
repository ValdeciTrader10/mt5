# Deploy no Dokploy — passo a passo

Sua VPS já roda o Dokploy (`deploy.empenhocontabilidade.com.br`) com bastante folga
(16 GB RAM, ~11 GB livres; 192 GB de disco). Dá para subir o sistema Forex tranquilo.

O Dokploy já cuida de proxy e HTTPS, então **não usamos o Caddy** aqui. Usamos o compose
`deploy/docker-compose.dokploy.yml` (sem Caddy; painel publicado numa porta para acesso por IP).

---

## 1. Criar o serviço no Dokploy

1. Entre em `deploy.empenhocontabilidade.com.br`.
2. **Create Project** (ou abra um existente). Sugestão de nome: **Forex M5**.
3. Dentro do projeto: **Create Service → Compose** (Docker Compose).
4. **Provider / Source:**
   - Se seu GitHub já está conectado ao Dokploy: escolha **GitHub**, repositório
     `ValdeciTrader10/mt5`, branch **`claude/hostinger-vps-docker-web-lqpmk2`**.
   - Se não estiver conectado (repo privado): conecte o GitHub em *Settings → Git*, ou
     use a opção **Git** com a URL do repositório + um token de acesso.
5. **Compose Path:** `deploy/docker-compose.dokploy.yml`

## 2. Variáveis de ambiente (aba Environment)

Cole isto na aba **Environment** do serviço, trocando o que estiver entre `< >`:

```
VNC_USER=trader
VNC_PASSWORD=<escolha-uma-senha-para-abrir-o-mt5>

PAINEL_USUARIO=admin
PAINEL_SENHA=<escolha-a-senha-do-painel>
SECRET_KEY=8d44ed544112592315b8336272d230b2d0b31908b53bbc2c9d674caff49a6cf6

MT5_LOGIN=84110577
MT5_SERVER=XMGlobal-MT5 4
LOG_LEVEL=DEBUG
```

> `SECRET_KEY` acima foi gerada aleatoriamente para você (assina o cookie de login).
> Se preferir senha do painel com hash bcrypt em vez de texto, use `PAINEL_SENHA_HASH`
> no lugar de `PAINEL_SENHA` (gere com `python -m sistema_forex.scripts.gerar_hash`).

## 3. Deploy

Clique em **Deploy**. O primeiro deploy baixa a imagem do MT5 e compila a imagem do
sistema — leva ~5 a 10 min. Acompanhe pelos **Logs** do serviço.

## 4. Logar no MT5 (uma vez)

O terminal MT5 abre direto no navegador pelo IP da VPS:

```
http://IP_DA_VPS:3100
```

Ele pede usuário/senha do VNC (o `CUSTOM_USER`/`VNC_PASSWORD` do Environment). Depois
aparece o terminal MT5: faça login na conta **XM demo** (login 84110577, servidor
`XMGlobal-MT5 4`), habilite **Algo Trading** e deixe conectado. O volume `mt5_config`
guarda esse login entre reinícios. Assim que o MT5 estiver logado, o coletor inicia o
backfill de 6 meses automaticamente.

> Segurança: a porta 3100 fica protegida só pela senha do VNC. Para uma conta demo tudo
> bem. Se quiser fechar depois, troque no compose `"3100:3000"` por `"127.0.0.1:3100:3000"`
> e passe a acessar por túnel SSH (`ssh -L 3100:localhost:3100 usuario@IP_DA_VPS`).

## 5. Acessar o painel

Enquanto não há domínio, o painel abre por IP:

```
http://IP_DA_VPS:8090
```

Login: `admin` / a senha que você definiu em `PAINEL_SENHA`.

> ⚠️ Por IP é **HTTP (sem cadeado)** — a senha trafega sem criptografia. Serve para
> validar. Antes de usar de verdade, faça o passo 6 (domínio + HTTPS).

## 6. (Depois) Domínio + HTTPS de verdade

Quando quiser o painel em `https://forex.empenhocontabilidade.com.br` com cadeado:

1. No seu DNS, crie um registro **A**: `forex` → IP da VPS.
2. No serviço, aba **Domains → Add Domain**: `forex.empenhocontabilidade.com.br`,
   **Container Port `8000`**, HTTPS/Let's Encrypt ligado.
3. Pode remover a publicação `8090` do compose (a linha `ports:` do serviço `web`).

O Dokploy emite o certificado automaticamente.

---

## Operação e diagnóstico

- **Logs** de cada container: aba Logs do serviço (veja `coletor` para a coleta em tempo real).
- Se o painel mostrar **MT5 Offline**: o terminal ainda não está logado (passo 4) ou o
  container `mt5` ainda está subindo (primeiro boot demora).
- **Redeploy** após novos commits: botão **Deploy** de novo (ou ative auto-deploy por webhook).
- Portas usadas por este stack no host: **8090** (painel) e **3100** (VNC, só localhost).
  A API Python do MT5 (8001) nunca sai da rede interna.
