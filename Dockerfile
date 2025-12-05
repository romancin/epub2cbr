# epub2cbr - EPUB to CBR/CBZ Converter
# Multi-stage build for smaller final image

FROM golang:1.23-alpine AS gowitness-builder

# Install git (required for go install)
# hadolint ignore=DL3018
RUN apk add --no-cache git

# Install gowitness - using @latest as gowitness doesn't use semantic versioning with v prefix
# hadolint ignore=DL3059,DL3062
RUN GOTOOLCHAIN=auto go install github.com/sensepost/gowitness@latest

FROM python:3.11-slim

LABEL maintainer="romancin"
LABEL description="Convert EPUB comic/manga files to CBR/CBZ archives"

# Install system dependencies
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Chrome/Chromium for gowitness
    chromium \
    chromium-driver \
    # For downloading RAR
    wget \
    # ZIP for CBZ creation (universal fallback)
    zip \
    # Fonts for proper text rendering
    fonts-liberation \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Download and install RAR from rarlab (x64 only - no ARM Linux version available)
# On ARM, we'll use ZIP/CBZ format instead
RUN set -ex && \
    ARCH=$(dpkg --print-architecture) && \
    echo "Architecture: $ARCH" && \
    if [ "$ARCH" = "amd64" ]; then \
        wget -q "https://www.rarlab.com/rar/rarlinux-x64-712.tar.gz" -O /tmp/rar.tar.gz && \
        tar -xzf /tmp/rar.tar.gz -C /tmp && \
        cp /tmp/rar/rar /usr/local/bin/ && \
        cp /tmp/rar/unrar /usr/local/bin/ && \
        rm -rf /tmp/rar /tmp/rar.tar.gz; \
    else \
        echo "RAR not available for $ARCH, will use ZIP/CBZ format"; \
    fi

# Copy gowitness from builder
COPY --from=gowitness-builder /go/bin/gowitness /usr/local/bin/gowitness

# Set Chrome path for gowitness
ENV CHROME_PATH=/usr/bin/chromium

# Create app directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY epub2cbr.py .
RUN chmod +x epub2cbr.py && mkdir -p /input /output

# Set working directory for conversions
WORKDIR /data

# Default command shows help
ENTRYPOINT ["python", "/app/epub2cbr.py"]
CMD ["--help"]
