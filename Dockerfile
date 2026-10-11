# Backend API container: FastAPI + psycopg + model stack.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY migrations/ migrations/
COPY *.py .

EXPOSE 8010
# migrations are applied by entrypoint loop; see docker-compose command
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010"]
