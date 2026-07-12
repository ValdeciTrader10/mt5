# Imagem única usada pelos serviços "web" e "coletor".
# O MT5 NÃO roda aqui — roda no container "mt5" (gmag11/metatrader5_vnc), sob Wine.
# Python 3.13: exigido pelo cliente pymt5linux (ponte RPyC com o MT5).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DADOS_DIR=/dados

WORKDIR /app

# Dependências primeiro (cache de camadas).
COPY sistema_forex/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Código
COPY sistema_forex /app/sistema_forex

RUN mkdir -p /dados
VOLUME ["/dados"]

# Default: coletor. O serviço "web" sobrescreve o command no compose.
CMD ["python", "-m", "sistema_forex.coletor_mt5"]
