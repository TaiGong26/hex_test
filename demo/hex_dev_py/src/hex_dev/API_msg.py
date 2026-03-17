from . import public_api_up_pb2
from . import public_api_down_pb2
from . import public_api_types_pb2

class APIMessage:
    def __init__(self):
        # self.msg_type = msg_type
        # self.msg = msg
        pass

    def set_report_frequency(self, freq: int):
        msg = public_api_down_pb2.APIDown()
        msg.protocol_major_version = 1
        msg.protocol_minor_version = 4
        if freq == 1000:
            msg.set_report_frequency = public_api_types_pb2.Rf1000Hz
        elif freq == 500:
            msg.set_report_frequency = public_api_types_pb2.Rf500Hz
        elif freq == 250:
            msg.set_report_frequency = public_api_types_pb2.Rf250Hz
        elif freq == 100:
            msg.set_report_frequency = public_api_types_pb2.Rf100Hz
        elif freq == 50:
            msg.set_report_frequency = public_api_types_pb2.Rf50Hz
        elif freq == 1:
            msg.set_report_frequency = public_api_types_pb2.Rf1Hz
        else:
            raise ValueError(f"Invalid frequency: {freq}")
        
        return msg
    
    def set_enable_kcp(self, enable: bool, port: int = 0):
        msg = public_api_down_pb2.APIDown()
        msg.protocol_major_version = 1
        msg.protocol_minor_version = 4
        msg.enable_kcp.client_peer_port = port

        msg.enable_kcp.kcp_config.window_size_snd_wnd = 64
        msg.enable_kcp.kcp_config.window_size_rcv_wnd = 64
        msg.enable_kcp.kcp_config.interval_ms = 10
        msg.enable_kcp.kcp_config.no_delay = True
        msg.enable_kcp.kcp_config.nc = True
        msg.enable_kcp.kcp_config.resend = 2
        
        return msg
    
    def set_command_api_control_initialize(self, control_initialize: bool = False):
        msg = public_api_down_pb2.APIDown()
        msg.protocol_major_version = 1
        msg.protocol_minor_version = 4
        msg.base_command.api_control_initialize=control_initialize
        return msg
    
    def set_simple_move_command(self, control_initialize: bool = False, X: float = 0.0, Y: float = 0.0, Z: float = 0.0):
        msg = public_api_down_pb2.APIDown()
        msg.protocol_major_version = 1
        msg.protocol_minor_version = 4
        if control_initialize:
            msg.base_command.simple_move_command.xyz_speed.speed_x=X
            msg.base_command.simple_move_command.xyz_speed.speed_y=Y
            msg.base_command.simple_move_command.xyz_speed.speed_z=Z
        else:
            msg.base_command.simple_move_command.xyz_speed.speed_x=0.0
            msg.base_command.simple_move_command.xyz_speed.speed_y=0.0
            msg.base_command.simple_move_command.xyz_speed.speed_z=0.0
        return msg
    
    def set_placeholder_message(self):
        msg = public_api_down_pb2.APIDown()
        msg.placeholder_message = True
        return msg
    