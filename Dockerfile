FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-embed.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# local  = fastembed + Modell im Image (Default, laeuft ohne externen Dienst)
# external = beides weg (~250 MB kleiner, 413 → 162 MB), semantische Suche braucht
#            dann EMBED_URL.
# Der Wert steuert nur den Build; zur Laufzeit entscheidet EMBED_URL.
ARG EMBED_BACKEND=local

# Embedding-Modell beim Build ins Image-Layer backen (kein Runtime-Download).
# Modellname muss EMBED_MODEL in server.py entsprechen.
ENV FASTEMBED_CACHE_PATH=/opt/fastembed-cache
ENV EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
RUN if [ "$EMBED_BACKEND" = "local" ]; then \
        pip install --no-cache-dir -r requirements-embed.txt && \
        python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='${EMBED_MODEL}')"; \
    else \
        echo "EMBED_BACKEND=${EMBED_BACKEND} — fastembed und Modell werden ausgelassen"; \
    fi

COPY server.py .
COPY setup-config*.json ./
# mcp_client/extractor braucht der Server nicht — sie gehoeren zur CLI und werden
# ueber /lib/<name> ausgeliefert, damit das Setup eine lauffaehige CLI installiert.
COPY lib/ ./lib/
COPY assets/favicon.png assets/favicon-dark.png assets/logo.png ./assets/
# Hooks, Setup-Script und HTML-Templates lagen frueher als String-Literale in
# server.py. server.py liest sie beim Import — fehlen sie, startet der Container
# gar nicht erst (statt spaeter einzelne Routen zu verlieren).
COPY bin/ ./bin/
COPY hooks/ ./hooks/
COPY scripts/ ./scripts/
COPY templates/ ./templates/

VOLUME /data

EXPOSE 3456

CMD ["python", "server.py"]
