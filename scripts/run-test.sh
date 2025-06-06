#!/bin/bash

# 接收参数
test_mode=$1          # 测试模式 (1,2,3)
test_type=$2          # 协议类型 (TCP/HTTP)
port=$3               # 测试端口
min_delay=$4          # 最低延迟
max_delay=$5          # 最高延迟
max_loss=$6           # 最大丢包率
min_speed=$7          # 最低下载速度
test_url=$8           # 测速文件URL

# 转换丢包率为小数
loss=$(awk "BEGIN {print $max_loss/100}")

# 准备IP列表
echo "准备测试IP列表..."
> combined-ips.txt

case $test_mode in
  1)
    cat ip-lists/cloudflare-ips.txt >> combined-ips.txt
    ;;
  2)
    cat ip-lists/non-cloudflare-ips.txt >> combined-ips.txt
    ;;
  3)
    cat ip-lists/cloudflare-ips.txt ip-lists/non-cloudflare-ips.txt >> combined-ips.txt
    ;;
esac

# 运行测试
echo "开始测试..."
args="-tl $max_delay -tll $min_delay -sl $loss -dn $min_speed -p 0 -url $test_url"

if [ "$test_type" = "HTTP" ]; then
  args="$args -http"
fi

./CloudflareST $args -tp $port -f combined-ips.txt -o result.csv

# 处理结果
echo "生成结果文件..."
awk -F, 'NR>1{print $1}' result.csv > results/all.txt

awk -F, -v min_d=$min_delay -v max_d=$max_delay -v max_l=$loss -v min_s=$min_speed '
  NR>1 && $6>=min_d && $6<=max_d && $5<=max_l && $7>=min_s {print $1}
' result.csv > results/preferred.txt

awk -F, -v min_d=$min_delay -v max_d=$max_delay -v max_l=$loss -v min_s=$min_speed '
  NR>1 && $6>=min_d && $6<=max_d && $5<=max_l && $7>=min_s {print $0}
' result.csv | sort -t, -k6n -k5n -k7nr | head -15 | awk -F, '{print $1}' > results/selected.txt

echo "测试完成！"