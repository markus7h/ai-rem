FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Embedding-Modell beim Build ins Image-Layer backen (kein Runtime-Download).
# Modellname muss EMBED_MODEL in server.py entsprechen.
ENV FASTEMBED_CACHE_PATH=/opt/fastembed-cache
ENV EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='${EMBED_MODEL}')"

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
