FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["sh", "-c", "python generate_samples.py && exec gunicorn --bind 0.0.0.0:8000 --workers 1 'app:create_app()'"]
