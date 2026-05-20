#!/usr/bin/env bash
# doze-repro-test.sh — Reproduce Android Doze network block on Broker auth
#
# This script tests that the Android Broker (Authenticator/BrokerHost) fails
# with a DNS/network error when a background caller triggers silent auth
# during Doze mode. It uses the SilentAuthReceiver in MsalTestApp to invoke
# acquireTokenSilent from a BroadcastReceiver (PROCESS_STATE_RECEIVER),
# which does NOT elevate the Broker's process importance enough to get a
# dozable-allow firewall rule from NetworkPolicyManagerService.
#
# Root cause (validated via AOSP source + on-device netpolicy dump):
#   When a foreground app binds to Broker via IPC, Android's
#   NetworkPolicyManagerService dynamically adds a dozable-allow firewall
#   rule for the Broker's UID. When the caller is in a background context
#   (BroadcastReceiver handling FCM), the binding does NOT elevate the
#   Broker enough — Doze firewall blocks its DNS resolution.
#
# Prerequisites (manual, one-time):
#   1. Install exactly ONE broker app (Authenticator, Company Portal,
#      BrokerHost, or Link to Windows)
#   2. Install MsalTestApp (must have SilentAuthReceiver registered)
#   3. Sign in to an account in MsalTestApp via interactive auth
#   4. Connect device via USB with adb authorized
#
# Usage:
#   bash scripts/doze-repro-test.sh
#
# Expected result:
#   AUTH FAILED with either:
#     - io_error: Doze firewall blocked Broker's DNS resolution
#     - device_network_not_available_doze_mode: Broker's proactive Doze check
#   TOKEN ACQUIRED if broker is battery-exempt (DeviceIdle whitelist)
#
set -euo pipefail

RECEIVER_PKG="com.msft.identity.client.sample.local"
RECEIVER_CLASS="com.microsoft.identity.client.testapp.SilentAuthReceiver"
RECEIVER_ACTION="com.microsoft.identity.client.testapp.SILENT_AUTH"
WAIT_SECONDS=15
TAG="SilentAuthReceiver"
DOZE_FORCED=false

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "============================================="
echo " Doze Network Block Reproduction Test"
echo "============================================="
echo ""

# --- Prerequisite checks ---
echo "[1/8] Checking adb connection..."
if ! adb devices 2>/dev/null | grep -q "device$"; then
    echo -e "${RED}ERROR: No adb device connected.${NC}"
    echo "Connect a device via USB and authorize adb."
    exit 1
fi
SERIAL=$(adb devices | grep "device$" | head -1 | awk '{print $1}')
if [ -z "$SERIAL" ]; then
    echo -e "${RED}ERROR: Failed to resolve adb device serial.${NC}"
    exit 1
fi
echo "  Device: $SERIAL"
ADB=(adb -s "$SERIAL")

adb_cmd() {
    "${ADB[@]}" "$@"
}

cleanup() {
    if [ "$DOZE_FORCED" = true ]; then
        echo "[8/8] Cleaning up Doze..."
        adb_cmd shell dumpsys deviceidle unforce > /dev/null 2>&1 || true
        adb_cmd shell dumpsys battery reset > /dev/null 2>&1 || true
        DOZE_FORCED=false
        echo "  Doze reset to ACTIVE"
    fi
}

trap cleanup EXIT INT TERM

echo "[2/8] Checking SilentAuthReceiver is registered..."
if ! adb_cmd shell "dumpsys package $RECEIVER_PKG" 2>/dev/null | grep -q "SilentAuthReceiver"; then
    echo -e "${RED}ERROR: SilentAuthReceiver not found in $RECEIVER_PKG.${NC}"
    echo "Install MsalTestApp."
    exit 1
fi
echo "  SilentAuthReceiver: registered"

echo "[3/8] Checking broker apps installed..."
BROKER_PKGS=(
    "com.azure.authenticator"
    "com.microsoft.windowsintune.companyportal"
    "com.microsoft.identity.testuserapp"
    "com.microsoft.appmanager"
)
BROKER_NAMES=(
    "Microsoft Authenticator"
    "Company Portal"
    "BrokerHost (test)"
    "Link to Windows"
)
INSTALLED_BROKERS=()
INSTALLED_NAMES=()
for i in "${!BROKER_PKGS[@]}"; do
    if adb_cmd shell pm list packages -e 2>/dev/null | grep -q "${BROKER_PKGS[$i]}"; then
        INSTALLED_BROKERS+=("${BROKER_PKGS[$i]}")
        INSTALLED_NAMES+=("${BROKER_NAMES[$i]}")
    fi
done
if [ ${#INSTALLED_BROKERS[@]} -eq 0 ]; then
    echo -e "${RED}ERROR: No broker app installed.${NC}"
    echo "Install one of: Authenticator, Company Portal, BrokerHost, or Link to Windows."
    exit 1
fi
if [ ${#INSTALLED_BROKERS[@]} -gt 1 ]; then
    echo -e "${RED}ERROR: Multiple broker apps installed (${#INSTALLED_BROKERS[@]}):${NC}"
    for i in "${!INSTALLED_BROKERS[@]}"; do
        echo "  - ${INSTALLED_NAMES[$i]} (${INSTALLED_BROKERS[$i]})"
    done
    echo ""
    echo "Uninstall all but one broker to get a deterministic test."
    echo "With multiple brokers, MSAL's broker discovery picks one at runtime"
    echo "and the test can't guarantee which broker handles the request."
    exit 1
fi
BROKER="${INSTALLED_BROKERS[0]}"
BROKER_NAME="${INSTALLED_NAMES[0]}"
echo "  Broker: $BROKER_NAME ($BROKER)"

# --- Battery exemption check ---
echo "[4/8] Checking battery optimization for broker..."
BATTERY_EXEMPT=false
if adb_cmd shell dumpsys deviceidle whitelist 2>/dev/null | grep -q "$BROKER"; then
    BATTERY_EXEMPT=true
fi
if [ "$BATTERY_EXEMPT" = true ]; then
    echo -e "${YELLOW}  WARNING: $BROKER_NAME is battery-exempt (DeviceIdle whitelist).${NC}"
    echo "  The broker may bypass Doze network restrictions."
    echo "  To remove exemption: Settings > Apps > $BROKER_NAME > Battery > Optimized"
    echo "  Or run: adb shell dumpsys deviceidle whitelist -$BROKER"
    echo ""
    echo -e "  Continuing anyway — test may show UNEXPECTED SUCCESS...${NC}"
else
    echo "  Battery optimization: active (not exempt) — good"
fi

# --- Force Doze ---
echo "[5/8] Forcing Doze mode..."
adb_cmd logcat -c
adb_cmd shell dumpsys battery unplug > /dev/null 2>&1
adb_cmd shell dumpsys deviceidle force-idle > /dev/null 2>&1
DOZE_FORCED=true

DOZE_STATE=$(adb_cmd shell dumpsys deviceidle get deep 2>/dev/null)
IDLE_MODE=$(adb_cmd shell "dumpsys power | grep mDeviceIdleMode" 2>/dev/null | tr -d '[:space:]')
echo "  Doze state: $DOZE_STATE"
echo "  $IDLE_MODE"

if [ "$DOZE_STATE" != "IDLE" ]; then
    echo -e "${RED}ERROR: Failed to enter Doze. State: $DOZE_STATE${NC}"
    exit 1
fi

# --- Send broadcast ---
echo "[6/8] Sending SILENT_AUTH broadcast (background context)..."
adb_cmd shell "am broadcast \
    -a $RECEIVER_ACTION \
    -n $RECEIVER_PKG/$RECEIVER_CLASS \
    --es scopes 'https://graph.microsoft.com/.default'" > /dev/null 2>&1

echo "  Waiting ${WAIT_SECONDS}s for auth to complete..."
sleep "$WAIT_SECONDS"

# --- Capture results ---
echo "[7/8] Capturing logcat results..."
LOGCAT=$(adb_cmd logcat -d -s "${TAG}:*" 2>/dev/null)

# --- Report results ---
echo ""
echo "============================================="
echo " Results"
echo "============================================="
echo ""

if echo "$LOGCAT" | grep -q "=== SUCCESS ==="; then
    echo -e "${GREEN}Result: TOKEN ACQUIRED${NC}"
    echo ""
    echo "The Broker had network access during Doze."
    if [ "$BATTERY_EXEMPT" = true ]; then
        echo "  (Broker is battery-exempt — this is expected)"
    fi
    echo ""
    echo "$LOGCAT" | grep "$TAG"
elif echo "$LOGCAT" | grep -q "=== FAILED ==="; then
    ERROR_CODE=$(echo "$LOGCAT" | grep "Error code:" | sed 's/.*Error code: //')
    ERROR_MSG=$(echo "$LOGCAT" | grep "Message:" | sed 's/.*Message: //')

    echo -e "${RED}Result: AUTH FAILED${NC}"
    echo ""
    echo "  Error code: $ERROR_CODE"
    echo "  Message:    $ERROR_MSG"
    echo ""

    if echo "$ERROR_CODE" | grep -q "io_error"; then
        echo "  Interpretation: Doze firewall blocked Broker's network."
        echo "  This is the raw DNS/network failure — the Broker attempted"
        echo "  the eSTS call but Android's dozable chain dropped the packet."
    elif echo "$ERROR_CODE" | grep -q "device_network_not_available\|power_optimization"; then
        echo "  Interpretation: Broker's proactive Doze check caught it."
        echo "  The powerOptCheck detected Doze before attempting the"
        echo "  network call and returned a classified error."
    else
        echo "  Interpretation: Unexpected error code — see details above."
    fi
elif echo "$LOGCAT" | grep -q "=== CANCELLED ==="; then
    echo -e "${YELLOW}Result: AUTH CANCELLED${NC}"
    echo "$LOGCAT" | grep "$TAG"
elif echo "$LOGCAT" | grep -q "No signed-in account\|No accounts found"; then
    echo -e "${YELLOW}Result: NO ACCOUNT${NC}"
    echo "  Sign in to MsalTestApp first via interactive auth."
else
    echo -e "${YELLOW}Result: NO OUTPUT${NC}"
    echo "  SilentAuthReceiver did not produce expected logs."
    echo ""
    echo "Raw logcat:"
    echo "$LOGCAT" | head -20
fi
