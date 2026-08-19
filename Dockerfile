# Use official Python lightweight image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

# Set working directory
WORKDIR /app

# Install system dependencies if required
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent application code into a package directory
COPY . /app/reporting_agent/

# Expose Cloud Run default port
EXPOSE 8080

# Run agent server using ADK API server pointing to /app
CMD ["sh", "-c", "exec python -m google.adk.cli api_server --host 0.0.0.0 --port ${PORT:-8080} /app"]
