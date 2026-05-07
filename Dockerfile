FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# System deps (minimal but enough)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    portaudio19-dev \
    libasound2-dev \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip
RUN pip install --upgrade pip

# 🔥 Copy ONLY requirements first (cache layer)
COPY requirements.txt .

# Install deps (cached unless requirements.txt changes)
RUN pip install --no-cache-dir -r requirements.txt

# Copy rest of app AFTER deps
COPY . .

EXPOSE 8010

CMD ["python", "webrtc/webrtc_agent.py", "--transport", "webrtc","--host", "0.0.0.0", "--port", "8010"]
# CMD ["python", "smartflo_agent.py", "--transport", "twilio", "--host", "0.0.0.0", "--port", "8010"]
# CMD ["python", "webrtc/webrtc_agent.py", "--transport", "webrtc", "--host", "0.0.0.0", "--port", "9001"]