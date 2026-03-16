#!/usr/bin/python

import argparse
import socket
import math
import json
from ipaddress import ip_address
import threading
import time

# parser = argparse.ArgumentParser(description="Send UDP test data.")
# parser.add_argument("--address", default="127.0.0.1", help="UDP address")
# parser.add_argument("--port", default=9870, type=int, help="UDP port")
# args = parser.parse_args()

# addr = ip_address(args.address)
# print(f"Opening IPv{addr.version} UDP socket for address {addr} on port {args.port}...")
# family = socket.AF_INET6 if addr.version == 6 else socket.AF_INET
# sock = socket.socket(family, socket.SOCK_DGRAM) # UDP
# time = 0.0

# while True:
#     sleep(0.05)
#     time += 0.05

#     data = {
#         "timestamp": time,
#         "test_data": {
#             "cos": math.cos(-time),
#             "sin": math.sin(-time)
#         }
#     }
#     sock.sendto( json.dumps(data).encode(), (args.address, args.port) )

#     test_str = "{ \
# 	  \"1252\": { \
# 	    \"timestamp\": { \
# 	      \"microsecond\": 0 \
# 	    }, \
# 	    \"value\": { \
# 	      \"current\": { \
# 		\"ampere\": null \
# 	      }, \
# 	      \"voltage\": { \
# 		\"volt\": 24.852617263793945 \
# 	      }\
# 	    }\
# 	  } }"

#     sock.sendto( test_str.encode("utf-8"), (args.address, args.port) )


class PlotjuggleDraw:
    def __init__(self, address: str = "127.0.0.1", port: int = 9870):
        self.address = address
        self.port = port
        self._running = False
        self._thread = None
        self._socket = None
        
        # 解析 IP 地址以确定使用 IPv4 还是 IPv6
        try:
            addr_obj = ip_address(self.address)
            self.family = socket.AF_INET6 if addr_obj.version == 6 else socket.AF_INET
            print(f"[UDPSender] 初始化: IPv{addr_obj.version} {self.address}:{self.port}")
            self._socket = socket.socket(self.family, socket.SOCK_DGRAM)
        except ValueError:
            print(f"[UDPSender] 警告: 无效的 IP 地址 '{self.address}'，默认使用 IPv4")
            self.family = socket.AF_INET

    def start(self):
        """启动发送线程"""
        if self._running:
            print("[UDPSender] 已经在运行中")
            return

        self._running = True
        # self._thread = threading.Thread(target=self._run_test_loop, daemon=True)
        # self._thread = threading.Thread(target=self._run_test_loop, daemon=True)
        self._thread.start()
        print("[UDPSender] 线程已启动")

    def stop(self):
        """停止发送线程"""
        self._running = False
        if self._socket:
            self._socket.close() # 关闭 socket 以打断阻塞
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        print("[UDPSender] 已停止")

    def send_data(self, data):
        self._socket.sendto(
                    data, 
                    (self.address, self.port)
                )

    def _run_test_loop(self):
        """内部发送循环逻辑"""
        try:
            # 在线程内部创建 socket
            current_time = 0.0
            
            while self._running:
                # 1. 构建并发送动态 JSON 数据
                data_dynamic = {
                    "timestamp": current_time,
                    "test_data": {
                        "cos": math.cos(current_time),
                        "sin": math.sin(current_time)
                    }
                }
                # 发送数据
                self._socket.sendto(
                    json.dumps(data_dynamic).encode(), 
                    (self.address, self.port)
                )

                # 2. 发送固定的测试字符串 (保留原逻辑)
                test_str = "{ \
                  \"1252\": { \
                    \"timestamp\": { \
                      \"microsecond\": 0 \
                    }, \
                    \"value\": { \
                      \"current\": { \
                        \"ampere\": null \
                      }, \
                      \"voltage\": { \
                        \"volt\": 24.852617263793945 \
                      }\
                    }\
                  } }"
                
                self._socket.sendto(
                    test_str.encode("utf-8"), 
                    (self.address, self.port)
                )

                # 更新时间并休眠
                current_time += 0.05
                time.sleep(0.05)
                
        except Exception as e:
            if self._running: # 如果是运行中报错才打印
                print(f"[UDPSender] 发送异常: {e}")
        finally:
            if self._socket:
                self._socket.close()

# --- 使用示例 ---
if __name__ == "__main__":
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="Send UDP test data.")
    parser.add_argument("--address", default="127.0.0.1", help="UDP address")
    parser.add_argument("--port", default=9870, type=int, help="UDP port")
    args = parser.parse_args()

    # 实例化并启动
    sender = PlotjuggleDraw()
    
    try:
        sender.start()
        # 主线程等待，防止程序退出
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n检测到中断信号...")
    finally:
        sender.stop()