#!/bin/sh
# Refuse to start when a non-loopback proxy is trusted without a long shared secret.
if [ "${TRUSTED_PROXY_IP}" != "127.0.0.1" ] && [ "${#PROXY_SECRET}" -lt 32 ]; then
    echo "gate: PROXY_SECRET (32+ chars) is required when TRUSTED_PROXY_IP is set" >&2
    exit 1
fi
case "${PROXY_SECRET}" in
    *[!A-Za-z0-9]*) echo "gate: PROXY_SECRET must be letters and digits only" >&2; exit 1 ;;
esac
