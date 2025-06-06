import os
import requests
import random
import numpy as np
import time
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from tqdm import tqdm
import urllib3

####################################################
#                 可配置参数（程序开头）              #
####################################################
# 环境变量默认值（可通过.env或GitHub Actions覆盖）
CONFIG = {
    "MODE": "TCP",                  # 测试模式
    "PORT": 443,                    # 测试端口
    "RTT_RANGE": "100~1000",         # 延迟范围(ms)
    "LOSS_MAX": 30.0,               # 最大丢包率(%)
    "DOWNLOAD_MIN": 0.5,             # 最低下载速度(MB/s)
    "THREADS": 50,                  # 并发线程数
    "IP_COUNT": 300,                # 测试IP数量
    "TOP_IPS_LIMIT": 15,            # 精选IP数量
    "SPEED_URL": "https://speed.cloudflare.com/__down?bytes=10000000",
    "CLOUDFLARE_IPS_URL": "www.cloudflare.com/ips-v4"
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
        res = requests.get(url, timeout=10)
        return res.text.splitlines()
    except Exception as e:
        print(f"获取IP段失败: {e}")
        return []

# 生成随机IP 
def generate_random_ip(subnet):
    base_ip = subnet.split('/')[0]
    return ".".join(base_ip.split('.')[:3] + [str(random.randint(1, 254))])

# TCP连接测试
def tcp_ping(ip, port, timeout=2):
    start = time.time()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return time.time() - start
    except:
        return None

# 下载速度测试（优化版）
def test_download_speed(ip):
    speed_url = os.getenv('SPEED_URL')
    parsed = urlparse(speed_url)
    
    try:
        # 使用Session保持连接 + 添加UA头
        headers = {
            'Host': parsed.hostname,
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
        }
        
        with requests.Session() as s:
            start_time = time.time()
            response = s.get(
                f"{parsed.scheme}://{ip}{parsed.path}?{parsed.query}",
                headers=headers,
                stream=True,
                timeout=10,
                verify=False
            )
            
            # 计算下载速度
            total_bytes = 0
            for chunk in response.iter_content(chunk_size=1024):
                total_bytes += len(chunk)
                if time.time() - start_time > 10:  # 超时控制
                    break
            
            duration = time.time() - start_time
            return total_bytes / (duration * 1024 * 1024)  # MB/s
    except:
        return 0

# IP综合测试
def test_ip(ip):
    port = int(os.getenv('PORT', 443))
    loss_count = 0
    rtt_list = []
    
    # TCP三次测试
    for _ in range(3):
        rtt = tcp_ping(ip, port)
        if rtt is None:
            loss_count += 1
        else:
            rtt_list.append(rtt * 1000)  # 转毫秒
    
    # 计算指标
    loss_rate = (loss_count / 3) * 100
    avg_rtt = np.mean(rtt_list) if rtt_list else float('inf')
    download_speed = test_download_speed(ip)
    
    return (ip, avg_rtt, loss_rate, download_speed)

####################################################
#                      主逻辑                      #
####################################################
if __name__ == "__main__":
    # 0. 初始化环境
    init_env()
    
    # 1. 打印配置参数
    print("="*50)
    print(f"{'IP优化器配置参数':^50}")
    print("="*50)
    print(f"测试模式: {os.getenv('MODE')}")
    print(f"测试端口: {os.getenv('PORT')}")
    print(f"延迟范围: {os.getenv('RTT_RANGE')}ms")
    print(f"最大丢包: {os.getenv('LOSS_MAX')}%")
    print(f"最低速度: {os.getenv('DOWNLOAD_MIN')}MB/s")
    print(f"并发线程: {os.getenv('THREADS')}")
    print(f"测试IP数: {os.getenv('IP_COUNT')}")
    print("="*50 + "\n")
    
    # 2. 获取IP段并生成随机IP
    subnets = fetch_cloudflare_ips()
    if not subnets:
        exit(1)
    
    all_ips = [generate_random_ip(random.choice(subnets)) 
               for _ in range(int(os.getenv('IP_COUNT')))]
    
    # 3. 多线程测试（带进度条）
    results = []
    with ThreadPoolExecutor(max_workers=int(os.getenv('THREADS'))) as executor:
        future_to_ip = {executor.submit(test_ip, ip): ip for ip in all_ips}
        
        # 进度条配置
        with tqdm(
            total=len(all_ips), 
            desc="测试进度", 
            unit="IP",
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"
        ) as pbar:
            for future in as_completed(future_to_ip):
                try:
                    results.append(future.result())
                except Exception as e:
                    print(f"\n测试异常: {e}")
                finally:
                    pbar.update(1)
    
    # 4. 筛选优选IP
    rtt_min, rtt_max = map(int, os.getenv('RTT_RANGE').split('~'))
    loss_max = float(os.getenv('LOSS_MAX'))
    download_min = float(os.getenv('DOWNLOAD_MIN'))
    
    optimized_ips = [
        ip_data for ip_data in results 
        if rtt_min <= ip_data[1] <= rtt_max
        and ip_data[2] <= loss_max 
        and ip_data[3] >= download_min
    ]
    
    # 5. 精选IP排序
    sorted_ips = sorted(
        optimized_ips,
        key=lambda x: (x[1], x[2], -x[3])
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
    print("\n" + "="*50)
    print(f"{'测试结果统计':^50}")
    print("="*50)
    print(f"总测试IP数: {len(results)}")
    print(f"优选IP数量: {len(optimized_ips)}")
    print(f"精选TOP IP: {len(sorted_ips)}")
    
    if sorted_ips:
        print("\n【最佳IP TOP5】")
        for i, ip_data in enumerate(sorted_ips[:5]):
            print(f"{i+1}. {ip_data[0]} | 延迟:{ip_data[1]:.2f}ms | 丢包:{ip_data[2]:.2f}% | 速度:{ip_data[3]:.2f}MB/s")
    
    print("="*50)
