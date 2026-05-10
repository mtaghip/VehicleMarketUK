# Microsoft's official Playwright image ships Chromium + all system deps pre-installed.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

# Install Python dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium browser for Playwright
RUN playwright install chromium

# Copy application code
COPY . .

# Create local data dir as fallback (Railway volume overrides this at /data)
RUN mkdir -p /data

EXPOSE 8000

CMD ["python", "main.py"]
