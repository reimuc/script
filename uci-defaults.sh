#!/bin/sh

### === PPPoE 拨号 ===
uci set network.wan.proto='pppoe'
uci set network.wan.username='你的宽带账号'
uci set network.wan.password='你的宽带密码'
uci commit network

### === 启用 5GHz WiFi ===
uci set wireless.radio1.disabled='0'
uci set wireless.default_radio1.encryption='psk2'
uci set wireless.default_radio1.key='你的WiFi密码'
uci commit wireless

### === 重启网络 ===
/etc/init.d/network restart
wifi reload

exit 0
