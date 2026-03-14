import asyncio
import websockets
import public_api_up_pb2
import public_api_down_pb2
import public_api_types_pb2
import time
import logging
from dataclasses import dataclass
from typing import Optional

# --- 配置 ---
Server_Host = "172.18.1.76"
Server_Port = 8439
WEBSOCKET_SEND_INTERVAL = 1.0 / 1000.0  # 1kHz
SEND_FREQ = public_api_types_pb2.Rf1000Hz

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s'
)

@dataclass
class KCPConfig:
    update_interval: int = 10
    no_delay: bool = True
    resend_count: int = 2
    no_congestion_control: bool = True
    send_window_size: int = 128
    receive_window_size: int = 128

KCPCONFIG = KCPConfig()

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
        self.last_recv_time = None # 初始化为当前时间，防止心跳立即报错

    async def send_msg(self, msg):
        """发送消息的封装，增加连接状态检查"""
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
        """建立连接并发送初始配置"""
        try:
            async with websockets.connect(
                        self.url,
                        ping_interval=5,
                        ping_timeout=10,
                        close_timeout=1
                    ) as ws:
                self.ws = ws
                self._connection_alive = True
                self._stop_event.clear()
                
                # --- 连接建立后的初始配置 ---
                # 1. 设置上报频率 (只需发送一次)
                # freq_msg = self._make_set_report_frequency(SEND_FREQ)
                # await self.send_msg(freq_msg)
                # logging.info(f"已设置上报频率: {SEND_FREQ}")
                
                # 2. 通知外部任务连接已就绪
                self._connected_event.set()
                
                # --- 保持连接，运行接收和心跳任务 ---
                tasks = [self._recv_loop()]
                if self._heartBeat:
                    tasks.append(self._heartbeat_loop())
                
                await asyncio.gather(*tasks)

        except Exception as e:
            logging.error(f"连接异常: {e}")
        finally:
            # 确保退出时清理状态
            self._connection_alive = False
            self._connected_event.clear()

    async def wait_for_connection(self):
        """外部任务调用此方法等待连接就绪"""
        await self._connected_event.wait()

    async def _recv_loop(self):
        while not self._stop_event.is_set() and self._connection_alive:
            try:
                data = await self.ws.recv()
                up_msg = public_api_up_pb2.APIUp()
                up_msg.ParseFromString(data)
                self._recv_msg = up_msg
                self.last_recv_time = time.time()
                
                # 生产环境建议注释以下打印，1000Hz日志太多
                # logging.debug(f"收到状态: {up_msg}") 

            except websockets.exceptions.ConnectionClosed as e:
                logging.warning(f"[接收] 连接已关闭: {e}")
                self._connection_alive = False
                self._stop_event.set() # 通知其他循环退出
                break
            except Exception as e:
                logging.error(f"[接收] 异常: {e}")
                continue

    async def _heartbeat_loop(self):
        HEARTBEAT_TIMEOUT = 5.0
        
        while not self._stop_event.is_set() and self._connection_alive:
            await asyncio.sleep(1.0)
            
            if time.time() - self.last_recv_time > HEARTBEAT_TIMEOUT:
                logging.error("[心跳] 接收超时，判定连接断开")
                self._connection_alive = False
                self._stop_event.set()
                break

    async def start(self) -> None:
        """启动客户端入口"""
        await self._connect()

    async def stop(self) -> None:
        logging.info("正在停止控制器...")
        self._stop_event.set()

        if self.ws and self._connection_alive:
            try:
                # 发送停止指令 (B=False)
                # stop_msg = self._make_stop_msg()
                stop_msg = public_api_down_pb2.APIDown()
                # 设置速度为0
                stop_msg.base_command.api_control_initialize= False
                # 注意：这里需要序列化
                await self.ws.send(stop_msg.SerializeToString())
                logging.info("已发送停止指令 (B=False)")
            except Exception as e:
                logging.error(f"发送停止指令失败: {e}")

        if self.ws:
            await self.ws.close()
            logging.info("WebSocket 已关闭")

    # --- 内部消息构造辅助方法 ---

    # def _make_set_report_frequency(self, freq: int) -> public_api_down_pb2.APIDown:
    #     freq_msg = public_api_down_pb2.APIDown()
    #     freq_msg.protocol_major_version = 1
    #     freq_msg.protocol_minor_version = 4
    #     # 根据实际 proto 定义设置，假设字段名为 set_report_frequency
    #     # freq_msg.set_report_frequency = freq 
    #     return freq_msg

    # def _make_stop_msg(self) -> public_api_down_pb2.APIDown:
    #     """构造停止指令 (B=False)"""
    #     down_msg = public_api_down_pb2.APIDown()
    #     # 根据你的需求，结束时 B 置为 False
    #     down_msg.base_command.api_control_initialize = False
    #     return down_msg


# ==========================================
# 外部业务逻辑 (控制循环)
# ==========================================

def make_default_down_message() -> public_api_down_pb2.APIDown:
    """构造默认移动指令 (字段 A)"""
    down_msg = public_api_down_pb2.APIDown()
    down_msg.protocol_major_version = 1
    down_msg.protocol_minor_version = 4

    base_cmd = down_msg.base_command
    # 假设这是字段 A 的内容
    base_cmd.simple_move_command.xyz_speed.speed_x = 0.5
    base_cmd.simple_move_command.xyz_speed.speed_y = 0
    base_cmd.simple_move_command.xyz_speed.speed_z = 0
    return down_msg

def make_api_crl_message() -> public_api_down_pb2.APIDown:
    """构造控制使能指令 (字段 B)"""
    down_msg = public_api_down_pb2.APIDown()
    # 设置 B 为 True
    down_msg.base_command.api_control_initialize = True
    return down_msg

async def control_loop(client: WebSocketClient):
    """
    业务逻辑循环：
    1. 循环发送 A (1kHz)
    2. 触发条件：发送一次 B
    3. 恢复发送 A
    """
    # 等待连接成功
    await client.wait_for_connection()
    logging.info("连接就绪，启动控制循环...")

    # 状态定义
    STATE_NORMAL = 0       # 持续发送 A
    STATE_TRIGGER_B = 1    # 发送 B
    
    current_state = STATE_NORMAL
    has_sent_b = False     # 确保 B 只发送一次的标志
    
    # 模拟触发条件：运行 3 秒后触发 B
    start_time = time.time()
    trigger_interval = 3.0 

    while client._connection_alive and not client._stop_event.is_set():
        loop_start = time.time()
        
        # 1. 构造基础消息 (默认为 A)
        msg = make_default_down_message()
        
        # 2. 状态机逻辑
        if current_state == STATE_NORMAL:
            # 检查触发条件 (例如：时间超过 3秒 且 B 未发送过)
            if (time.time() - start_time > trigger_interval) and not has_sent_b:
                logging.info("触发条件达到，准备发送字段 B...")
                current_state = STATE_TRIGGER_B
        
        if current_state == STATE_TRIGGER_B:
            # 修改消息为 B (叠加或替换)
            # 这里演示：在 A 的基础上开启控制使能 (B=True)
            msg.base_command.api_control_initialize = True
            
            # 标记已发送，下次切回 NORMAL
            has_sent_b = True
            current_state = STATE_NORMAL
            logging.info("字段 B 已发送，恢复字段 A 循环")

        # 3. 发送
        await client.send_msg(msg)
        
        # 4. 频率控制 (模拟 1kHz)
        elapsed = time.time() - loop_start
        sleep_time = WEBSOCKET_SEND_INTERVAL - elapsed
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)

async def main():
    # 初始化客户端 (开启心跳)
    x4 = WebSocketClient(Server_Host, Server_Port, heartBeat=True)
    
    # 并发运行：客户端连接 + 控制逻辑
    # 注意：control_loop 内部会等待连接，所以可以直接 gather
    try:
        await asyncio.gather(
            x4.start(),
            control_loop(x4)
        )
    except KeyboardInterrupt:
        print("\n程序被用户中断")
    finally:
        await x4.stop()

if __name__ == "__main__":
    # 检测 Python 版本，Windows 下需要特殊处理事件循环策略 (可选)
    # import sys
    # if sys.platform == 'win32':
    #     asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    asyncio.run(main())
