import asyncio
import websockets
from typing import Optional, Callable
import time
import logging
from . import public_api_up_pb2
from . import public_api_down_pb2
from . import public_api_types_pb2
import traceback


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
            # 连接未建立，不发送
            return
        try:
            data = msg.SerializeToString()
            await self.ws.send(data)
        except websockets.exceptions.ConnectionClosedOK:
            logging.info("[发送] 连接已正常关闭")
            self._connection_alive = False
        except websockets.exceptions.ConnectionClosedError as e:
            logging.error(f"[发送] 连接异常关闭: {e}")
            traceback.print_exc()
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
                        ping_interval=15,
                        ping_timeout=60,
                        close_timeout=1
                    ) as ws:
                self.ws = ws
                self._connection_alive = True
                self._stop_event.clear()
                
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

