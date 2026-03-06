FROM python:3.11-slim

WORKDIR /app

# Install common requirements
COPY libs/common/requirements.txt /app/libs/common/requirements.txt
RUN pip install --no-cache-dir -r /app/libs/common/requirements.txt

# Install service requirements
COPY services/api/requirements.txt /app/services/api/requirements.txt
RUN pip install --no-cache-dir -r /app/services/api/requirements.txt

# Copy common libs and service code
COPY libs /app/libs
COPY services/api /app/services/api

ENV PYTHONPATH="/app"

CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
