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
    "PING_COUNT": 3,                # Ping次数
    "PING_TIMEOUT": 2,              # Ping超时(秒)
    "PORT": 443,                    # TCP测试端口
    "RTT_RANGE": "10~1000",         # 延迟范围(ms)
    "LOSS_MAX": 30.0,               # 最大丢包率(%)
    "THREADS": 50,                  # 并发线程数
    "IP_COUNT": 1000,               # 测试IP数量
    "TOP_IPS_LIMIT": 10,            # 精选IP数量
    "CLOUDFLARE_IPS_URL": "https://www.cloudflare.com/ips-v4",
    "TCP_RETRY": 3,                 # TCP重试次数
    "SPEED_TEST": True,             # 是否启用测速
    "SPEED_THREADS": 20,            # 测速并发数
    "SPEED_URL": "https://speed.cloudflare.com/__down?bytes=10000000",  # 测速URL
    "SPEED_TIMEOUT": 3,             # 测速超时(秒)
    "DOWNLOAD_MIN": 1.0             # 最低下载速度(MB/s)
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
        print(f"🚨🚨 获取IP段失败: {e}")
        return []

# 生成随机IP（基于位运算实现）
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

# 自定义Ping测试（跨平台兼容）
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

# TCP连接测试（带重试机制）
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

# 下载测速函数
def speed_test(ip):
    """测试指定IP的下载速度"""
    speed_url = os.getenv('SPEED_URL')
    timeout = float(os.getenv('SPEED_TIMEOUT', 8))
    
    try:
        parsed_url = urlparse(speed_url)
        # 构建直接使用IP的URL
        target_url = f"{parsed_url.scheme}://{ip}{parsed_url.path}?{parsed_url.query}"
        
        headers = {
            'Host': parsed_url.netloc,
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        }
        
        start_time = time.time()
        response = requests.get(
            target_url, 
            headers=headers, 
            timeout=timeout, 
            stream=True,
            verify=False
        )
        
        # 计算下载速度
        downloaded_bytes = 0
        for chunk in response.iter_content(chunk_size=1024):
            if time.time() - start_time > timeout:
                break
            if chunk:
                downloaded_bytes += len(chunk)
                
        duration = max(0.1, time.time() - start_time)  # 防止除0
        speed_mbps = (downloaded_bytes / duration) / (1024 * 1024)  # MB/s
        
        return speed_mbps
        
    except Exception as e:
        # print(f"测速失败({ip}): {str(e)[:50]}")
        return 0.0

# IP基础测试（Ping/TCP）
def test_ip_basic(ip):
    """执行基础连通性测试"""
    mode = os.getenv('MODE', 'PING').upper()
    
    if mode == "PING":
        return custom_ping(ip)
    else:  # TCP模式
        port = int(os.getenv('PORT', 443))
        return tcp_ping(ip, port, timeout=float(os.getenv('PING_TIMEOUT', 2)))

# IP综合测试（基础测试+测速）
def test_ip_full(ip):
    """执行完整测试流程：基础测试+测速"""
    # 先执行基础测试
    delay, loss = test_ip_basic(ip)
    
    # 检查基础测试是否通过
    rtt_min, rtt_max = map(int, os.getenv('RTT_RANGE').split('~'))
    loss_max = float(os.getenv('LOSS_MAX'))
    
    # 基础测试未通过
    if delay < rtt_min or delay > rtt_max or loss > loss_max:
        return (ip, delay, loss, 0.0)
    
    # 基础测试通过，进行测速
    if os.getenv('SPEED_TEST', 'True') == 'True':
        speed = speed_test(ip)
        return (ip, delay, loss, speed)
    else:
        return (ip, delay, loss, 0.0)

# 批量测速函数
def batch_speed_test(ips):
    """对通过基础测试的IP进行批量测速"""
    speed_results = []
    threads = min(int(os.getenv('SPEED_THREADS', 10)), len(ips))
    
    with ThreadPoolExecutor(max_workers=threads) as executor:
        futures = {executor.submit(speed_test, ip): ip for ip in ips}
        
        with tqdm(total=len(ips), desc="📊 测速进度", unit="IP", 
                 bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]") as pbar:
            for future in as_completed(futures):
                ip = futures[future]
                try:
                    speed = future.result()
                    speed_results.append((ip, speed))
                except Exception as e:
                    print(f"\n测速异常({ip}): {e}")
                    speed_results.append((ip, 0.0))
                finally:
                    pbar.update(1)
    
    return speed_results

####################################################
#                      主逻辑                      #
####################################################
if __name__ == "__main__":
    # 0. 初始化环境
    init_env()
    
    # 1. 打印配置参数
    print("="*60)
    print(f"{'IP网络优化器 v3.0':^60}")
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
    print(f"测速功能: {'启用' if os.getenv('SPEED_TEST') == 'True' else '禁用'}")
    if os.getenv('SPEED_TEST') == 'True':
        print(f"测速URL: {os.getenv('SPEED_URL')}")
        print(f"最低速度: {os.getenv('DOWNLOAD_MIN')} MB/s")
    print("="*60 + "\n")
    
    # 2. 获取IP段并生成随机IP
    subnets = fetch_cloudflare_ips()
    if not subnets:
        print("❌❌ 无法获取Cloudflare IP段，程序终止")
        exit(1)
    
    print(f"✅ 获取到 {len(subnets)} 个Cloudflare IP段")
    
    all_ips = []
    for _ in range(int(os.getenv('IP_COUNT'))):
        subnet = random.choice(subnets)
        all_ips.append(generate_random_ip(subnet))
    
    # 3. 多线程基础测试（Ping/TCP）
    basic_results = []
    qualified_ips = []  # 通过基础测试的IP
    with ThreadPoolExecutor(max_workers=int(os.getenv('THREADS'))) as executor:
        future_to_ip = {executor.submit(test_ip_basic, ip): ip for ip in all_ips}
        
        # 进度条配置
        with tqdm(
            total=len(all_ips), 
            desc="🚀 基础测试", 
            unit="IP",
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"
        ) as pbar:
            for future in as_completed(future_to_ip):
                ip = future_to_ip[future]
                try:
                    delay, loss = future.result()
                    basic_results.append((ip, delay, loss))
                    
                    # 检查是否符合基础要求
                    rtt_min, rtt_max = map(int, os.getenv('RTT_RANGE').split('~'))
                    loss_max = float(os.getenv('LOSS_MAX'))
                    
                    if rtt_min <= delay <= rtt_max and loss <= loss_max:
                        qualified_ips.append(ip)
                        
                except Exception as e:
                    print(f"\n🔧 基础测试异常: {e}")
                    basic_results.append((ip, float('inf'), 100.0))
                finally:
                    pbar.update(1)
    
    print(f"✅ 基础测试完成 | 通过IP数: {len(qualified_ips)}/{len(all_ips)}")
    
    # 4. 对通过基础测试的IP进行测速
    speed_results = []
    if os.getenv('SPEED_TEST') == 'True' and qualified_ips:
        speed_results = batch_speed_test(qualified_ips)
        print(f"✅ 测速完成 | 有效IP数: {len(speed_results)}")
    
    # 5. 合并测试结果
    full_results = []
    speed_dict = dict(speed_results)  # 转换为字典便于查找
    
    for ip, delay, loss in basic_results:
        # 如果该IP有测速结果，使用测速结果
        speed = speed_dict.get(ip, 0.0)
        full_results.append((ip, delay, loss, speed))
    
    # 6. 筛选优选IP
    rtt_min, rtt_max = map(int, os.getenv('RTT_RANGE').split('~'))
    loss_max = float(os.getenv('LOSS_MAX'))
    download_min = float(os.getenv('DOWNLOAD_MIN', 0))
    
    optimized_ips = [
        (ip, delay, loss, speed) for ip, delay, loss, speed in full_results
        if rtt_min <= delay <= rtt_max
        and loss <= loss_max
        and speed >= download_min
    ]
    
    # 7. 精选IP排序
    sorted_ips = sorted(
        optimized_ips,
        key=lambda x: (x[1], x[2], -x[3])  # 先按延迟，再按丢包，最后按速度（降序）
    )[:int(os.getenv('TOP_IPS_LIMIT', 15))]
    
    # 8. 保存结果
    os.makedirs('results', exist_ok=True)
    
    with open('results/all_ips.txt', 'w') as f:
        f.write("\n".join([f"{ip},{delay:.2f},{loss:.2f},{speed:.2f}" for ip, delay, loss, speed in full_results]))
    
    with open('results/optimized_ips.txt', 'w') as f:
        f.write("\n".join([f"{ip},{delay:.2f},{loss:.2f},{speed:.2f}" for ip, delay, loss, speed in optimized_ips]))
    
    with open('results/top_ips.txt', 'w') as f:
        f.write("\n".join([ip for ip, _, _, _ in sorted_ips]))
    
    # 9. 显示统计结果
    print("\n" + "="*60)
    print(f"{'🔥 测试结果统计':^60}")
    print("="*60)
    print(f"总测试IP数: {len(full_results)}")
    print(f"优选IP数量: {len(optimized_ips)}")
    print(f"精选TOP IP: {len(sorted_ips)}")
    
    if sorted_ips:
        print("\n🏆🏆【最佳IP TOP5】")
        for i, (ip, delay, loss, speed) in enumerate(sorted_ips[:5]):
            print(f"{i+1}. {ip} | 延迟:{delay:.2f}ms | 丢包:{loss:.2f}% | 速度:{speed:.2f}MB/s")
    
    print("="*60)
    print("✅ 结果已保存至 results/ 目录")
