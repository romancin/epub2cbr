# epub2cbr - EPUB to CBR/CBZ Converter

FROM python:3.11-slim

LABEL maintainer="romancin"
LABEL description="Convert EPUB comic/manga files to CBR/CBZ archives"

# Install system dependencies
# hadolint ignore=DL3008
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Chrome/Chromium for gowitness
    chromium \
    chromium-driver \
    # For downloading binaries
    wget \
    ca-certificates \
    # ZIP for CBZ creation (universal fallback)
    zip \
    # Fonts for proper text rendering
    fonts-liberation \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Download gowitness pre-compiled binary (much faster than compiling)
ENV GOWITNESS_VERSION=3.1.1
RUN set -ex && \
    ARCH=$(dpkg --print-architecture) && \
    echo "Architecture: $ARCH" && \
    if [ "$ARCH" = "amd64" ]; then \
        GOWITNESS_ARCH="linux-amd64"; \
    elif [ "$ARCH" = "arm64" ]; then \
        GOWITNESS_ARCH="linux-arm64"; \
    else \
        GOWITNESS_ARCH="linux-arm"; \
    fi && \
    wget -q "https://github.com/sensepost/gowitness/releases/download/${GOWITNESS_VERSION}/gowitness-${GOWITNESS_VERSION}-${GOWITNESS_ARCH}" \
        -O /usr/local/bin/gowitness && \
    chmod +x /usr/local/bin/gowitness

# Download and install RAR from rarlab (x64 only - no ARM Linux version available)
# On ARM, we'll use ZIP/CBZ format instead
RUN set -ex && \
    ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "amd64" ]; then \
        wget -q "https://www.rarlab.com/rar/rarlinux-x64-712.tar.gz" -O /tmp/rar.tar.gz && \
        tar -xzf /tmp/rar.tar.gz -C /tmp && \
        cp /tmp/rar/rar /usr/local/bin/ && \
        cp /tmp/rar/unrar /usr/local/bin/ && \
        rm -rf /tmp/rar /tmp/rar.tar.gz; \
    else \
        echo "RAR not available for $ARCH, will use ZIP/CBZ format"; \
    fi

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
