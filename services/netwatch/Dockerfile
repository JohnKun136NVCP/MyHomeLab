# Contexto de construcción: services/
FROM python:3.15-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
        arp-scan nmap iputils-ping iproute2 nftables ieee-data network-manager samba-common-bin wireguard-tools \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY netwatch/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common/homelab_common.py /app/
COPY netwatch/netwatch /app/netwatch
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "netwatch"]
