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

from WebsocketClient import WebSocketClient
from KCPClient import KCPClient
from API_msg import APIMessage
from plotjuggle_draw import PlotjuggleDraw

# --- 配置 ---
Server_Host = "172.18.1.76"
Server_Port = 8439
F_1Hz_INTERVAL = 1.0
F_1kHz_INTERVAL = 1.0 / 1000.0
SEND_FREQ = public_api_types_pb2.Rf1Hz
APIMsg = APIMessage()


# control_ready_event = asyncio.Event()
control_ready_event = threading.Event()
kcp_conn_ready_event = threading.Event()
hex_parser = HexSocketParser()


KCP_BUFF_LEN = 20
kcp_recv_buff = deque(maxlen=KCP_BUFF_LEN)
recv_buf_lock = threading.Lock()

def kcp_recv_handler(data: bytes):
    # 先把收到的数据喂给 HexSocket 解析器
    result = hex_parser.parse(data)  # 可能一次解析出多个帧

    if result is None:
        # 还没有完整帧，可能只是部分头部
        return

    # 遍历所有解析出来的帧
    for opcode, payload in result:
        # 根据 opcode 分发：通常只用 Binary
        if opcode == HexSocketOpcode.Binary:
            try:
                up_msg = public_api_up_pb2.APIUp()
                up_msg.ParseFromString(payload)  # 注意：这里是从 payload 解，不是原始 data
                if up_msg.base_status.api_control_initialized:
                    if not control_ready_event.is_set():
                        print(f"[KCP 回调] >>> 控制权获取成功! <<<")
                        control_ready_event.set()
                # with recv_buf_lock:
                #     if len(kcp_recv_buff) >= KCP_BUFF_LEN:
                #         kcp_recv_buff.pop(0)
                #     kcp_recv_buff.append(up_msg)
                # 采用双端队列完成缓冲区操作
                kcp_recv_buff.append(up_msg)
                # print(f"[KCP 回调] 收到消息: {up_msg}")

                # return up_msg
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


is_getkcp_port = False
kcp_connet = False
kcp_init_sent = False  # 标记初始化指令是否已发送
async def crl_loop(ws_client: WebSocketClient, kcp_client: KCPClient):
    last_websocket_time = time.perf_counter()
    global last_ws_send_time, last_kcp_send_time, kcp_init_sent, is_getkcp_port, kcp_connet
    """
    主线程，控制内容:
        kcp发送频率：1kHz
        websocket：控制和接收消息，1Hz

    
    具体实现：
        1. 等待 WebSocket 连接成功
        2. 绑定本地端口 (配置中指定的 8439)
        3. 发送占位符
        4. 主循环：
            4.1 websocket检查接收消息，若收到服务端kcp消息，进行处理和转换频率
            4.2 kcp发送消息
        
    """

    # 1. 等待 WebSocket 连接成功
    await ws_client.wait_for_connection()
    logging.info("[业务] WebSocket 已连接，开始握手流程...")

    # 绑定本地端口 (配置中指定的 8439)
    kcp_client.bind_local(10025)

    # 发送占位符
    # await ws_client.send_msg()

    # 发送kcp链接请求：
    msg = APIMsg.set_enable_kcp(True, 10025)
    await ws_client.send_msg(msg)

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
            
            # print(f"recv_msg:{recv_msg}")
            if recv_msg:
                # 根据 Proto 定义检查 kcp_server_status 字段
                # optional KcpServerStatus kcp_server_status = 19;
                if recv_msg.HasField('kcp_server_status'):
                    # 提取端口 (server_port)
                    kcp_port = recv_msg.kcp_server_status.server_port
                    # 提取会话ID (session_id) 作为 conv
                    kcp_conv = recv_msg.session_id
                    # print("具备kcp_server_status 字段")
                    
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
                                move_msg = APIMsg.set_placeholder_message()
                                kcp_client.send_hex(move_msg)
                                # ttg_last_time = time.time()
                                

                                # 初始化
                                move_msg = APIMsg.set_command_api_control_initialize(True)
                                kcp_client.send_hex(move_msg)

                                kcp_connet= True

                                # msg = APIMsg.set_report_frequency(1)
                                # await ws_client.send_msg(msg)
                                kcp_conn_ready_event.set()

                                print("初始化完成，启动线程")

                                start_thread(kcp_client)
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

                # 下一阶段，开启同步，开启kcp相关收、发线程

        # ==========================================
        # 阶段二：运行控制
        # ==========================================
        else:
            # recv_msg = ws_client.get_recv_msg()
            data = await asyncio.wait_for(ws_client.ws.recv(), timeout=1.1)
            recv_msg = public_api_up_pb2.APIUp()
            recv_msg.ParseFromString(data)
            if time.perf_counter() - last_websocket_time > 0.9:
                msg = APIMsg.set_placeholder_message()
                await ws_client.send_msg(msg)

            # # --- KCP 控制指令发送 (1000Hz) ---
            # if current_time - last_kcp_send_time >= 0.001:
            #     # print(f"time{current_time - last_kcp_send_time}")
            #     # if len(kcp_send_queue) > 0:
            #     #     # 1. 发送移动指令 (队列第一条)
            #     #     move_msg = kcp_send_queue[0]   # speed command
            #     #     kcp_client.send_hex(move_msg)

                
                # move_msg = APIMsg.set_simple_move_command(True,0.5,0,0)   # speed command
                # kcp_client.send_hex(move_msg)
                        
            #     last_kcp_send_time = time.time()
            

        # 短暂休眠防止 CPU 空转
        await asyncio.sleep(0.01)

    print("crl_loop end")




def draw_loop():
    draw = PlotjuggleDraw()
    last_draw_time = time.monotonic()
    while True:
        if time.monotonic() - last_draw_time >= 0.02:
            with recv_buf_lock:
                if kcp_recv_buff:
                    msg = kcp_recv_buff.pop()
                    draw.send_data(msg.SerializeToString())

            time.sleep(time.monotonic() - last_draw_time)



def kcp_send_thread(kcp_client):

    # time.monotonic()

    last_send_time = time.perf_counter()



    while True:
        # 等同步
        if not kcp_conn_ready_event.is_set():
            time.sleep(0.1) # 未就绪时休眠，避免 CPU 空转
            continue
        
        

        # 周期计算
        if time.perf_counter() - last_send_time <= 0.001:
            # 发
            move_msg = APIMsg.set_simple_move_command(True,0.5,0,0)   # speed command
            kcp_client.send_hex(move_msg)

            time.sleep(time.perf_counter() - last_send_time)
            print(f"kcp send time{time.perf_counter() - last_send_time}")
            last_send_time = time.perf_counter()
        # else:
        #     time.sleep(0.5)


draw_thread = None
def start_thread(kcp_client):
    global draw_thread
    draw_thread = threading.Thread(target=draw_loop, daemon=True)
    kcp_thread = threading.Thread(target=kcp_send_thread, args=(kcp_client,), daemon=True)
    draw_thread.start()
    kcp_thread.start()


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
    finally:
        await x4_ws_client.stop()

if __name__ == "__main__":
    # 确保先初始化消息队列
    # msg_init()
    asyncio.run(main())


