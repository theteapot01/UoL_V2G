#!/bin/bash
# =============================================================================
# create_ocpp_certs.sh
#
# Generates the certificate chain for OCPP Security Profile 3 (mutual TLS):
#
#   Root CA
#   ├── CSMS certificate  (server — grid Pi, presented during TLS handshake)
#   └── CP certificate    (client — charger Pi, proves charge point identity)
#
# Both Pis must have the full certs/ocpp/ directory. Run this script once on
# either machine, then copy the output directory to the other Pi.
#
# Usage:
#   ./create_ocpp_certs.sh [CSMS_IP]
#
#   CSMS_IP  IP address of the grid Pi running the CSMS (default: 10.42.0.1).
#            Must match Config.OCPP_SERVER so Python TLS hostname verification
#            passes without disabling security checks.
#
# Output:  certs/ocpp/
#   ca.crt       Root CA certificate  (trusted by both Pis)
#   ca.key       Root CA private key  (keep private — only needed to sign new certs)
#   csms.crt     CSMS server certificate
#   csms.key     CSMS private key     (grid Pi only)
#   cp.crt       Charge Point client certificate
#   cp.key       Charge Point private key  (charger Pi only)
#
# Requirements: openssl >= 1.1.1
# =============================================================================

set -euo pipefail

CSMS_IP="${1:-10.42.0.1}"
OUTDIR="certs/ocpp"

VALIDITY_ROOT=3650   # 10 years
VALIDITY_LEAF=730    #  2 years

SUBJ_BASE="/C=GB/ST=England/L=Leeds/O=UoL V2G Demo"

echo "==> Creating OCPP PKI in ${OUTDIR}/"
echo "    CSMS IP (SAN): ${CSMS_IP}"
mkdir -p "${OUTDIR}"

# ── 1. Root CA ────────────────────────────────────────────────────────────────
echo ""
echo "[1/5] Generating Root CA key..."
openssl genrsa -out "${OUTDIR}/ca.key" 4096

echo "[2/5] Self-signing Root CA certificate..."
openssl req -new -x509 \
    -days "${VALIDITY_ROOT}" \
    -key  "${OUTDIR}/ca.key" \
    -out  "${OUTDIR}/ca.crt" \
    -subj "${SUBJ_BASE}/CN=OCPP Root CA"

# ── 2. CSMS server certificate ────────────────────────────────────────────────
echo ""
echo "[3/5] Generating CSMS certificate (server, IP SAN = ${CSMS_IP})..."
openssl genrsa -out "${OUTDIR}/csms.key" 2048

openssl req -new \
    -key  "${OUTDIR}/csms.key" \
    -out  "${OUTDIR}/csms.csr" \
    -subj "${SUBJ_BASE}/CN=OCPP CSMS"

openssl x509 -req \
    -days "${VALIDITY_LEAF}" \
    -in   "${OUTDIR}/csms.csr" \
    -CA   "${OUTDIR}/ca.crt" \
    -CAkey "${OUTDIR}/ca.key" \
    -CAcreateserial \
    -out  "${OUTDIR}/csms.crt" \
    -extfile <(printf \
        "subjectAltName=IP:%s\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth" \
        "${CSMS_IP}")

# ── 3. Charge Point client certificate ───────────────────────────────────────
echo ""
echo "[4/5] Generating Charge Point client certificate (CN=CP_1)..."
openssl genrsa -out "${OUTDIR}/cp.key" 2048

openssl req -new \
    -key  "${OUTDIR}/cp.key" \
    -out  "${OUTDIR}/cp.csr" \
    -subj "${SUBJ_BASE}/CN=CP_1"

openssl x509 -req \
    -days "${VALIDITY_LEAF}" \
    -in   "${OUTDIR}/cp.csr" \
    -CA   "${OUTDIR}/ca.crt" \
    -CAkey "${OUTDIR}/ca.key" \
    -out  "${OUTDIR}/cp.crt" \
    -extfile <(printf \
        "basicConstraints=CA:FALSE\nkeyUsage=digitalSignature\nextendedKeyUsage=clientAuth")

# ── Cleanup CSRs ─────────────────────────────────────────────────────────────
echo ""
echo "[5/5] Cleaning up CSR files..."
rm -f "${OUTDIR}/csms.csr" "${OUTDIR}/cp.csr"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "Done. Certificate files:"
ls -lh "${OUTDIR}/"
echo ""
echo "Next steps:"
echo "  1. Copy certs/ocpp/ to BOTH Pis under the project root."
echo "  2. The ca.key is only needed for signing; keep it off the Pis in production."
echo "  3. Run grid.py (CSMS) and charger.py (CP) — connection will now use wss://."
