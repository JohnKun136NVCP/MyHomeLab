#!/bin/sh
# Escucha solo en la IP del laboratorio: nunca en la red de la familia ni en internet.
set -e
: "${HONEYPOT_IP:?Pon HONEYPOT_IP (IP de la Pi en el laboratorio) en .env}"
sed -e "s/__LISTEN__/${HONEYPOT_IP}/" -e "s/__NODE__/${HONEYPOT_NODE:-lab-canary}/" \
    /etc/opencanaryd/opencanary.conf.template > /etc/opencanaryd/opencanary.conf
echo "Honeypot escuchando en ${HONEYPOT_IP}"
exec opencanaryd --dev
