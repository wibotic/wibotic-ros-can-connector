#!/usr/bin/env python

# use `pip install future uavcan` before running on python 2

# Other Imports
import os
import sys
import time
import queue
import signal
import threading

# ROS Imports
import rospy
import uavcan

from wibotic_msg import msg, srv

# Global state and constants shared between threads
DSDL_PATH = os.path.dirname(__file__) + "/uavcan_vendor_specific_types/wibotic"
WIBOTIC_NODE_NAME = "com.wibotic.charger"
PARAMETER_REQ_TIMEOUT = 3
SUCCESS = 5
FAILURE = 0
_uav_incoming_info = queue.Queue()
_uav_incoming_param = queue.Queue(1)
_uav_outgoing = queue.Queue()
_threads = []
_shutting_down = False


def ClearQueue(q):
    while not q.empty():
        q.get()


def ascii_list_to_str(l):
    return "".join(chr(i) for i in l)


class Shutdown(Exception):
    pass


class ROSNodeThread(threading.Thread):
    class Node:
        def sender(self):
            pub = rospy.Publisher(
                "~wibotic_info",
                msg.WiBoticInfo,
                queue_size=10,
            )
            while not rospy.is_shutdown():
                incoming_data = _uav_incoming_info.get()
                uavcan_dsdl_type = uavcan.get_uavcan_data_type(incoming_data)
                unpacked_data = {}
                for field in uavcan_dsdl_type.fields:
                    unpacked_data[field.name] = getattr(incoming_data, field.name)
                packaged_data = msg.WiBoticInfo(**unpacked_data)
                rospy.loginfo(packaged_data)
                pub.publish(packaged_data)

        @staticmethod
        def get_incoming_param():
            try:
                response = _uav_incoming_param.get(
                    block=True,
                    timeout=PARAMETER_REQ_TIMEOUT,
                )
            except queue.Empty:
                return FAILURE

            if uavcan.get_active_union_field(response.value) == "empty":
                return "empty"
            return response

        def handle_param_list(self, req):
            params = []
            index = 0
            ClearQueue(_uav_incoming_param)
            while True:
                request = uavcan.protocol.param.GetSet.Request(index=index)
                _uav_outgoing.put(request)
                response = self.get_incoming_param()
                if response not in [FAILURE, "empty"]:
                    params.append(ascii_list_to_str(response.name))
                    index += 1
                    rospy.loginfo(index)
                elif response == "empty":
                    return [SUCCESS, params]
                else:
                    return [FAILURE, params]

        def handle_param_read(self, req):
            request = uavcan.protocol.param.GetSet.Request(name=req.name)
            _uav_outgoing.put(request)
            response = self.get_incoming_param()
            if response not in [FAILURE, "empty"]:
                status = SUCCESS if response.name == req.name else FAILURE
                value = response.value.integer_value
                return [status, value]
            return [FAILURE, 0]

        def handle_param_write(self, req):
            request = uavcan.protocol.param.GetSet.Request(
                name=req.name,
                value=uavcan.protocol.param.Value(integer_value=req.value),
            )
            _uav_outgoing.put(request)
            response = self.get_incoming_param()
            if response not in [FAILURE, "empty"]:
                if response.value.integer_value == req.value:
                    return SUCCESS
            return FAILURE

        def handle_param_save(self, req):
            request = uavcan.protocol.param.ExecuteOpcode.Request(
                opcode=uavcan.protocol.param.ExecuteOpcode.Request().OPCODE_SAVE
            )
            _uav_outgoing.put(request)
            response = self.get_incoming_param()
            if response not in [FAILURE, "empty"]:
                if response.ok:
                    return SUCCESS
            return FAILURE

    def __init__(self):
        rospy.init_node("wibotic_connector_can")
        super(ROSNodeThread, self).__init__()
        self.daemon = True

    def run(self):
        rospy.loginfo("ROS Thread Initialized")
        node = ROSNodeThread.Node()
        try:
            rospy.Service(
                "~read_parameter",
                srv.ReadParameter,
                node.handle_param_read,
            )
            rospy.Service(
                "~write_parameter",
                srv.WriteParameter,
                node.handle_param_write,
            )
            rospy.Service(
                "~list_parameters",
                srv.ListParameters,
                node.handle_param_list,
            )
            rospy.Service(
                "~save_parameters",
                srv.SaveParameters,
                node.handle_param_save,
            )
            node.sender()
        except rospy.ROSInterruptException:
            pass
        rospy.loginfo("ROS Thread Finished")


class UAVCanNodeThread(threading.Thread):
    class Node:
        outstanding_param_request = threading.Semaphore()

        def __init__(self, can_interface, node_id):
            uavcan.load_dsdl(DSDL_PATH)
            node_info = uavcan.protocol.GetNodeInfo.Response()
            node_info.name = "com.wibotic.ros_connector"
            node_info.software_version.major = 1
            try:
                self.uavcan_node = uavcan.make_node(
                    can_interface,
                    node_id=node_id,
                    node_info=node_info,
                    mode=uavcan.protocol.NodeStatus().MODE_OPERATIONAL,
                )
            except OSError:
                rospy.logerr(
                    "ERROR: Device not found. "
                    "Please confirm the device name is correctly set!"
                )
            else:
                self.monitor = uavcan.app.node_monitor.NodeMonitor(self.uavcan_node)
                self.uavcan_node.add_handler(
                    uavcan.thirdparty.wibotic.WiBoticInfo,
                    self.wibotic_info_callback,
                )

        def get_wibotic_node_id(self):
            online_nodes = self.monitor.get_all_node_id()
            for node_id in online_nodes:
                node_name = ascii_list_to_str(self.monitor.get(node_id).info.name)
                if node_name == WIBOTIC_NODE_NAME:
                    return node_id
            return None

        def wibotic_info_callback(self, event):
            _uav_incoming_info.put(event.transfer.payload)

        def wibotic_param_callback(self, event):
            _uav_incoming_param.put(event.transfer.payload)
            self.outstanding_param_request.release()

        def send_pending(self):
            while True:
                send_item = _uav_outgoing.get()
                target_node_id = self.get_wibotic_node_id()
                if target_node_id is not None:
                    self.outstanding_param_request.acquire()
                    self.uavcan_node.request(
                        send_item,
                        target_node_id,
                        self.wibotic_param_callback,
                    )
                else:
                    rospy.logwarn("No WiBotic device found on bus")

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
            # Thread implicitly daemonic since parent thread is daemonic
            threading.Thread(target=node.send_pending).start()
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
