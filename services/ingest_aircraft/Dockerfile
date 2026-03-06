FROM python:3.11-slim

WORKDIR /app

# Install common requirements
COPY libs/common/requirements.txt /app/libs/common/requirements.txt
RUN pip install --no-cache-dir -r /app/libs/common/requirements.txt

# Install service requirements
COPY services/ingest_aircraft/requirements.txt /app/services/ingest_aircraft/requirements.txt
RUN pip install --no-cache-dir -r /app/services/ingest_aircraft/requirements.txt

# Copy common libs and service code
COPY libs /app/libs
COPY services/ingest_aircraft /app/services/ingest_aircraft

ENV PYTHONPATH="/app"

CMD ["python", "services/ingest_aircraft/main.py"]
