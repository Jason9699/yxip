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
    echo "测试模式: Cloudflare IP"
    cat ip-lists/cloudflare-ips.txt >> combined-ips.txt
    ;;
  2)
    echo "测试模式: 非Cloudflare IP"
    cat ip-lists/non-cloudflare-ips.txt >> combined-ips.txt
    ;;
  3)
    echo "测试模式: 所有IP"
    cat ip-lists/cloudflare-ips.txt ip-lists/non-cloudflare-ips.txt >> combined-ips.txt
    ;;
esac

# 统计IP数量
ip_count=$(wc -l < combined-ips.txt)
echo "待测试IP数量: $ip_count"

# 运行测试
echo "开始测试..."
args="-tl $max_delay -tll $min_delay -sl $loss -dn 10 -p 0 -url '$test_url'"

if [ "$test_type" = "HTTP" ]; then
  args="$args -http"
fi

# 执行测试命令
echo "执行命令: ./CloudflareST $args -tp $port -f combined-ips.txt -o result.csv"
eval "./CloudflareST $args -tp $port -f combined-ips.txt -o result.csv"

# 检查结果文件
if [ ! -f result.csv ]; then
  echo "错误: 结果文件未生成!"
  exit 1
fi

# 处理结果
echo "生成结果文件..."
mkdir -p results

# 所有测试通过的IP
awk -F, 'NR>1{print $1}' result.csv > results/all.txt
echo "全部IP数量: $(wc -l < results/all.txt)"

# 满足条件的优选IP
awk -F, -v min_d=$min_delay -v max_d=$max_delay -v max_l=$loss -v min_s=$min_speed '
  NR>1 && $6>=min_d && $6<=max_d && $5<=max_l && $7>=min_s {print $1}
' result.csv > results/preferred.txt
echo "优选IP数量: $(wc -l < results/preferred.txt)"

# 精选TOP15 IP
awk -F, -v min_d=$min_delay -v max_d=$max_delay -v max_l=$loss -v min_s=$min_speed '
  NR>1 && $6>=min_d && $6<=max_d && $5<=max_l && $7>=min_s {print $0}
' result.csv | sort -t, -k6n -k5n -k7nr | head -15 | awk -F, '{print $1}' > results/selected.txt
echo "精选IP数量: $(wc -l < results/selected.txt)"

# 显示精选IP
echo "精选IP列表:"
cat results/selected.txt

echo "测试完成！"