import asyncio
import websockets
import public_api_up_pb2
import public_api_down_pb2
import public_api_types_pb2
import time
import socket
from kcp import KCP
import threading
from dataclasses import dataclass
from typing import Optional, Callable
import traceback
import logging
import queue
from hex_socket import HexSocketParser, HexSocketOpcode
from collections import deque
from plotjuggle_draw import PlotjuggleDraw


# --- 配置 ---
Server_Host = "172.18.1.76"
Server_Port = 8439
F_1Hz_INTERVAL = 1.0
F_1kHz_INTERVAL = 1.0 / 1000.0
SEND_FREQ = public_api_types_pb2.Rf1Hz

cnt=0

@dataclass
class KCPConfig:
    update_interval: int = 10
    no_delay: bool = True
    resend_count: int = 2
    no_congestion_control: bool = True
    send_window_size: int = 64
    receive_window_size: int = 64

KCPCONFIG = KCPConfig()


# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s'
)

class WebSocketClient:
    def __init__(self, addr: str, port: int, heartBeat: bool = False):
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._recv_msg = None
        self._connection_alive = False
        self._heartBeat = heartBeat
        self._stop_event = asyncio.Event()
        self._connected_event = asyncio.Event()
        
        self.addr = addr
        self.port = port
        self.url = f"ws://{addr}:{port}"
        
        # 【修正1】初始化为当前时间，防止心跳计算报错
        self.last_recv_time = time.time() 

    async def send_msg(self, msg):
        if not self._connection_alive or not self.ws:
            return
        try:
            data = msg.SerializeToString()
            await self.ws.send(data)
        except websockets.exceptions.ConnectionClosedOK:
            logging.info("[发送] 连接已正常关闭")
            self._connection_alive = False
        except websockets.exceptions.ConnectionClosedError as e:
            logging.error(f"[发送] 连接异常关闭: {e}")
            self._connection_alive = False
        except Exception as e:
            logging.error(f"[发送] 其他异常: {e}")

    def get_recv_msg(self):
        return self._recv_msg

    async def _connect(self) -> None:
        try:
            # 【修正2】核心逻辑修正：连接必须在 with 块内保持活跃
            async with websockets.connect(
                        self.url,
                        ping_interval=5,
                        ping_timeout=10,
                        close_timeout=1
                    ) as ws:
                self.ws = ws
                self._connection_alive = True
                self._stop_event.clear()
                
                # # 连接成功，发送初始配置
                # freq_msg = public_api_down_pb2.APIDown()
                # freq_msg.protocol_major_version = 1
                # freq_msg.protocol_minor_version = 4
                # await self.send_msg(freq_msg)
                
                # 通知外部任务可以开始发送了
                self._connected_event.set()
                print("read to send")

                while True:
                    await asyncio.sleep(1.0)
                    pass
                # # 【关键】在这里启动接收和心跳循环，阻塞在这里，防止退出 with 块
                tasks = [self._recv_loop()]
                if self._heartBeat:
                    tasks.append(self._heartbeat_loop())
                await asyncio.gather(*tasks)
                
        except Exception as e:
            logging.error(f"连接发生异常: {e}")
        finally:
            # 确保退出时状态正确
            self._connection_alive = False
            self._connected_event.clear()

    async def wait_for_connection(self):
        await self._connected_event.wait()

    async def _recv_loop(self):
        while not self._stop_event.is_set() and self._connection_alive:
            try:
                data = await self.ws.recv()
                up_msg = public_api_up_pb2.APIUp()
                up_msg.ParseFromString(data)
                self._recv_msg = up_msg
                self.last_recv_time = time.time()
            except websockets.exceptions.ConnectionClosed as e:
                logging.warning(f"[接收] 连接已关闭: {e}")
                self._connection_alive = False
                self._stop_event.set()
                traceback.print_exc()
                break
            except Exception as e:
                logging.error(f"[接收] 异常: {e}")
                continue

    async def _heartbeat_loop(self):
        HEARTBEAT_TIMEOUT = 5.0
        while not self._stop_event.is_set() and self._connection_alive:
            await asyncio.sleep(1.0)
            if time.time() - self.last_recv_time > HEARTBEAT_TIMEOUT:
                logging.error("[心跳] 接收超时")
                self._connection_alive = False
                self._stop_event.set()
                break

    async def start(self) -> None:
        await self._connect()

    async def stop(self) -> None:
        logging.info("正在停止控制器...")
        self._stop_event.set()

        if self.ws and self._connection_alive:
            try:
                stop_msg = public_api_down_pb2.APIDown()
                stop_msg.base_command.api_control_initialize = False
                # 【修正3】必须序列化
                await self.ws.send(stop_msg.SerializeToString())
            except Exception as e:
                logging.error(f"发送停止指令失败: {e}")

        if self.ws:
            await self.ws.close()
            logging.info("WebSocket 已关闭")


class KCPClient:
    def __init__(self, Config: Optional[KCPConfig] = None):
        self._kcp_config = Config if Config else KCPCONFIG
        self._running = False
        self.socket = None
        self._kcp = None
        self._kcp_send_data = b'' 
        self.recv_callback = None
        self.local_port = 0 # 新增：记录本地端口
        self.last_recv_time=time.monotonic()


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
                print(f"[KCP] 发送错误: {e}")
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
            print(f"[KCP] 发送错误: {e}")
            traceback.print_exc()

    def send_hex(self, data, opcode: HexSocketOpcode = HexSocketOpcode.Binary):
        # print(f"scp data{data}")
        _data = data.SerializeToString()
        frame = HexSocketParser.create_header(_data, HexSocketOpcode.Binary)  # 加 4 字节头部
        self.send(frame)  # 这里是你原来的 self._kcp.enqueue + flush

    def _recv_loop(self):
        while self._running:
            try:
                data,addr = self.socket.recvfrom(2048)
                # print(f"data：{data} addr{addr}")
                # print(f"recv_time：{ time.monotonic() - self.last_recv_time}")
                self.last_recv_time=time.monotonic()
                self._kcp.receive(data)
                while True:
                    msg = self._kcp.get_received()
                    if not msg: break
                    if self.recv_callback:
                        self.recv_callback(bytes(msg))
                # pass
            except BlockingIOError:
                time.sleep(0.001)
            except Exception as e:
                if self._running:
                    print(f"[KCP] 错误: {e}")
                    traceback.print_exc()

    def _update_loop(self):
        while self._running:
            now_ms = int(time.monotonic() * 1000)
            if self._kcp:
                self._kcp.update(10)
            time.sleep(0.001)

    def start(self):
        self._running = True
        threading.Thread(target=self._recv_loop, daemon=True).start()
        threading.Thread(target=self._update_loop, daemon=True).start()
        # threading.Thread(target=self._send_loop, daemon=True).start()

    def close(self):
        self._running = False
        if self.socket:
            self.socket.close()


Draw = PlotjuggleDraw()
# --- 全局状态 ---
control_ready_event = asyncio.Event()

hex_parser = HexSocketParser()
last_recv_time = time.time()

KCP_BUFF_LEN = 20
kcp_recv_buff = deque(maxlen=KCP_BUFF_LEN)
def kcp_recv_handler(data: bytes):
    global last_recv_time
    # 先把收到的数据喂给 HexSocket 解析器
    result = hex_parser.parse(data)  # 可能一次解析出多个帧

    if result is None:
        # 还没有完整帧，可能只是部分头部
        return

    # print(f"recv_time：{ time.time() - last_recv_time}")
    last_recv_time = time.time()
    # 遍历所有解析出来的帧
    for opcode, payload in result:
        # 根据 opcode 分发：通常只用 Binary
        if opcode == HexSocketOpcode.Binary:
            try:
                up_msg = public_api_up_pb2.APIUp()
                up_msg.ParseFromString(payload)  # 注意：这里是从 payload 解，不是原始 data
                # print(f"[KCP 回调] 收到 Binary 帧: {up_msg}")
                if up_msg.base_status.api_control_initialized:
                    if not control_ready_event.is_set():
                        print(f"[KCP 回调] >>> 控制权获取成功! <<<")
                        control_ready_event.set()

                # if len(kcp_recv_buff) >= KCP_BUFF_LEN:
                #     kcp_recv_buff.pop()
                #     print(len(kcp_recv_buff))
                kcp_recv_buff.append(up_msg)
                # print(len(kcp_recv_buff))
                Draw.send_data(payload)
            except Exception as e:
                print(f"[KCP 回调] Proto 解析失败: {e}")
        elif opcode == HexSocketOpcode.Text:
            # 如果有文本消息，按需处理
            print(f"[HexSocket Text] {payload.decode('utf-8', errors='replace')}")
        elif opcode == HexSocketOpcode.Ping:
            # 如果需要，可以回 Pong
            print("[HexSocket] Ping received")
        elif opcode == HexSocketOpcode.Pong:
            print("[HexSocket] Pong received")
        else:
            print(f"[HexSocket] Unknown opcode {opcode}: {payload[:20]!r}")



# 构建websocket发送队列
ws_send_queue = []

# 构建kcp发送队列
kcp_send_queue = []

is_getkcp_port = False

"""
1. websocket发送，知道收到kcp端口和conv
2. 接收到后可以通知kcp启动并发送，尝试连接
3. 此时websocket转成1Hz发送频率，并不断的heatbeat
4. kcp尝试连接
"""

def msg_init():

    msg = public_api_down_pb2.APIDown()
    msg.protocol_major_version = 1
    msg.protocol_minor_version = 4

    # 设置 KCP 端口
    msg.enable_kcp.client_peer_port = 10025

    # 设置 KCP 参数
    msg.enable_kcp.kcp_config.window_size_snd_wnd = 64
    msg.enable_kcp.kcp_config.window_size_rcv_wnd = 64
    msg.enable_kcp.kcp_config.interval_ms = 10
    msg.enable_kcp.kcp_config.no_delay = True
    msg.enable_kcp.kcp_config.nc = True
    msg.enable_kcp.kcp_config.resend = 2

    # 加入 WebSocket 队列
    ws_send_queue.append(msg)


    # ==========================================
    # 第二阶段：WebSocket 发送占位符
    # ==========================================
    msg = public_api_down_pb2.APIDown()
    msg.protocol_major_version = 1
    msg.protocol_minor_version = 4
    msg.placeholder_message = True

    # ==========================================
    # 第三阶段：
    # ==========================================
    msg = public_api_down_pb2.APIDown()
    msg.protocol_major_version = 1
    msg.protocol_minor_version = 4
    msg.set_report_frequency =SEND_FREQ

    # 加入 WebSocket 队列
    ws_send_queue.append(msg)


    # ==========================================
    # KCP 部分：发送移动指令
    # ==========================================
    msg = public_api_down_pb2.APIDown()
    msg.protocol_major_version = 1
    msg.protocol_minor_version = 4

    # 设置移动指令
    base_cmd = msg.base_command
    base_cmd.simple_move_command.xyz_speed.speed_x = 0.5
    base_cmd.simple_move_command.xyz_speed.speed_y = 0
    base_cmd.simple_move_command.xyz_speed.speed_z = 0

    # 加入 KCP 队列
    kcp_send_queue.append(msg)


    # ==========================================
    # KCP 部分：发送初始化指令
    # ==========================================
    msg = public_api_down_pb2.APIDown()
    msg.protocol_major_version = 1
    msg.protocol_minor_version = 4

    # 设置初始化
    msg.base_command.api_control_initialize = True

    # 加入 KCP 队列
    kcp_send_queue.append(msg)


    # ==========================================
    # KCP 部分：占位符
    # ==========================================

    dmsg = public_api_down_pb2.APIDown() 
    msg.protocol_major_version = 1
    msg.protocol_minor_version = 4
    dmsg.placeholder_message  = True
    
    kcp_send_queue.append(dmsg)


    # ==========================================
    # KCP 部分：频率上报
    # ==========================================

    msg = public_api_down_pb2.APIDown()
    msg.protocol_major_version = 1
    msg.protocol_minor_version = 4
    msg.set_report_frequency =public_api_types_pb2.Rf1Hz
    kcp_send_queue.append(dmsg)


# --- 全局状态补充 ---
last_ws_send_time = 0
last_kcp_send_time = 0
kcp_init_sent = False  # 标记初始化指令是否已发送

ttg_last_time=0
kcp_connet = False




async def crl_loop(ws_client: WebSocketClient, kcp_client: KCPClient):
    global last_ws_send_time, last_kcp_send_time, kcp_init_sent, is_getkcp_port, kcp_connet
    
    # 1. 等待 WebSocket 连接成功
    await ws_client.wait_for_connection()
    logging.info("[业务] WebSocket 已连接，开始握手流程...")

    # if current_time - last_ws_send_time >= 0.1:

    # 绑定本地端口 (配置中指定的 8439)
    kcp_client.bind_local(10025)

    if len(ws_send_queue) > 0:
        await ws_client.send_msg(ws_send_queue[0])

    # 2. 主循环
    while ws_client._connection_alive and not ws_client._stop_event.is_set():
        current_time = time.time()
        
        # ==========================================
        # 阶段一：握手获取 KCP 信息
        # ==========================================
        if not is_getkcp_port:
            # 只要没收到 KCP 信息，就一直发送配置请求 (队列第一条)
            # 控制发送频率，避免刷屏，例如 10Hz

            # 检查接收消息
            # recv_msg = ws_client.get_recv_msg()

            data = await ws_client.ws.recv()
            recv_msg = public_api_up_pb2.APIUp()
            recv_msg.ParseFromString(data)
            
            print(f"recv_msg:{recv_msg}")
            if recv_msg:
                # 根据 Proto 定义检查 kcp_server_status 字段
                # optional KcpServerStatus kcp_server_status = 19;
                if recv_msg.HasField('kcp_server_status'):
                    # 提取端口 (server_port)
                    kcp_port = recv_msg.kcp_server_status.server_port
                    
                    # 提取会话ID (session_id) 作为 conv
                    kcp_conv = recv_msg.session_id
                    
                    if kcp_port > 0 and kcp_conv != 0:
                        logging.info(f"[业务] 收到 KCP 握手信息: Port={kcp_port}, Conv={kcp_conv}")
                        
                        # 启动 KCP
                        try:
                            if kcp_connet == False:
                                
                                # 连接服务器
                                kcp_client.connect(kcp_conv, Server_Host, kcp_port)
                                kcp_client.set_recv_callback(kcp_recv_handler)
                                kcp_client.start()

                                # 占位符
                                move_msg = kcp_send_queue[2]
                                kcp_client.send_hex(move_msg)
                                # ttg_last_time = time.time()
                                

                                # 初始化
                                move_msg = kcp_send_queue[1]
                                kcp_client.send_hex(move_msg)
                                
                                move_msg = kcp_send_queue[3]
                                kcp_client.send_hex(move_msg)

                                kcp_connet= True

                                # msg = public_api_down_pb2.APIDown()
                                # msg.protocol_major_version = 1
                                # msg.protocol_minor_version = 4
                                # msg.set_report_frequency = public_api_types_pb2.Rf50Hz
                                
                                # await ws_client.send_msg(msg)
                            
                            # 标记状态切换
                            is_getkcp_port = True
                            # 清空已处理的接收缓存
                            ws_client._recv_msg = None
                            
                            # 重置计时器，准备进入下一阶段
                            last_ws_send_time = current_time
                            last_kcp_send_time = current_time
                            
                        except Exception as e:
                            logging.error(f"[业务] KCP 启动失败: {e}")
                            return
                
                # 如果收到了消息但没有 KCP 信息，清空缓存继续等待
                ws_client._recv_msg = None

        # ==========================================
        # 阶段二：运行控制
        # ==========================================
        else:
            # recv_msg = ws_client.get_recv_msg()
            data = await ws_client.ws.recv()
            recv_msg = public_api_up_pb2.APIUp()
            recv_msg.ParseFromString(data)

            # if recv_msg:
            #     print(recv_msg.base_status.api_control_initialized)
                # print(recv_msg.report_frequency)
            # --- WebSocket 心跳 (1Hz) ---
            # # 发送队列第二条 (占位符)
            if current_time - last_ws_send_time >= 1.0:
                # move_msg = kcp_send_queue[0]
                # kcp_client.send_hex(ws_send_queue[1])
            
                await ws_client.send_msg(ws_send_queue[1])
                last_ws_send_time = current_time

            # --- KCP 控制指令发送 (100Hz) ---
            if current_time - last_kcp_send_time >= 0.01:
                # print(f"time{current_time - last_kcp_send_time}")
                if len(kcp_send_queue) > 0:
                    # 1. 发送移动指令 (队列第一条)
                    move_msg = kcp_send_queue[0]   # speed command
                    kcp_client.send_hex(move_msg)
                    
                    # move_msg = kcp_send_queue[0]
                    # await ws_client.send_msg(move_msg)


                    # print(f"msg:")
                    # print(f"ttg{time.time() - ttg_last_time}")
                    # 2. 发送初始化指令 (队列第二条)
                    # 逻辑：发送一定频率后发送。这里简化为启动后发送一次
                    if not kcp_init_sent and len(kcp_send_queue) > 1:
                        init_msg = kcp_send_queue[1]   # init = True
                        kcp_client.send_hex(init_msg)
                        # init_msg = kcp_send_queue[1]
                        # await ws_client.send_msg(init_msg)


                        kcp_init_sent = True
                        logging.info("[业务] 已发送 KCP 初始化指令")
                        ttg_last_time = time.time()
                        # print(f"ttg{time.time() - ttg_last_time}")
                        
                last_kcp_send_time = time.time()
            

        # 短暂休眠防止 CPU 空转
        await asyncio.sleep(0.01)

    print("crl_loop end")



async def main():
    x4_ws_client = WebSocketClient(Server_Host, Server_Port, heartBeat=True)
    x4_kcp_client = KCPClient()
    try:
        await asyncio.gather(
            x4_ws_client.start(),
            crl_loop(x4_ws_client, x4_kcp_client)
        )
    except KeyboardInterrupt:
        print("\n程序被用户中断")
        print(f"kcp_recv_buff: {kcp_recv_buff}")
    finally:

        msg = public_api_down_pb2.APIDown()
        msg.base_command.api_control_initialize = False
        x4_kcp_client.send_hex(msg)
        time.sleep(1)


        await x4_ws_client.stop()

if __name__ == "__main__":
    # 确保先初始化消息队列
    msg_init()
    asyncio.run(main())