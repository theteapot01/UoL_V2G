#!/bin/bash
# =============================================================================
# create_iec104_certs.sh
#
# Generates the certificate chain for IEC 60870-5-104 transport security
# per IEC 62351-3 (TLS for TCP/IP-based protocols):
#
#   Root CA
#   ├── Server certificate  (charger Pi — the IEC 104 controlled station)
#   └── Client certificate  (grid Pi   — the IEC 104 controlling station)
#
# Both Pis must have the full certs/iec104/ directory. Run this script once
# on either machine, then copy the output directory to the other Pi.
#
# Usage:
#   ./create_iec104_certs.sh
#
# Output:  certs/iec104/
#   ca.crt        Root CA certificate    (trusted by both Pis)
#   ca.key        Root CA private key    (keep off the Pis in production)
#   server.crt    IEC 104 server certificate  (charger Pi)
#   server.key    IEC 104 server private key  (charger Pi only)
#   client.crt    IEC 104 client certificate  (grid Pi)
#   client.key    IEC 104 client private key  (grid Pi only)
#
# Standard port for IEC 104 over TLS (IEC 62351-3): 19998
#
# Requirements: openssl >= 1.1.1
#
# Format note: matches the c104 reference gen-certs.sh exactly — RSA-4096,
# no X.509 extensions.  The minimal mbedTLS bundled in the c104 Pi wheel
# fails to parse several extension types (AKI, EKU, SAN) and silently
# drops keys/chains that contain them, causing every handshake to fail.
# =============================================================================

set -euo pipefail

OUTDIR="certs/iec104"

echo "==> Creating IEC 104 TLS PKI (IEC 62351-3) in ${OUTDIR}/"
mkdir -p "${OUTDIR}"

echo ""
echo "[1/3] Root CA..."
openssl genrsa -out "${OUTDIR}/ca.key" 4096
openssl req -x509 -new -nodes \
    -key  "${OUTDIR}/ca.key" \
    -sha256 \
    -subj '/CN=IEC104 CA' \
    -days 3650 \
    -out  "${OUTDIR}/ca.crt"

echo ""
echo "[2/3] Server certificate (charger Pi)..."
openssl genrsa -out "${OUTDIR}/server.key" 4096
openssl req -new \
    -key  "${OUTDIR}/server.key" \
    -out  "${OUTDIR}/server.csr" \
    -subj '/CN=IEC104 Server'
openssl x509 -req \
    -in   "${OUTDIR}/server.csr" \
    -CA   "${OUTDIR}/ca.crt" \
    -CAkey "${OUTDIR}/ca.key" \
    -CAcreateserial \
    -out  "${OUTDIR}/server.crt" \
    -sha256 \
    -days 3650

echo ""
echo "[3/3] Client certificate (grid Pi)..."
openssl genrsa -out "${OUTDIR}/client.key" 4096
openssl req -new \
    -key  "${OUTDIR}/client.key" \
    -out  "${OUTDIR}/client.csr" \
    -subj '/CN=IEC104 Client'
openssl x509 -req \
    -in   "${OUTDIR}/client.csr" \
    -CA   "${OUTDIR}/ca.crt" \
    -CAkey "${OUTDIR}/ca.key" \
    -CAcreateserial \
    -out  "${OUTDIR}/client.crt" \
    -sha256 \
    -days 3650

rm -f "${OUTDIR}/server.csr" "${OUTDIR}/client.csr"

echo ""
echo "Done. Certificate files:"
ls -lh "${OUTDIR}/"
echo ""
echo "Next steps:"
echo "  1. Copy certs/iec104/ to BOTH Pis under the project root."
echo "  2. The ca.key is only needed for signing; keep it off the Pis in production."
