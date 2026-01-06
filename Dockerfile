# Use Node.js LTS on Alpine Linux
FROM node:20-alpine
# Stage 1: Builder
FROM node:20-alpine AS builder

# Install system dependencies
# curl: to download k6
# ca-certificates: for SSL
RUN apk add --no-cache curl ca-certificates python3 py3-pip build-base python3-dev freetype-dev libpng-dev openblas-dev
# Install build dependencies
RUN apk add --no-cache curl python3 py3-pip build-base python3-dev freetype-dev libpng-dev openblas-dev

WORKDIR /app

# Install k6 (Load Testing Tool)
# Downloading the binary directly to ensure specific version
RUN curl -L https://github.com/grafana/k6/releases/download/v0.48.0/k6-v0.48.0-linux-amd64.tar.gz | tar xvz \
    && mv k6-v0.48.0-linux-amd64/k6 /usr/bin/k6 \
    && rm -rf k6-v0.48.0-linux-amd64
    && mv k6-v0.48.0-linux-amd64/k6 /usr/bin/k6

# Create app directory
WORKDIR /app
# Python dependencies (install into virtual environment)
COPY requirements.txt .
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir -r requirements.txt

# Copy package files first to leverage Docker cache
# Node dependencies
COPY package.json package-lock.json* ./

# Install dependencies (including devDependencies for eslint)
RUN npm install && npm prune --production

# Install Python dependencies
COPY requirements.txt ./
RUN pip3 install -r requirements.txt --break-system-packages
# Stage 2: Final
FROM node:20-alpine

# Copy the rest of the application code
# Install runtime dependencies only
RUN apk add --no-cache python3 freetype libpng openblas libstdc++ curl

WORKDIR /app

# Copy artifacts from builder
COPY --from=builder /usr/bin/k6 /usr/bin/k6
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app/node_modules ./node_modules

# Set environment to use venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY . .

# Expose the GUI port
EXPOSE 3000

# Healthcheck to ensure the server is responsive
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 CMD curl -f http://localhost:3000/api/system-health || exit 1

# Start the GUI server
CMD ["python3", "server.py"]