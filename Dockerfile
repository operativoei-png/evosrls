FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV FLASK_APP=app.py
ENV PYTHONUNBUFFERED=1
CMD ["gunicorn", "--bind", "0.0.0.0:$PORT", "app:app"]
