FROM python:3.11-slim

WORKDIR /app

# Install common requirements
COPY libs/common/requirements.txt /app/libs/common/requirements.txt
RUN pip install --no-cache-dir -r /app/libs/common/requirements.txt

# Install service requirements
COPY services/propagate_orbits/requirements.txt /app/services/propagate_orbits/requirements.txt
RUN pip install --no-cache-dir -r /app/services/propagate_orbits/requirements.txt

# Copy common libs and service code
COPY libs /app/libs
COPY services/propagate_orbits /app/services/propagate_orbits

ENV PYTHONPATH="/app"

CMD ["python", "services/propagate_orbits/main.py"]
