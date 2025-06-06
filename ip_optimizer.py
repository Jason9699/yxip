import os
import requests
import random
import numpy as np
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
import socket

# 环境变量验证与默认值设置
def validate_env():
    REQUIRED_ENVS = ["SPEED_URL", "CLOUDFLARE_IPS_URL"]
    for env in REQUIRED_ENVS:
        if not os.getenv(env):
            raise ValueError(f"错误: {env} 环境变量未设置！")
    
    # 自动添加URL协议头
    cf_url = os.getenv('CLOUDFLARE_IPS_URL')
    if not cf_url.startswith(('http://', 'https://')):
        os.environ['CLOUDFLARE_IPS_URL'] = f"https://{cf_url}"

# 获取Cloudflare IP段 
def fetch_cloudflare_ips():
    url = os.getenv('CLOUDFLARE_IPS_URL')
    res = requests.get(url)
    return res.text.splitlines()

# 生成随机IP 
def generate_random_ip(subnet):
    base_ip = subnet.split('/')[0]
    return ".".join(base_ip.split('.')[:3] + [str(random.randint(1, 254))])

# TCP连接测试 (替代ICMP)
def tcp_ping(ip, port, timeout=2):
    start = time.time()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return time.time() - start
    except:
        return None

# 下载速度测试
def test_download_speed(ip):
    speed_url = os.getenv('SPEED_URL')
    parsed = urlparse(speed_url)
    
    try:
        # 使用IP直接访问并设置Host头
        headers = {'Host': parsed.hostname}
        start_time = time.time()
        response = requests.get(
            f"{parsed.scheme}://{ip}{parsed.path}?{parsed.query}",
            headers=headers,
            stream=True,
            timeout=10,
            verify=False
        )
        
        # 计算下载速度 (MB/s)
        total_bytes = 0
        for chunk in response.iter_content(chunk_size=1024):
            if chunk:
                total_bytes += len(chunk)
                if time.time() - start_time > 10:  # 最多10秒
                    break
        
        duration = time.time() - start_time
        return total_bytes / (duration * 1024 * 1024)  # 转为MB/s
    except:
        return 0

# IP综合测试
def test_ip(ip):
    port = int(os.getenv('PORT', 443))
    loss_count = 0
    rtt_list = []
    
    # 进行3次TCP测试计算丢包率
    for _ in range(3):
        rtt = tcp_ping(ip, port)
        if rtt is None:
            loss_count += 1
        else:
            rtt_list.append(rtt * 1000)  # 转为毫秒
    
    loss_rate = (loss_count / 3) * 100  # 丢包率(%)
    avg_rtt = np.mean(rtt_list) if rtt_list else float('inf')
    download_speed = test_download_speed(ip)
    
    return (ip, avg_rtt, loss_rate, download_speed)

# 主逻辑
if __name__ == "__main__":
    # 0. 环境验证
    validate_env()
    
    # 1. 配置参数
    MODE = os.getenv('MODE', 'TCP')
    PORT = int(os.getenv('PORT', 443))
    RTT_RANGE = list(map(int, os.getenv('RTT_RANGE', '40~250').split('~')))
    LOSS_MAX = float(os.getenv('LOSS_MAX', 10))
    DOWNLOAD_MIN = float(os.getenv('DOWNLOAD_MIN', 1.0))
    THREADS = int(os.getenv('THREADS', 50))
    IP_COUNT = int(os.getenv('IP_COUNT', 1000))
    
    # 2. 获取IP段并生成随机IP
    subnets = fetch_cloudflare_ips()
    all_ips = [generate_random_ip(subnet) for _ in range(IP_COUNT)]
    
    # 3. 多线程测试
    results = []
    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        results = list(executor.map(test_ip, all_ips))
    
    # 4. 筛选优选IP
    optimized_ips = [
        ip_data for ip_data in results 
        if RTT_RANGE[0] <= ip_data[1] <= RTT_RANGE[1] 
        and ip_data[2] <= LOSS_MAX 
        and ip_data[3] >= DOWNLOAD_MIN
    ]
    
    # 5. 精选IP排序 (延时 > 丢包 > 下载)
    sorted_ips = sorted(
        optimized_ips,
        key=lambda x: (x[1], x[2], -x[3])
    )[:15]  # 最多保留15个
    
    # 6. 创建结果目录
    os.makedirs('results', exist_ok=True)
    
    # 7. 保存结果
    with open('results/all_ips.txt', 'w') as f:
        f.write("\n".join([ip[0] for ip in results]))
    
    with open('results/optimized_ips.txt', 'w') as f:
        f.write("\n".join([ip[0] for ip in optimized_ips]))
    
    with open('results/top_ips.txt', 'w') as f:
        f.write("\n".join([ip[0] for ip in sorted_ips]))
    
    print(f"IP优选完成！共测试{len(results)}个IP，优选{len(optimized_ips)}个，精选{len(sorted_ips)}个")
