#!/bin/sh

# ---------------------------------------------------------
# Sing-Box OpenWrt TProxy NFTables Setup Script
# ---------------------------------------------------------

# Configuration
PROXY_PORT=12345     # sing-box tproxy inbound listen_port
PROXY_FWMARK=0x1     # 被代理流量打的 mark（与 ip rule 匹配）
PROXY_TABLE=100      # 策略路由表号
ROUTING_MARK=0xff    # sing-box 自身出站流量 mark（用于 output 链排除）
NFT_TABLE="sing-box" # nft table 名

check_dependencies() {
  echo "🔍 Checking dependencies..."
  if ! lsmod | grep -q "nft_tproxy"; then
    echo "❗Module nft_tproxy not loaded. Trying to load..."
    modprobe nft_tproxy || true
  fi
  if ! lsmod | grep -q "nft_socket"; then
    echo "❗Module nft_socket not loaded. Trying to load..."
    modprobe nft_socket || true
  fi

  if ! lsmod | grep -q "nft_tproxy"; then
    echo "❌ kmod-nft-tproxy missing! opkg install kmod-nft-tproxy"
    exit 1
  fi
  if ! lsmod | grep -q "nft_socket"; then
    echo "❌ kmod-nft-socket missing! opkg install kmod-nft-socket"
    exit 1
  fi
}

setup_policy_routing() {
  # IPv4
  if ! ip rule show | grep -q "fwmark $PROXY_FWMARK.*lookup $PROXY_TABLE"; then
    ip rule add fwmark $PROXY_FWMARK table $PROXY_TABLE
  fi
  # “local 0.0.0.0/0 dev lo” 是 TProxy 常规搭配：让被标记包本地投递
  if ! ip route show table $PROXY_TABLE | grep -q "^local 0.0.0.0/0 dev lo"; then
    ip route add local 0.0.0.0/0 dev lo table $PROXY_TABLE
  fi

  # IPv6
  if ! ip -6 rule show | grep -q "fwmark $PROXY_FWMARK.*lookup $PROXY_TABLE"; then
    ip -6 rule add fwmark $PROXY_FWMARK table $PROXY_TABLE
  fi
  if ! ip -6 route show table $PROXY_TABLE | grep -q "^local ::/0 dev lo"; then
    ip -6 route add local ::/0 dev lo table $PROXY_TABLE
  fi
}

apply_nft_rules() {
  # 幂等：先删旧表
  nft delete table inet $NFT_TABLE 2>/dev/null || true

  nft -f - <<EOF
table inet $NFT_TABLE {

  # -----------------------
  # 保留地址集合（建议长期维护用 set）
  # -----------------------
  set byp4 {
    type ipv4_addr
    flags interval
    elements = {
      0.0.0.0/8,        # this-network / 异常目的地址（防御性）
      10.0.0.0/8,       # RFC1918
      100.64.0.0/10,    # CGNAT
      127.0.0.0/8,      # loopback（虽然 fib local 常能覆盖，但保留也很便于理解）
      169.254.0.0/16,   # IPv4 link-local
      172.16.0.0/12,    # RFC1918
      192.168.0.0/16,   # RFC1918
      198.18.0.0/15     # benchmark / 测试网段（很多 fakeip/测试会用到）
    }
  }

  set byp6 {
    type ipv6_addr
    flags interval
    elements = {
      ::/128,        # unspecified
      ::1/128,       # loopback（同上，fib local 通常覆盖，但保留更直观）
      fc00::/7,      # ULA
      fe80::/10      # link-local
    }
  }

  # -----------------------
  # 可选：DIVERT（已透明 socket 的连接直接打 mark，避免重复处理）
  # -----------------------
  chain divert {
    meta l4proto tcp socket transparent 1 meta mark set $PROXY_FWMARK counter accept
  }

  # -----------------------
  # PREROUTING：拦截 LAN 转发流量 / 回灌流量
  # -----------------------
  chain prerouting {
    type filter hook prerouting priority mangle; policy accept;

    # 只处理 tcp/udp
    meta l4proto != { tcp, udp } return

    # 可选 divert（仅 tcp）：先处理已透明 socket
    jump divert

    # 1) 放行目的为本机(local) —— 防止访问路由器自身服务异常 & 防回环
    fib daddr type local return

    # 2) 放行组播/广播（mDNS/SSDP 等），避免干扰局域网发现
    fib daddr type { multicast, broadcast } return

    # 3) 放行保留地址段（私网/链路本地/测试段等）
    ip  daddr @byp4 return
    ip6 daddr @byp6 return

    # 4) 其余 tcp/udp 做 tproxy 并打 mark（仅端口写法，inet 下 v4/v6 通用）
    meta l4proto { tcp, udp } tproxy to :$PROXY_PORT meta mark set $PROXY_FWMARK counter accept comment "TProxy -> sing-box"
  }

  # -----------------------
  # OUTPUT：路由器本机发起流量打 mark，触发策略路由回到 prerouting
  # -----------------------
  chain output {
    type route hook output priority mangle; policy accept;

    # 0) 排除 sing-box 自己的出站，防止代理回环（你在 sing-box outbounds 用 routing_mark=0xff）
    meta mark $ROUTING_MARK return

    # 只处理 tcp/udp
    meta l4proto != { tcp, udp } return

    # 1) 放行目的为本机/组播/广播
    fib daddr type local return
    fib daddr type { multicast, broadcast } return

    # 2) 放行保留地址段
    ip  daddr @byp4 return
    ip6 daddr @byp6 return

    # 3) 其余打 mark，让策略路由把包送回本机 lo，再到 prerouting 被 tproxy
    meta l4proto { tcp, udp } meta mark set $PROXY_FWMARK counter accept comment "Mark local-originated -> TProxy"
  }
}
EOF
}

start() {
  check_dependencies
  setup_policy_routing
  apply_nft_rules
  echo "✅ sing-box TProxy rules applied."
}

stop() {
  nft delete table inet $NFT_TABLE 2>/dev/null || true

  # 清理策略路由
  while ip rule show | grep -q "fwmark $PROXY_FWMARK.*lookup $PROXY_TABLE"; do
    ip rule del fwmark $PROXY_FWMARK table $PROXY_TABLE
  done
  while ip -6 rule show | grep -q "fwmark $PROXY_FWMARK.*lookup $PROXY_TABLE"; do
    ip -6 rule del fwmark $PROXY_FWMARK table $PROXY_TABLE
  done

  ip route flush table $PROXY_TABLE 2>/dev/null || true
  ip -6 route flush table $PROXY_TABLE 2>/dev/null || true

  echo "🧹 sing-box TProxy rules cleaned."
}

case "$1" in
start) start ;;
stop) stop ;;
restart)
  stop
  start
  ;;
*) echo "Usage: $0 {start|stop|restart}" ;;
esac
