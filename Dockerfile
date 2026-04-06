FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ipmitool && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8777

ENV DATA_DIR=/data

CMD ["python", "-m", "ipmi_fan_curve"]
