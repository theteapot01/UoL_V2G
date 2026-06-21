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
#   ./create_iec104_certs.sh [CHARGER_IP]
#
#   CHARGER_IP  IP address of the charger Pi running the IEC 104 server
#               (default: 10.42.0.23, matches Config.IP_ADDRESS).
#               Embedded as an IP SAN so the client can verify the server
#               without disabling certificate checks.
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
# =============================================================================

set -euo pipefail

CHARGER_IP="${1:-10.42.0.23}"
OUTDIR="certs/iec104"

VALIDITY_ROOT=3650   # 10 years
VALIDITY_LEAF=730    #  2 years

SUBJ_BASE="/C=GB/ST=England/L=Leeds/O=UoL V2G Demo"

echo "==> Creating IEC 104 TLS PKI (IEC 62351-3) in ${OUTDIR}/"
echo "    IEC 104 server IP (SAN): ${CHARGER_IP}"
mkdir -p "${OUTDIR}"

# ── 1. Root CA ────────────────────────────────────────────────────────────────
# Use RSA keys (openssl genrsa) rather than ECDSA.  The c104 pip wheel for
# Raspberry Pi bundles a minimal mbedTLS build that may have ECDSA support
# compiled out.  When ECDSA is unavailable, EC keys are silently ignored,
# the TLS context has no private key, and every handshake fails even with
# validate=False.  RSA-2048 is universally supported in all mbedTLS builds.
echo ""
echo "[1/5] Generating Root CA key (RSA-4096)..."
openssl genrsa -out "${OUTDIR}/ca.key" 4096

echo "[2/5] Self-signing Root CA certificate..."
openssl req -new -x509 \
    -days "${VALIDITY_ROOT}" \
    -key  "${OUTDIR}/ca.key" \
    -out  "${OUTDIR}/ca.crt" \
    -subj "${SUBJ_BASE}/CN=IEC104 Root CA" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "subjectKeyIdentifier=hash" \
    -addext "keyUsage=critical,keyCertSign,cRLSign"

# ── 2. Server certificate (charger Pi — IEC 104 controlled station) ───────────
echo ""
echo "[3/5] Generating IEC 104 server certificate (charger Pi, IP SAN = ${CHARGER_IP})..."
openssl genrsa -out "${OUTDIR}/server.key" 2048

openssl req -new \
    -key  "${OUTDIR}/server.key" \
    -out  "${OUTDIR}/server.csr" \
    -subj "${SUBJ_BASE}/CN=IEC104 Server"

openssl x509 -req \
    -days "${VALIDITY_LEAF}" \
    -in   "${OUTDIR}/server.csr" \
    -CA   "${OUTDIR}/ca.crt" \
    -CAkey "${OUTDIR}/ca.key" \
    -CAcreateserial \
    -out  "${OUTDIR}/server.crt" \
    -extfile <(printf \
        "subjectAltName=IP:%s\nbasicConstraints=CA:FALSE\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\nkeyUsage=digitalSignature\nextendedKeyUsage=serverAuth" \
        "${CHARGER_IP}")

# ── 3. Client certificate (grid Pi — IEC 104 controlling station) ─────────────
echo ""
echo "[4/5] Generating IEC 104 client certificate (grid Pi)..."
openssl genrsa -out "${OUTDIR}/client.key" 2048

openssl req -new \
    -key  "${OUTDIR}/client.key" \
    -out  "${OUTDIR}/client.csr" \
    -subj "${SUBJ_BASE}/CN=IEC104 Client"

openssl x509 -req \
    -days "${VALIDITY_LEAF}" \
    -in   "${OUTDIR}/client.csr" \
    -CA   "${OUTDIR}/ca.crt" \
    -CAkey "${OUTDIR}/ca.key" \
    -out  "${OUTDIR}/client.crt" \
    -extfile <(printf \
        "basicConstraints=CA:FALSE\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\nkeyUsage=digitalSignature\nextendedKeyUsage=clientAuth")

# ── Cleanup CSRs ─────────────────────────────────────────────────────────────
echo ""
echo "[5/5] Cleaning up CSR files..."
rm -f "${OUTDIR}/server.csr" "${OUTDIR}/client.csr"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Done. Certificate files:"
ls -lh "${OUTDIR}/"
echo ""
echo "Next steps:"
echo "  1. Copy certs/iec104/ to BOTH Pis under the project root."
echo "  2. The ca.key is only needed for signing; keep it off the Pis in production."
echo "  3. IEC 104 now listens on port 19998 (IEC 62351-3 standard TLS port)."
