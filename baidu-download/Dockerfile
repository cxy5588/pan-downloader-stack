FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install --with-deps chromium

COPY . .

ENV PORT=8082 \
    DOWNLOAD_PATH=/downloads \
    CAPTURE_DIR=/app/captures

RUN mkdir -p ${DOWNLOAD_PATH} ${CAPTURE_DIR}

EXPOSE 8082

CMD ["gunicorn", "-b", "0.0.0.0:8082", "-w", "4", "app:app"]
