#!/bin/sh

# ---------------------------------------------------------
# Sing-Box OpenWrt TProxy NFTables Setup Script
# Author: Ava (GitHub Copilot)
# ---------------------------------------------------------

# Configuration
PROXY_PORT=12345
PROXY_FWMARK=1
PROXY_TABLE=100

# 1. Configure Routing Rules
# ---------------------------
# Create a routing table for marked packets to be delivered locally
# Check if rule exists to avoid duplicates
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

        # 0. 拒绝 DoT (853)，强迫设备使用标准 DNS (53)
        meta l4proto { tcp, udp } th dport 853 counter reject comment "Block DoT (853) to force fallback"

        # [已注释] 拒绝 QUIC (UDP 443)
        # meta l4proto udp th dport 443 counter reject comment "Block QUIC (UDP 443)"

        # 1. Hijack DNS traffic (TCP/UDP 53) to Sing-Box
        # This ensures even queries to the router (192.168.x.x) are intercepted
        meta l4proto { tcp, udp } th dport 53 tproxy to :$PROXY_PORT meta mark set $PROXY_FWMARK accept comment "Hijack DNS (53)"

        # 2. Skip local and reserved addresses for other traffic
        ip daddr { 127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 224.0.0.0/4, 255.255.255.255 } return
        ip6 daddr { ::1, fe80::/10, fc00::/7, ff00::/8 } return

        # 3. Redirect remaining TCP and UDP traffic to Sing-Box TProxy port
        meta l4proto { tcp, udp } tproxy to :$PROXY_PORT meta mark set $PROXY_FWMARK accept comment "Redirect all traffic to Sing-Box"
    }

    # Output chain for proxying the router itself
    chain output {
        type route hook output priority mangle; policy accept;

        # 1. Exclude sing-box traffic (marked with 0xff in sing-box config)
        meta mark 0xff return

        # 2. Skip local and reserved addresses
        ip daddr { 127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 224.0.0.0/4, 255.255.255.255 } return
        ip6 daddr { ::1, fe80::/10, fc00::/7, ff00::/8 } return

        # 3. Mark packets for rerouting to TProxy
        meta mark set $PROXY_FWMARK
    }
}
EOF

echo "Sing-Box TProxy rules applied with NFTables!"
