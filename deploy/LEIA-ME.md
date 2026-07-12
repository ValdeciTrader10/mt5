# Deploy na VPS Hostinger — passo a passo

Tudo roda em Docker na própria VPS. Não depende da VPS Windows.

## Pré-requisitos

- VPS Hostinger **Linux amd64** com Docker + Docker Compose. Recomendado **KVM 2 (8 GB RAM)**;
  KVM 1 (4 GB) funciona apertado (o MT5 sob Wine é o que mais consome).
- Instalar Docker (se ainda não tiver):
  ```bash
  curl -fsSL https://get.docker.com | sh
  ```

## 1. Preparar variáveis

```bash
cd deploy
cp .env.example .env
```

No `.env`, preencha:

- `PAINEL_SENHA_HASH` — gere o hash da sua senha do painel:
  ```bash
  docker run --rm -it python:3.11-slim bash -c "pip install bcrypt -q && python -c \"import bcrypt,getpass;print(bcrypt.hashpw(getpass.getpass('senha: ').encode(),bcrypt.gensalt()).decode())\""
  ```
  (ou, com o projeto instalado: `python -m sistema_forex.scripts.gerar_hash`)
- `SECRET_KEY` — `python -c "import secrets; print(secrets.token_hex(32))"`
- `VNC_PASSWORD` — senha para abrir o terminal MT5 no navegador.
- `MT5_LOGIN` / `MT5_SERVER` — sua conta XM demo (referência).

## 2. Subir o stack

```bash
docker compose up -d --build
```

O container `mt5` no **primeiro boot** baixa e instala o MT5 sob Wine (~5 min). Acompanhe:

```bash
docker compose logs -f mt5
```

## 3. Logar no MT5 (uma vez)

O terminal MT5 fica exposto **apenas no localhost** por segurança. Do seu computador,
abra um túnel SSH para a VPS e acesse pelo navegador:

```bash
ssh -L 3000:localhost:3000 usuario@IP_DA_VPS
# depois abra no navegador:  http://localhost:3000
```

Faça login (login 336082748, servidor `XMGlobal-MT5 9`, conta **demo**), habilite
**Algo Trading** (botão na barra) e deixe o terminal conectado. O volume `mt5_config`
guarda esse login entre reinícios.

> Alternativa sem SSH: exponha a porta 3000 atrás do Caddy com basic-auth. Não é o
> padrão porque o proxy de subpath do VNC é frágil; o túnel SSH é mais simples e seguro.

## 4. Acessar o painel

No navegador: `https://IP_DA_VPS`
(aviso de certificado autoassinado é esperado — prossiga). Faça login com
`PAINEL_USUARIO` / a senha que você gerou.

O painel mostra: estado da conexão MT5, contagem de candles por par/TF (o coletor já
começa o backfill de 6 meses assim que o MT5 estiver logado), spread médio e o gráfico.

## Operação

```bash
docker compose ps                 # estado dos serviços
docker compose logs -f coletor    # ver a coleta em tempo real (logs DEBUG)
docker compose restart coletor    # reiniciar só o coletor
docker compose down               # parar tudo (dados persistem nos volumes)
```

## Segurança

- Porta **8001** (API Python do MT5) **nunca** sai da rede interna do Docker.
- Porta **3000** (terminal) só no localhost → túnel SSH.
- Painel sempre atrás de login + HTTPS (Caddy).
- Firewall recomendado na VPS: liberar só 22 (SSH), 80 e 443.
  ```bash
  ufw allow 22 && ufw allow 80 && ufw allow 443 && ufw enable
  ```
