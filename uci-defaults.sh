#!/bin/sh

WAN_USER='宽带账号'
WAN_PASS='宽带密码'
WIFI_KEY='WiFi密码'

echo "[init] configure PPPoE..."
uci batch <<EOF
set network.wan.proto='pppoe'
set network.wan.username='${WAN_USER}'
set network.wan.password='${WAN_PASS}'
commit network
EOF

echo "[init] configure 5GHz WiFi..."
uci batch <<EOF
set wireless.radio1.disabled='0'
set wireless.default_radio1.encryption='psk2'
set wireless.default_radio1.key='${WIFI_KEY}'
commit wireless
EOF

echo "[init] restart network & wifi..."
/etc/init.d/network restart
wifi reload || wifi

echo "[init] done."
exit 0
