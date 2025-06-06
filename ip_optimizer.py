import os
import requests
import random
import numpy as np
import time
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from tqdm import tqdm  # 添加进度条库[2,3,11](@ref)

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

# TCP连接测试
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
        headers = {'Host': parsed.hostname}
        start_time = time.time()
        response = requests.get(
            f"{parsed.scheme}://{ip}{parsed.path}?{parsed.query}",
            headers=headers,
            stream=True,
            timeout=10,
            verify=False
        )
        
        total_bytes = 0
        for chunk in response.iter_content(chunk_size=1024):
            if chunk:
                total_bytes += len(chunk)
                if time.time() - start_time > 10:
                    break
        
        duration = time.time() - start_time
        return total_bytes / (duration * 1024 * 1024)
    except:
        return 0

# IP综合测试
def test_ip(ip):
    port = int(os.getenv('PORT', 443))
    loss_count = 0
    rtt_list = []
    
    for _ in range(3):
        rtt = tcp_ping(ip, port)
        if rtt is None:
            loss_count += 1
        else:
            rtt_list.append(rtt * 1000)
    
    loss_rate = (loss_count / 3) * 100
    avg_rtt = np.mean(rtt_list) if rtt_list else float('inf')
    download_speed = test_download_speed(ip)
    
    return (ip, avg_rtt, loss_rate, download_speed)

# 主逻辑
if __name__ == "__main__":
    # 0. 环境验证
    validate_env()
    
    # 1. 配置参数（从环境变量读取）
    MODE = os.getenv('MODE', 'TCP')
    PORT = int(os.getenv('PORT', 443))
    RTT_RANGE = list(map(int, os.getenv('RTT_RANGE', '40~250').split('~')))
    LOSS_MAX = float(os.getenv('LOSS_MAX', 10))
    DOWNLOAD_MIN = float(os.getenv('DOWNLOAD_MIN', 1.0))
    THREADS = int(os.getenv('THREADS', 20))  # 默认并发数改为20[8,9](@ref)
    IP_COUNT = int(os.getenv('IP_COUNT', 200))  # 默认测试IP数改为200
    
    print(f"配置参数: 并发数={THREADS}, 测试IP数={IP_COUNT}")
    
    # 2. 获取IP段并生成随机IP
    subnets = fetch_cloudflare_ips()
    all_ips = [generate_random_ip(random.choice(subnets)) for _ in range(IP_COUNT)]
    
    # 3. 多线程测试（带进度条）
    results = []
    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        # 提交所有任务
        future_to_ip = {executor.submit(test_ip, ip): ip for ip in all_ips}
        
        # 创建进度条[2,11](@ref)
        with tqdm(total=len(all_ips), desc="测试进度", unit="IP") as pbar:
            # 处理完成的任务
            for future in as_completed(future_to_ip):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    print(f"\nIP测试异常: {e}")
                finally:
                    pbar.update(1)
    
    # 4. 筛选优选IP
    optimized_ips = [
        ip_data for ip_data in results 
        if RTT_RANGE[0] <= ip_data[1] <= RTT_RANGE[1] 
        and ip_data[2] <= LOSS_MAX 
        and ip_data[3] >= DOWNLOAD_MIN
    ]
    
    # 5. 精选IP排序
    sorted_ips = sorted(
        optimized_ips,
        key=lambda x: (x[1], x[2], -x[3])
    )[:15]
    
    # 6. 保存结果
    os.makedirs('results', exist_ok=True)
    
    with open('results/all_ips.txt', 'w') as f:
        f.write("\n".join([ip[0] for ip in results]))
    
    with open('results/optimized_ips.txt', 'w') as f:
        f.write("\n".join([ip[0] for ip in optimized_ips]))
    
    with open('results/top_ips.txt', 'w') as f:
        f.write("\n".join([ip[0] for ip in sorted_ips]))
    
    # 7. 显示统计结果
    print(f"\nIP优选完成！测试IP数: {len(results)}")
    print(f"优选IP数: {len(optimized_ips)} (延迟{RTT_RANGE[0]}-{RTT_RANGE[1]}ms, 丢包<{LOSS_MAX}%, 速度>{DOWNLOAD_MIN}MB/s)")
    print(f"精选TOP IP: {len(sorted_ips)}")
