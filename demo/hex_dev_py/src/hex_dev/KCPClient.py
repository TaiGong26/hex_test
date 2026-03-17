# import asyncio
# import websockets
# import public_api_up_pb2
# import public_api_down_pb2
# import public_api_types_pb2
import time
import socket
from kcp import KCP
import threading
from dataclasses import dataclass
from typing import Optional, Callable
import traceback
import logging
from .hex_socket import HexSocketParser, HexSocketOpcode

@dataclass
class KCPConfig:
    """KCP configuration parameters"""
    update_interval: int = 10
    no_delay: bool = True
    resend_count: int = 2
    no_congestion_control: bool = True
    send_window_size: int = 1024
    receive_window_size: int = 1024
    max_message_length: int = 2048

KCPCONFIG = KCPConfig()


# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s'
)

class KCPClient:
    def __init__(self, Config: Optional[KCPConfig] = None):
        self._kcp_config = Config if Config else KCPCONFIG
        self._running = False
        self.socket = None
        self._kcp = None
        self._kcp_send_data = b'' 
        self.recv_callback = None
        self.local_port = 0 # 新增：记录本地端口


    def set_recv_callback(self, callback):
        self.recv_callback = callback


    # 【关键修改】新增：绑定本地端口的方法
    def bind_local(self, preferred_port=0):
        """
        绑定本地 UDP 端口。
        preferred_port=0 表示让系统自动分配空闲端口。
        返回实际绑定的端口号。
        """
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 设置端口复用，防止 "Address already in use"
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        # 绑定到所有网卡的指定端口
        try:
            self.socket.bind(('0.0.0.0', preferred_port))
            self.local_port = self.socket.getsockname()[1]
            print(f"[KCP] 本地 UDP 端口绑定成功: {self.local_port}")
            print(f"[KCP] Socket 状态: {self.socket.getsockname()}")
            return self.local_port
        except Exception as e:
            print(f"[KCP] 端口绑定失败: {e}")
            raise


    def connect(self, conv: int, host: str, port: int):
        self.conv = int(conv)
        self.server_addr = (host, port)
        
        # 如果之前没有 bind_local，这里创建 socket (为了兼容旧逻辑)
        if not self.socket:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.bind(('0.0.0.0', 0))
            self.local_port = self.socket.getsockname()[1]

        self.socket.setblocking(False)
        # self.socket.connect(self.server_addr) # 可选：Connect UDP 方便发送

        self._kcp = KCP(
            conv_id=self.conv,
            no_delay=self._kcp_config.no_delay,
            update_interval=self._kcp_config.update_interval,
            resend_count=self._kcp_config.resend_count,
            no_congestion_control=self._kcp_config.no_congestion_control,
            send_window_size=self._kcp_config.send_window_size,
            receive_window_size=self._kcp_config.receive_window_size,
        )

        @self._kcp.outbound_handler
        def outbound_handler(_, data: bytes):
            try:
                # 使用已连接的 socket 发送，或者 sendto
                addr, port = self.server_addr
                self.socket.sendto(data,(addr, port))
            except Exception as e:
                print(f"[KCP_send_to] 发送错误: {e}")
                traceback.print_exc()
        
        print(f"[KCP] 初始化完成，目标: {self.server_addr}, Conv: {self.conv}")
        

    def send(self, data:bytes):
        try:
            if self._kcp:
                self._kcp.enqueue(data)
                self._kcp.flush()
                # print(f"data:{time.time()}")
            else:
                # print("1")
                pass
        except Exception as e:
            print(f"[KCP_send] 发送错误: {e}")
            traceback.print_exc()

    def send_hex(self, data, opcode: HexSocketOpcode = HexSocketOpcode.Binary):
        # print(f"scp data{data}")
        _data = data.SerializeToString()
        frame = HexSocketParser.create_header(_data, HexSocketOpcode.Binary)  # 加 4 字节头部
        self.send(frame)  # 这里是你原来的 self._kcp.enqueue + flush

    def _recv_loop(self):
        while self._running:
            try:
                data,addr = self.socket.recvfrom(2048) #从udp接收
                # print(f"data：{data} addr{addr}")
                self._kcp.receive(data)
                
                s = time.perf_counter()
                
                while True:
                    msg = self._kcp.get_received()
                    if not msg: break
                    if self.recv_callback:
                        self.recv_callback(bytes(msg))
                        # time.sleep(0.001)
                
                e = time.perf_counter()
                
                if e-s >0.0004:
                    print(f"callback recv time:{e-s:.6f}")
                # pass
            except BlockingIOError:
                time.sleep(0.001)
            except Exception as e:
                if self._running:
                    print(f"[KCP_recv] 错误: {e}")
                    traceback.print_exc()

    def _update_loop(self):
        while self._running:
            # now_ms = int(time.monotonic() * 1000)
            # if self._kcp:
            #     self._kcp.update(5)
            # time.sleep(0.01)
            self._kcp.update()

            time.sleep(0.0001)


    def start(self):
        self._running = True
        threading.Thread(target=self._recv_loop, daemon=True).start()
        threading.Thread(target=self._update_loop, daemon=True).start()
        # threading.Thread(target=self._send_loop, daemon=True).start()

    def close(self):
        self._running = False
        if self.socket:
            self.socket.close()

# --- 全局状态 ---
