#!/bin/bash

# 更新 Cloudflare IP 列表
echo "更新 Cloudflare IP 列表..."
wget -q -O ip-lists/cloudflare-ips.txt https://www.cloudflare.com/ips-v4

# 保留用户自定义的非 Cloudflare IP
echo "保留现有非 Cloudflare IP 列表..."
touch ip-lists/non-cloudflare-ips.txt