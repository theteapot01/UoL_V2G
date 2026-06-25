#!/usr/bin/env bash
# reliability_test.sh
# ====================
# Inject packet loss on the IEC 104 network interface using Linux tc netem,
# then wait a fixed duration while the operator runs the V2G prototype.
#
# Run this on the GRID PI (IEC 104 client) before starting grid.py.
# The packet loss is applied to outgoing/incoming frames on the interface
# that connects to the charger Pi.
#
# Usage:
#   sudo ./tools/reliability_test.sh [OPTIONS]
#
# Options:
#   -i, --iface    Network interface (default: eth0)
#   -l, --loss     Packet loss percentage (default: 0)
#   -d, --duration Seconds to run the test (default: 300)
#   --remove       Remove any active netem rule and exit
#
# Examples:
#   sudo ./tools/reliability_test.sh --iface eth0 --loss 0  --duration 300
#   sudo ./tools/reliability_test.sh --iface eth0 --loss 5  --duration 300
#   sudo ./tools/reliability_test.sh --iface eth0 --loss 20 --duration 300
#   sudo ./tools/reliability_test.sh --remove --iface eth0
#
# After each run, copy the latest Logs/iec104_*.csv off the grid Pi and
# rename it to encode the loss rate, e.g.:
#   cp $(ls -t Logs/iec104_*.csv | head -1) Logs/iec104_loss0.csv
#
# Then run:
#   python tools/analyse_reliability.py \
#       Logs/iec104_loss0.csv Logs/iec104_loss5.csv Logs/iec104_loss20.csv \
#       --labels "0% loss" "5% loss" "20% loss"

set -euo pipefail

IFACE="eth0"
LOSS_PCT=0
DURATION=300
REMOVE_ONLY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        -i|--iface)    IFACE="$2";     shift 2 ;;
        -l|--loss)     LOSS_PCT="$2";  shift 2 ;;
        -d|--duration) DURATION="$2";  shift 2 ;;
        --remove)      REMOVE_ONLY=1;  shift   ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ── Sanity checks ─────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo "ERROR: must run as root (sudo)." >&2
    exit 1
fi
if ! command -v tc &>/dev/null; then
    echo "ERROR: 'tc' not found. Install iproute2: sudo apt install iproute2" >&2
    exit 1
fi
if ! ip link show "$IFACE" &>/dev/null; then
    echo "ERROR: interface '$IFACE' not found. Available interfaces:" >&2
    ip -o link show | awk -F': ' '{print "  " $2}' >&2
    exit 1
fi

# ── Remove any existing netem rule ────────────────────────────────────────────
_remove_netem() {
    if tc qdisc show dev "$IFACE" | grep -q netem; then
        tc qdisc del dev "$IFACE" root
        echo "[netem] Removed packet-loss rule from $IFACE."
    else
        echo "[netem] No netem rule active on $IFACE."
    fi
}

if [[ $REMOVE_ONLY -eq 1 ]]; then
    _remove_netem
    exit 0
fi

# ── Apply netem ───────────────────────────────────────────────────────────────
if tc qdisc show dev "$IFACE" | grep -q netem; then
    tc qdisc replace dev "$IFACE" root netem loss "${LOSS_PCT}%"
else
    tc qdisc add    dev "$IFACE" root netem loss "${LOSS_PCT}%"
fi

echo ""
echo "======================================================================"
echo "  Packet loss APPLIED: ${LOSS_PCT}% on interface ${IFACE}"
echo "======================================================================"
echo ""
echo "NEXT STEPS:"
echo "  1. On the CHARGER PI: start charger.py (if not already running)"
echo "  2. On the GRID PI:    start grid.py in another terminal:"
echo "       python grid.py"
echo "  3. Let the test run for ${DURATION} seconds."
echo "  4. This script will automatically remove the netem rule when done."
echo ""
echo "Starting ${DURATION}-second timer..."
echo ""

# Countdown
for ((i=DURATION; i>0; i--)); do
    if (( i % 30 == 0 )); then
        echo "  ${i}s remaining..."
    fi
    sleep 1
done

# ── Clean up ──────────────────────────────────────────────────────────────────
_remove_netem
echo ""
echo "======================================================================"
echo "  Test complete. Packet loss removed."
echo "======================================================================"
echo ""
echo "Find the IEC 104 log for this run:"
LATEST=$(ls -t Logs/iec104_*.csv 2>/dev/null | head -1 || echo "")
if [[ -n "$LATEST" ]]; then
    echo "  $LATEST"
    LABEL="loss${LOSS_PCT}"
    DEST="${LATEST%%_[0-9]*}_${LABEL}.csv"
    echo ""
    echo "Rename it to encode the loss rate:"
    echo "  cp $LATEST Logs/iec104_${LABEL}.csv"
else
    echo "  No iec104 CSV found in Logs/ yet."
fi
echo ""
echo "Repeat for each loss scenario (0%, 5%, 20%), then run:"
echo "  python tools/analyse_reliability.py \\"
echo "      Logs/iec104_loss0.csv Logs/iec104_loss5.csv Logs/iec104_loss20.csv \\"
echo "      --labels \"0% loss\" \"5% loss\" \"20% loss\""
