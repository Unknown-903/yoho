FROM python:3.12-slim

WORKDIR /app

# Install system dependencies + build tools
RUN apt-get update && apt-get install -y \
    gcc \
    python3-dev \
    build-essential \
    ffmpeg \
    git \
    curl \
    aria2 \
    p7zip-full \
    unzip \
    zip \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY . .

# Create download directories
RUN mkdir -p /app/downloads /app/encodes

CMD ["python", "-m", "bot"]
