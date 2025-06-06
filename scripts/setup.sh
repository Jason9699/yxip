#!/bin/bash

if [ ! -f CloudflareST ]; then
    echo "下载测试工具..."
    wget -q https://github.com/XIU2/CloudflareSpeedTest/releases/download/v2.2.5/CloudflareST_linux_386.tar.gz
    tar -zxvf CloudflareST_linux_386.tar.gz > /dev/null
    rm CloudflareST_linux_386.tar.gz
    chmod +x CloudflareST
fi

mkdir -p results ip-lists
touch ip-lists/cloudflare-ips.txt ip-lists/non-cloudflare-ips.txt