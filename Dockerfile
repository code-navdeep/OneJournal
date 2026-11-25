# =============================================================================
# Stage 1 — Compile Tailwind CSS with the standalone CLI (no Node required)
# =============================================================================
FROM debian:bookworm-slim AS css-builder

ARG TARGETARCH
WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) TW_ARCH=x64   ;; \
      arm64) TW_ARCH=arm64 ;; \
      *) echo "Unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -sLo /usr/local/bin/tailwindcss \
      "https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-linux-${TW_ARCH}"; \
    chmod +x /usr/local/bin/tailwindcss

# Copy only the files Tailwind needs to scan for class references
COPY tailwind.config.js ./
COPY static/css/input.css ./static/css/input.css
COPY templates/ ./templates/
COPY static/js/ ./static/js/
COPY app.py ./

RUN tailwindcss -i ./static/css/input.css -o ./static/css/app.css --minify

# =============================================================================
# Stage 2 — Python runtime
# =============================================================================
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY . .

# Compiled CSS from stage 1 — overwrites any local copy
COPY --from=css-builder /build/static/css/app.css ./static/css/app.css

RUN mkdir -p data exports

EXPOSE 8090

CMD ["python3", "app.py"]
