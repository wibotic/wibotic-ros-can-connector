#!/usr/bin/env python

# use `pip install future uavcan` before running on python 2

# ROS Imports
import rospy
from std_msgs.msg import String
from wibotic_msg import msg, srv

# Other Imports
import uavcan
import threading
from threading import Thread
import time
import queue
import signal
import sys
import os

# Global state and constants shared between threads
_uav_incoming = queue.Queue()
_uav_incoming_param = queue.Queue(1)
_uav_outgoing_param = queue.Queue()
DSDL_PATH = os.path.dirname(__file__) + "/uavcan_vendor_specific_types/wibotic"
PARAMETER_REQ_TIMEOUT = 3
_threads = []
_shutting_down = False


class ParamRead(object):
    __slots__ = "param_id"


class ParamReadResponse(object):
    __slots__ = ("request", "value", "status")


class ParamWrite(object):
    __slots__ = ("param_id", "value")


class ParamWriteResponse(object):
    __slots__ = ("request", "status")


class Shutdown(Exception):
    pass


class ROSNodeThread(Thread):
    class Node:
        def sender(self):
            pub = rospy.Publisher("~wibotic_info", msg.WiBoticInfo, queue_size=10)
            while not rospy.is_shutdown():
                incoming_data = _uav_incoming.get()

                uavcan_dsdl_type = uavcan.get_uavcan_data_type(incoming_data)
                unpacked_data = {}
                for field in uavcan_dsdl_type.fields:
                    unpacked_data[field.name] = getattr(incoming_data, field.name)
                packaged_data = msg.WiBoticInfo(**unpacked_data)

                rospy.loginfo(packaged_data)
                pub.publish(packaged_data)

        def handle_param_read(self, req):
            queued_read = ParamRead()
            queued_read.param_id = req.param_id
            _uav_outgoing_param.put(queued_read)
            try:
                response = _uav_incoming_param.get(
                    block=True, timeout=PARAMETER_REQ_TIMEOUT
                )
                return [response.status, response.value]
            except:
                return 0  # Failure

        def handle_param_write(self, req):
            queued_write = ParamWrite()
            queued_write.param_id = req.param_id
            queued_write.value = req.value
            _uav_outgoing_param.put(queued_write)
            try:
                response = _uav_incoming_param.get(
                    block=True, timeout=PARAMETER_REQ_TIMEOUT
                )
                return response.status
            except:
                return 0  # Failure

    def __init__(self):
        rospy.init_node("wibotic_connector_can")
        super(ROSNodeThread, self).__init__()
        self.daemon = True

    def run(self):
        rospy.loginfo("ROS Thread Initialized")
        node = ROSNodeThread.Node()
        try:
            rospy.Service("~read_parameter", srv.ReadParameter, node.handle_param_read)
            rospy.Service(
                "~write_parameter", srv.WriteParameter, node.handle_param_write
            )
            node.sender()
        except rospy.ROSInterruptException:
            pass
        rospy.loginfo("ROS Thread Finished")


class UAVCanNodeThread(Thread):
    class Node:
        outstanding_param_request = threading.Semaphore()
        param_req_in_progress = None

        def __init__(self, can_interface, node_id):
            uavcan.load_dsdl(DSDL_PATH)
            node_info = uavcan.protocol.GetNodeInfo.Response()
            node_info.name = "com.wibotic.ros_connector"
            node_info.software_version.major = 1
            self.uavcan_node = uavcan.make_node(
                can_interface, node_id=node_id, node_info=node_info
            )
            self.uavcan_node.add_handler(
                uavcan.thirdparty.wibotic.WiBoticInfo, self.wibotic_info_callback
            )
            self.uavcan_node.add_handler(
                uavcan.thirdparty.wibotic.WiBoticCommand, self.wibotic_command_callback
            )
            self.uavcan_node.mode = uavcan.protocol.NodeStatus().MODE_OPERATIONAL

        def wibotic_info_callback(self, event):
            _uav_incoming.put(event.transfer.payload)

        def wibotic_command_callback(self, event):
            if type(self.param_req_in_progress) is ParamRead:
                response = ParamReadResponse()
                response.request = self.param_req_in_progress
                response.value = event.transfer.payload.payload
                response.status = 5  # TODO: return a more accurate code than "Success"
                _uav_incoming_param.put(response)
            elif type(self.param_req_in_progress) is ParamWrite:
                response = ParamWriteResponse()
                response.request = self.param_req_in_progress
                response.status = event.transfer.payload.payload
                _uav_incoming_param.put(response)
            self.param_req_in_progress = None
            self.outstanding_param_request.release()

        def send_pending(self):
            try:
                while True:
                    send_item = _uav_outgoing_param.get_nowait()
                    if type(send_item) is ParamRead:
                        message = uavcan.thirdparty.wibotic.WiBoticRead()
                        message.parameter_id = send_item.param_id
                        self.outstanding_param_request.acquire()
                        self.param_req_in_progress = send_item
                        self.uavcan_node.broadcast(message)
                    elif type(send_item) is ParamWrite:
                        message = uavcan.thirdparty.wibotic.WiBoticCommand()
                        message.command = send_item.param_id
                        message.payload = send_item.value
                        self.outstanding_param_request.acquire()
                        self.param_req_in_progress = send_item
                        self.uavcan_node.broadcast(message)
            except:
                pass

        def check_shutdown(self):
            if _shutting_down:
                self.uavcan_node.close()
                raise Shutdown("Node Shutdown")

    def __init__(self):
        super(UAVCanNodeThread, self).__init__()
        self.daemon = True

    def run(self):
        rospy.loginfo("UAVCAN Thread Initialized")

        if not rospy.has_param("~can_interface"):
            rospy.set_param("~can_interface", "can0")
        if not rospy.has_param("~uavcan_node_id"):
            rospy.set_param("~uavcan_node_id", 20)
        can_interface = rospy.get_param("~can_interface")
        node_id = rospy.get_param("~uavcan_node_id")
        node = UAVCanNodeThread.Node(can_interface, node_id)
        try:
            node.uavcan_node.periodic(0.2, node.send_pending)
            node.uavcan_node.periodic(1, node.check_shutdown)
            node.uavcan_node.spin()
        except uavcan.UAVCANException as e:
            rospy.logerr(e)
        except Shutdown as e:
            pass
        rospy.loginfo("UAVCAN Thread Finished")


def graceful_shutdown(signal, frame):
    global _shutting_down
    if not _shutting_down:
        rospy.loginfo("Shutting Down")

        _shutting_down = True
        rospy.signal_shutdown("Shutdown Requested")
        for t in _threads:
            t.join(timeout=3)

        sys.exit(0)


if __name__ == "__main__":
    rospy.loginfo("WiBotic ROS Interface")

    _threads.append(UAVCanNodeThread())
    _threads.append(ROSNodeThread())

    for t in _threads:
        t.start()

    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)
    rospy.loginfo("Press Ctrl+C to stop")
    while True:
        time.sleep(1)  # allow SIGINT to be handled
