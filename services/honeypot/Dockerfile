# OpenCanary: servicios falsos que avisan cuando alguien los toca.
FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends gcc libffi-dev libssl-dev \
    && pip install --no-cache-dir opencanary \
    && apt-get purge -y gcc && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
COPY opencanary.conf.template entrypoint.sh /etc/opencanaryd/
RUN chmod +x /etc/opencanaryd/entrypoint.sh && mkdir -p /var/log/opencanary
WORKDIR /tmp
ENTRYPOINT ["/etc/opencanaryd/entrypoint.sh"]
