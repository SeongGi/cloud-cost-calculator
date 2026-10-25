#!/bin/sh
# Refuse to start when a non-loopback proxy is trusted without a long shared secret.
if [ "${TRUSTED_PROXY_IP}" != "127.0.0.1" ] && [ "${#PROXY_SECRET}" -lt 32 ]; then
    echo "gate: PROXY_SECRET (32+ chars) is required when TRUSTED_PROXY_IP is set" >&2
    exit 1
fi
# Admin pages: an empty secret would let the app accept nothing (safe), a short one is refused.
if [ -n "${ADMIN_GATE_SECRET}" ] && [ "${#ADMIN_GATE_SECRET}" -lt 32 ]; then
    echo "gate: ADMIN_GATE_SECRET must be 32+ letters/digits" >&2
    exit 1
fi
case "${ADMIN_GATE_SECRET}" in
    *[!A-Za-z0-9]*) echo "gate: ADMIN_GATE_SECRET must be letters and digits only" >&2; exit 1 ;;
esac
case "${PROXY_SECRET}" in
    *[!A-Za-z0-9]*) echo "gate: PROXY_SECRET must be letters and digits only" >&2; exit 1 ;;
esac
