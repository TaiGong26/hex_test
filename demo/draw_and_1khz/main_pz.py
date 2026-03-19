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

import os
os.sched_setaffinity(0, {0})

from WebsocketClient import WebSocketClient
from KCPClient import KCPClient
# from hex_dev_py import KCPClient, WebsocketClient
from API_msg import APIMessage
from plotjuggle_draw import PlotjuggleDraw

# --- 配置 ---
Server_Host = "172.18.1.76"
Server_Port = 8439
F_1Hz_INTERVAL = 1.0
F_1kHz_INTERVAL = 1.0 / 1000.0
SEND_FREQ = public_api_types_pb2.Rf1Hz
APIMsg = APIMessage()

# TO TaiGong261 : 变量管理混乱，请尽量减少使用全局变量，全局变量一般只应该有const类型的，即永远不会变的东西。
# 同时养成习惯，写代码的时候就搞清楚每个变量的定义域在哪，何时该变量会被释放，什么变量需要保证不被释放。就目前你的变量使用来说，复杂工程出bug概率极高，且可读性很差

# control_ready_event = asyncio.Event()
control_ready_event = threading.Event()
kcp_conn_ready_event = threading.Event()
hex_parser = HexSocketParser()


KCP_BUFF_LEN = 100
kcp_recv_buff = deque(maxlen=KCP_BUFF_LEN)
kcp_raw_deque = deque(maxlen=KCP_BUFF_LEN)
recv_buf_lock = threading.Lock()
def kcp_recv_handler(data: bytes):
    try:
        # 通过双端队列完成
        kcp_raw_deque.append(data)
        # pass
    except Exception as e:
        print(f"[KCP 回调] Proto 解析失败: {e}")
      
def kcp_process_thread():
    print("[处理线程] 已启动")
    
    while True:
        try:
            # 数据出队
            if kcp_raw_deque:
                raw_data = kcp_raw_deque.popleft()
            else:
                time.sleep(0.001)
                continue

            # --- 解析逻辑 ---
            try:
                result = hex_parser.parse(raw_data)
            except ValueError as e:
                # 打印日志（调试用）
                # print(f"[Warn] 数据包解析错误，已丢弃: {e}")
                
                # TO TaiGong26 : 何意味判断？这里用的我提供的库，既然都没打算改代码，那还判断什么？我就当是ai写的了。自己去看hasattr（）函数有什么用，ai写的东西一定要弄明白它在干嘛

                # 重置解析器状态，坏包丢弃
                if hasattr(hex_parser, 'reset'):
                    hex_parser.reset()
                # 或者粗暴一点：重新实例化一个（不推荐，效率低）
                # hex_parser = HexSocketParser() 
                continue 
            
            if result is None:
                continue

            # 3. 业务处理
            for opcode, payload in result:
                if opcode == HexSocketOpcode.Binary:
                    try:
                        # 全局 deque
                        kcp_recv_buff.append(payload)
                    except Exception as e:
                        # print(f"[Warn] 协议错误，已丢弃: {e}")
                        pass 
                        
                # elif ... (其他逻辑，尽量别用 print)
            time.sleep(0.001)
                
        # TO TaiGong261 : IndexError不应该忽略，而是尝试用锁或者尝试使用原子操作去解决竞态问题，在没有crc校验的情况下怎么敢直接忽略的？且这里原本就不存在竞态，不要写无用代码。
        except IndexError:
            # 极少数情况下多线程竞争可能触发，忽略并重试
            continue

        except Exception as e:
            # 捕获其他异常，防止线程崩溃退出
            print(f"[处理线程] 异常: {e}")
            traceback.print_exc()
            time.sleep(0.1) # 发生错误时稍微停顿


# # ===== 旧版 KCP 接收处理：震荡过大：呈现集中趋势 =====
# KCP_BUFF_LEN = 20
# kcp_recv_buff = deque(maxlen=KCP_BUFF_LEN)
# recv_buf_lock = threading.Lock()

# def kcp_recv_handler(data: bytes):
#     # 先把收到的数据喂给 HexSocket 解析器
#     result = hex_parser.parse(data)  # 可能一次解析出多个帧

#     if result is None:
#         # 还没有完整帧，可能只是部分头部
#         return

#     # 遍历所有解析出来的帧
#     for opcode, payload in result:
#         # 根据 opcode 分发：通常只用 Binary
#         if opcode == HexSocketOpcode.Binary:
#             try:
#                 up_msg = public_api_up_pb2.APIUp()
#                 up_msg.ParseFromString(payload)  # 注意：这里是从 payload 解，不是原始 data
#                 if up_msg.base_status.api_control_initialized:
#                     if not control_ready_event.is_set():
#                         print(f"[KCP 回调] >>> 控制权获取成功! <<<")
#                         control_ready_event.set()
#                 # with recv_buf_lock:
#                 #     if len(kcp_recv_buff) >= KCP_BUFF_LEN:
#                 #         kcp_recv_buff.pop(0)
#                 #     kcp_recv_buff.append(up_msg)
#                 # 采用双端队列完成缓冲区操作
#                 kcp_recv_buff.append(up_msg)
#                 # print(f"[KCP 回调] 收到消息: {up_msg}")

#                 # return up_msg
#             except Exception as e:
#                 print(f"[KCP 回调] Proto 解析失败: {e}")
#         elif opcode == HexSocketOpcode.Text:
#             # 如果有文本消息，按需处理
#             print(f"[HexSocket Text] {payload.decode('utf-8', errors='replace')}")
#         elif opcode == HexSocketOpcode.Ping:
#             # 如果需要，可以回 Pong
#             print("[HexSocket] Ping received")
#         elif opcode == HexSocketOpcode.Pong:
#             print("[HexSocket] Pong received")
#         else:
#             print(f"[HexSocket] Unknown opcode {opcode}: {payload[:20]!r}")


kcp_send_lock = threading.Lock()

# TO TaiGong26 : 既然is_getkcp_port仅在这个函数内部使用了，为什么不直接放进去初始化，当作函数局部变量使用？而且变量名敲错了
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

    # TO TaiGong261 : 思考一下，为什么只需要发送一次？什么情况下才需要冗余发送，这里是否需要冗余发送？
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


                                # TO TaiGong26 : 为什么用锁？send_hex()有竞争关系吗？不要写无效代码
                                # 占位符
                                move_msg = APIMsg.set_placeholder_message()
                                with kcp_send_lock:
                                    kcp_client.send_hex(move_msg)
                                

                                # 初始化
                                move_msg = APIMsg.set_command_api_control_initialize(True)
                                
                                with kcp_send_lock:
                                    kcp_client.send_hex(move_msg)

                                kcp_connet= True

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

        # TO TaiGong26 : 这个else是否有存在必要？
        else:
            # recv_msg = ws_client.get_recv_msg()
            data = await asyncio.wait_for(ws_client.ws.recv(), timeout=1.1)
            recv_msg = public_api_up_pb2.APIUp()
            recv_msg.ParseFromString(data)
            if time.perf_counter() - last_websocket_time > 0.9:
                msg = APIMsg.set_placeholder_message()
                await ws_client.send_msg(msg)     

        # 短暂休眠防止 CPU 空转
        await asyncio.sleep(0.01)

    print("crl_loop end")


def draw_loop():
    draw = PlotjuggleDraw()
    interval = 0.01 #s
    last_draw_time = time.perf_counter()
    target_time = time.perf_counter()
    while True:
        sleep_time =  target_time - time.perf_counter()
        # print(f"sleep_time: {sleep_time:.6f}")
        if sleep_time > 0:
            time.sleep(sleep_time)
            # TO TaiGong26 : 没用的recv_buf_lock就删掉，保留旧代码的前提是不要影响新代码。且有git尽量使用git来保存，比如分个分支出去
            with recv_buf_lock:
                if kcp_recv_buff:
                    payload = kcp_recv_buff.pop()
                    draw.send_data(payload)

        # 粗略的周期计算,比实际略低0.7-0.9左右
        target_time = last_draw_time + interval
        last_draw_time = time.perf_counter()

send_queue = [APIMsg.set_simple_move_command(True,0.5,0,0),APIMsg.set_command_api_control_initialize(False)]

def kcp_send_thread(kcp_client):

    interval = 0.001 #ms
    # time.monotonic()
    last_send_time = time.perf_counter()

    # 等待同步信号：kcp连接成功
    while not kcp_conn_ready_event.is_set():
        time.sleep(0.1) # 未就绪时休眠，避免 CPU 空转
        continue

    start_f_time = None
    target_time = None
    cnt = 0

    # TO TaiGong261 : time.perf_counter()是一件耗时的操作，尽量避免短时间内重复获取多次，尽可能复用。以后必须要对每个函数调用耗时有概念
    while True:
        if cnt == 0 : 
            start_f_time =  time.perf_counter()
            target_time = time.perf_counter()
        now_time = time.perf_counter()

        # #  ======================
        # #     忙等
        # #  ======================
        # # 计算距离下一次发送还需要多久
        # sleep_time = target_time - time.perf_counter()
        # # print(f"remain_time: {remain_time:.6f}")
        # if sleep_time > 0:
        #     while time.perf_counter() < target_time:
        #         pass
        # try:
        #     move_msg = APIMsg.set_simple_move_command(True,0.5,0,0)
        #     kcp_client.send_hex(move_msg)
        # except Exception as e:
        #     print(f"发送异常: {e}")

        # target_time += interval

        # ---------------------------------------------------
        #  忙等+睡眠：看具体计算方式。
        # ---------------------------------------------------

        # target_time = last_send_time + interval*0.73  # 通过上一次发送时间(Hz不稳定)，计算下次发送时间,间隔1ms,大概700hz
        
        sleep_time =  target_time - time.perf_counter()
        if sleep_time > 0.00035:
            time.sleep(sleep_time - 0.00035)

        while target_time > time.perf_counter():
            pass

        try:
            # move_msg = APIMsg.set_simple_move_command(True,0.5,0,0)
            # TO TaiGong26 : 这怎么又想起来不用lock了？改东西改一半是大忌
            # with kcp_send_lock:
            kcp_client.send_hex(send_queue[0])
        except Exception as e:
            print(f"发送异常: {e}")

        # # 抛弃帧
        # if now_time > target_time:
        #     target_time = now_time + interval
        # else:
        #     target_time += interval
        # target_time = last_send_time + interval
        # target_time = last_send_time + 0.000915
        target_time += interval # 通过当前target累加，计算出下一个target

        print(f"kcp：{time.perf_counter() - last_send_time:.06f}")
        last_send_time = time.perf_counter()

        cnt+=1
        if cnt == 2000: break

    end_f_time = time.perf_counter()
    print(f"发送频率：{cnt / (end_f_time - start_f_time):.06f}，发送时长：{end_f_time - start_f_time:.06f}")

    move_msg = APIMsg.set_command_api_control_initialize(False)
    kcp_client.send_hex(move_msg)


draw_thread = None
def start_thread(kcp_client):
    global draw_thread
    draw_thread = threading.Thread(target=draw_loop, daemon=True)
    draw_thread.start()
    kcp_thread = threading.Thread(target=kcp_send_thread, args=(kcp_client,), daemon=True)
    kcp_thread.start()
    t_process = threading.Thread(target=kcp_process_thread, daemon=True)
    t_process.start()


async def main():
    x4_ws_client = WebSocketClient(Server_Host, Server_Port, heartBeat=True)
    x4_kcp_client = KCPClient()
    input()
    
    # TO TaiGong261 : 线程里面套线程，可读性很差，同时也不便于线程管理。线程数越多，系统上下文切换还有GIL锁竞争越激烈，代码效率越低。
    try:
        await asyncio.gather(
            x4_ws_client.start(),
            crl_loop(x4_ws_client, x4_kcp_client)
        )
    except KeyboardInterrupt:
        
        print("\n程序被用户中断")

    finally:
        
        move_msg = APIMsg.set_command_api_control_initialize(False)
        # TO TaiGong26 : 从kcp发和从websocket发是一样的，又是重复代码，而且 x4_ws_client.stop()里面又调用一遍
        x4_kcp_client.send_hex(move_msg)
        await x4_ws_client.send_msg(move_msg)
        time.sleep(2) 
        print("finally: 1!")
        # TO TaiGong261 : 搞半天就只有crl_loop()里面由_stop_event来控制停止，start_thread()里面的线程就全部放飞了吗？但凡python没有内存回收机制，你内存就爆炸了。
        await x4_ws_client.stop()


if __name__ == "__main__":
    # 确保先初始化消息队列
    # msg_init()
    asyncio.run(main())


