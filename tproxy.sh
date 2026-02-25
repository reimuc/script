#!/bin/sh

# ---------------------------------------------------------
# Sing-Box OpenWrt TProxy NFTables Setup Script
# Author: Ava (GitHub Copilot)
# ---------------------------------------------------------

# Configuration
PROXY_PORT=12345
PROXY_FWMARK=1
PROXY_TABLE=100
ROUTING_MARK=0xff

check_dependencies() {
  echo "🔍 Checking dependencies..."

  if ! lsmod | grep -q "nft_tproxy"; then
    echo "❗Module nft_tproxy not loaded. Trying to load..."
    modprobe nft_tproxy
    if ! lsmod | grep -q "nft_tproxy"; then
      echo "❌ kmod-nft-tproxy is missing! Please install it: opkg install kmod-nft-tproxy"
      exit 1
    fi
  fi

  if ! lsmod | grep -q "nft_socket"; then
    echo "❗Module nft_socket not loaded. Trying to load..."
    modprobe nft_socket
    if ! lsmod | grep -q "nft_socket"; then
      echo "❌ kmod-nft-socket is missing! Please install it: opkg install kmod-nft-socket"
      exit 1
    fi
  fi
}

start() {
  check_dependencies

  # 1. Configure Routing Rules
  # ---------------------------
  # Create a routing table for marked packets to be delivered locally
  if ! ip rule show | grep -q "fwmark 0x$PROXY_FWMARK lookup $PROXY_TABLE"; then
    ip rule add fwmark $PROXY_FWMARK table $PROXY_TABLE
    ip route add local 0.0.0.0/0 dev lo table $PROXY_TABLE
  fi

  if ! ip -6 rule show | grep -q "fwmark 0x$PROXY_FWMARK lookup $PROXY_TABLE"; then
    ip -6 rule add fwmark $PROXY_FWMARK table $PROXY_TABLE
    ip -6 route add local ::/0 dev lo table $PROXY_TABLE
  fi

  # 2. Configure NFTables
  # ---------------------------
  # Create a table and chain for TProxy redirection
  nft -f - <<EOF
table inet sing-box {
    chain prerouting {
        type filter hook prerouting priority mangle; policy accept;

        # 1. Skip local and reserved addresses
        ip daddr { 127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 224.0.0.0/4, 255.255.255.255 } return
        ip6 daddr { ::1, fe80::/10, fc00::/7, ff00::/8 } return

        # 2. Redirect remaining traffic to Sing-Box
        meta l4proto { tcp, udp } tproxy to :$PROXY_PORT meta mark set $PROXY_FWMARK counter accept comment "TProxy redirect to sing-box"
    }

    chain output {
        type route hook output priority mangle; policy accept;

        # 1. Exclude sing-box traffic (marked with 0xff)
        meta mark $ROUTING_MARK return

        # 2. Skip local and reserved addresses
        ip daddr { 127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 224.0.0.0/4, 255.255.255.255 } return
        ip6 daddr { ::1, fe80::/10, fc00::/7, ff00::/8 } return

        # 3. Mark TCP/UDP packets for rerouting to TProxy
        meta l4proto { tcp, udp } meta mark set $PROXY_FWMARK counter accept comment "Mark local-originated packets for TProxy"
    }
}
EOF
  echo "TProxy rules applied!"
}

stop() {
  # Flush NFTables
  nft delete table inet sing-box 2>/dev/null || true

  # Remove Routing Rules
  while ip rule show | grep -q "fwmark 0x$PROXY_FWMARK lookup $PROXY_TABLE"; do
    ip rule del fwmark $PROXY_FWMARK table $PROXY_TABLE
  done
  while ip -6 rule show | grep -q "fwmark 0x$PROXY_FWMARK lookup $PROXY_TABLE"; do
    ip -6 rule del fwmark $PROXY_FWMARK table $PROXY_TABLE
  done

  # Remove Routes
  ip route flush table $PROXY_TABLE
  ip -6 route flush table $PROXY_TABLE

  echo "TProxy rules cleaned up!"
}

case "$1" in
start) start ;;
stop) stop ;;
restart)
  stop
  start
  ;;
*) echo "Usage: \$0 {start|stop|restart}" ;;
esac
