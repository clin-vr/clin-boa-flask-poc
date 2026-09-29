FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gunicorn.conf.py .
COPY evidence_api ./evidence_api
EXPOSE 8080
CMD ["gunicorn", "--config", "gunicorn.conf.py", "evidence_api.app:create_app()"]
