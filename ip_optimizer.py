import os
import requests
import random
import numpy as np
import time
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from tqdm import tqdm
import urllib3
import ipaddress
import threading

####################################################
#                 可配置参数（程序开头）              #
####################################################
# 环境变量默认值（可通过.env或GitHub Actions覆盖）
CONFIG = {
    "MODE": "TCP",                  # 测试模式：PING/TCP
    "PING_TARGET": "https://www.google.com/generate_204",  # Ping测试目标
    "PING_COUNT": 2,                # Ping次数
    "PING_TIMEOUT": 2,              # Ping超时(秒)
    "PORT": 443,                    # TCP测试端口
    "RTT_RANGE": "10~2000",          # 延迟范围(ms)
    "LOSS_MAX": 30.0,               # 最大丢包率(%)
    "THREADS": 50,                  # 并发线程数
    "IP_COUNT": 2000,                # 测试IP数量
    "TOP_IPS_LIMIT": 10,            # 精选IP数量
    "CLOUDFLARE_IPS_URL": "https://www.cloudflare.com/ips-v4",
    "TCP_RETRY": 2                  # TCP重试次数
}

####################################################
#                    核心功能函数                   #
####################################################
# 初始化环境变量
def init_env():
    # 设置环境变量
    for key, value in CONFIG.items():
        os.environ[key] = str(value)
    
    # 自动添加URL协议头
    cf_url = os.getenv('CLOUDFLARE_IPS_URL')
    if not cf_url.startswith(('http://', 'https://')):
        os.environ['CLOUDFLARE_IPS_URL'] = f"https://{cf_url}"
    
    # 禁用TLS警告
    urllib3.disable_warnings()

# 获取Cloudflare IP段 
def fetch_cloudflare_ips():
    url = os.getenv('CLOUDFLARE_IPS_URL')
    try:
        res = requests.get(url, timeout=10, verify=False)
        return res.text.splitlines()
    except Exception as e:
        print(f"🚨 获取IP段失败: {e}")
        return []

# 生成随机IP（基于位运算实现）[5](@ref)
def generate_random_ip(subnet):
    """根据CIDR生成子网内的随机合法IP（排除网络地址和广播地址）"""
    try:
        network = ipaddress.ip_network(subnet, strict=False)
        network_addr = int(network.network_address)
        broadcast_addr = int(network.broadcast_address)
        
        # 排除网络地址和广播地址
        first_ip = network_addr + 1
        last_ip = broadcast_addr - 1
        
        # 生成随机IP
        random_ip_int = random.randint(first_ip, last_ip)
        return str(ipaddress.IPv4Address(random_ip_int))
    except Exception as e:
        print(f"生成随机IP错误: {e}，使用简单方法生成")
        base_ip = subnet.split('/')[0]
        return ".".join(base_ip.split('.')[:3] + [str(random.randint(1, 254))])

# 自定义Ping测试（跨平台兼容）[6,8](@ref)
def custom_ping(ip):
    target = urlparse(os.getenv('PING_TARGET')).netloc or os.getenv('PING_TARGET')
    count = int(os.getenv('PING_COUNT'))
    timeout = int(os.getenv('PING_TIMEOUT'))
    
    try:
        # 跨平台ping命令
        if os.name == 'nt':  # Windows
            cmd = f"ping -n {count} -w {timeout*1000} {target}"
        else:  # Linux/Mac
            cmd = f"ping -c {count} -W {timeout} -I {ip} {target}"
        
        result = subprocess.run(
            cmd, 
            shell=True, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout + 2
        )
        
        # 解析ping结果
        output = result.stdout.lower()
        
        if "100% packet loss" in output or "unreachable" in output:
            return float('inf'), 100.0  # 完全丢包
        
        # 提取延迟和丢包率
        loss_line = next((l for l in result.stdout.split('\n') if "packet loss" in l.lower()), "")
        timing_lines = [l for l in result.stdout.split('\n') if "time=" in l.lower()]
        
        # 计算丢包率
        loss_percent = 100.0
        if loss_line:
            loss_parts = loss_line.split('%')
            if loss_parts:
                try:
                    loss_percent = float(loss_parts[0].split()[-1])
                except:
                    pass
        
        # 计算平均延迟
        delays = []
        for line in timing_lines:
            if "time=" in line:
                time_str = line.split("time=")[1].split()[0]
                try:
                    delays.append(float(time_str))
                except:
                    continue
        avg_delay = np.mean(delays) if delays else float('inf')
        
        return avg_delay, loss_percent
        
    except subprocess.TimeoutExpired:
        return float('inf'), 100.0
    except Exception as e:
        print(f"Ping测试异常: {e}")
        return float('inf'), 100.0

# TCP连接测试（带重试机制）[8,10](@ref)
def tcp_ping(ip, port, timeout=2):
    retry = int(os.getenv('TCP_RETRY', 3))
    success_count = 0
    total_rtt = 0
    
    for _ in range(retry):
        start = time.time()
        try:
            with socket.create_connection((ip, port), timeout=timeout) as sock:
                rtt = (time.time() - start) * 1000  # 毫秒
                total_rtt += rtt
                success_count += 1
        except:
            pass
        time.sleep(0.1)  # 短暂间隔
    
    loss_rate = 100 - (success_count / retry * 100)
    avg_rtt = total_rtt / success_count if success_count > 0 else float('inf')
    return avg_rtt, loss_rate

# IP综合测试
def test_ip(ip):
    mode = os.getenv('MODE', 'PING').upper()
    
    if mode == "PING":
        # 使用自定义Ping测试
        avg_delay, loss_rate = custom_ping(ip)
        return (ip, avg_delay, loss_rate, 0)  # 速度设为0
    
    else:  # TCP模式
        port = int(os.getenv('PORT', 443))
        avg_rtt, loss_rate = tcp_ping(ip, port, timeout=float(os.getenv('PING_TIMEOUT', 2)))
        return (ip, avg_rtt, loss_rate, 0)  # 速度设为0

####################################################
#                      主逻辑                      #
####################################################
if __name__ == "__main__":
    # 0. 初始化环境
    init_env()
    
    # 1. 打印配置参数
    print("="*60)
    print(f"{'IP网络优化器 v2.1':^60}")
    print("="*60)
    print(f"测试模式: {os.getenv('MODE')}")
    
    if os.getenv('MODE') == "PING":
        print(f"Ping目标: {os.getenv('PING_TARGET')}")
        print(f"Ping次数: {os.getenv('PING_COUNT')}")
        print(f"Ping超时: {os.getenv('PING_TIMEOUT')}秒")
    else:
        print(f"TCP端口: {os.getenv('PORT')}")
        print(f"TCP重试: {os.getenv('TCP_RETRY')}次")
    
    print(f"延迟范围: {os.getenv('RTT_RANGE')}ms")
    print(f"最大丢包: {os.getenv('LOSS_MAX')}%")
    print(f"并发线程: {os.getenv('THREADS')}")
    print(f"测试IP数: {os.getenv('IP_COUNT')}")
    print("="*60 + "\n")
    
    # 2. 获取IP段并生成随机IP
    subnets = fetch_cloudflare_ips()
    if not subnets:
        print("❌ 无法获取Cloudflare IP段，程序终止")
        exit(1)
    
    print(f"✅ 获取到 {len(subnets)} 个Cloudflare IP段")
    
    all_ips = []
    for _ in range(int(os.getenv('IP_COUNT'))):
        subnet = random.choice(subnets)
        all_ips.append(generate_random_ip(subnet))
    
    # 3. 多线程测试（带进度条）
    results = []
    with ThreadPoolExecutor(max_workers=int(os.getenv('THREADS'))) as executor:
        future_to_ip = {executor.submit(test_ip, ip): ip for ip in all_ips}
        
        # 进度条配置
        with tqdm(
            total=len(all_ips), 
            desc="🚀 测试进度", 
            unit="IP",
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"
        ) as pbar:
            for future in as_completed(future_to_ip):
                try:
                    results.append(future.result())
                except Exception as e:
                    print(f"\n🔧 测试异常: {e}")
                finally:
                    pbar.update(1)
    
    # 4. 筛选优选IP
    rtt_min, rtt_max = map(int, os.getenv('RTT_RANGE').split('~'))
    loss_max = float(os.getenv('LOSS_MAX'))
    
    optimized_ips = [
        ip_data for ip_data in results 
        if rtt_min <= ip_data[1] <= rtt_max
        and ip_data[2] <= loss_max
    ]
    
    # 5. 精选IP排序
    sorted_ips = sorted(
        optimized_ips,
        key=lambda x: (x[1], x[2])  # 按延迟和丢包率排序
    )[:int(os.getenv('TOP_IPS_LIMIT', 15))]
    
    # 6. 保存结果
    os.makedirs('results', exist_ok=True)
    
    with open('results/all_ips.txt', 'w') as f:
        f.write("\n".join([ip[0] for ip in results]))
    
    with open('results/optimized_ips.txt', 'w') as f:
        f.write("\n".join([ip[0] for ip in optimized_ips]))
    
    with open('results/top_ips.txt', 'w') as f:
        f.write("\n".join([ip[0] for ip in sorted_ips]))
    
    # 7. 显示统计结果
    print("\n" + "="*60)
    print(f"{'🔥 测试结果统计':^60}")
    print("="*60)
    print(f"总测试IP数: {len(results)}")
    print(f"优选IP数量: {len(optimized_ips)}")
    print(f"精选TOP IP: {len(sorted_ips)}")
    
    if sorted_ips:
        print("\n🏆【最佳IP TOP5】")
        for i, ip_data in enumerate(sorted_ips[:5]):
            print(f"{i+1}. {ip_data[0]} | 延迟:{ip_data[1]:.2f}ms | 丢包:{ip_data[2]:.2f}%")
    
    print("="*60)
    print("✅ 结果已保存至 results/ 目录")
