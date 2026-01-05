# Use Node.js LTS on Alpine Linux
FROM node:20-alpine

# Install system dependencies
# curl: to download k6
# ca-certificates: for SSL
RUN apk add --no-cache curl ca-certificates python3 py3-pip build-base python3-dev freetype-dev libpng-dev openblas-dev

# Install k6 (Load Testing Tool)
# Downloading the binary directly to ensure specific version
RUN curl -L https://github.com/grafana/k6/releases/download/v0.48.0/k6-v0.48.0-linux-amd64.tar.gz | tar xvz \
    && mv k6-v0.48.0-linux-amd64/k6 /usr/bin/k6 \
    && rm -rf k6-v0.48.0-linux-amd64

# Create app directory
WORKDIR /app

# Copy package files first to leverage Docker cache
COPY package.json package-lock.json* ./

# Install dependencies (including devDependencies for eslint)
RUN npm install && npm prune --production

# Install Python dependencies
COPY requirements.txt ./
RUN pip3 install -r requirements.txt --break-system-packages

# Copy the rest of the application code
COPY . .

# Expose the GUI port
EXPOSE 3000

# Start the GUI server
CMD ["python3", "server.py"]