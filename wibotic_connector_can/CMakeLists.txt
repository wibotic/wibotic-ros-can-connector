cmake_minimum_required(VERSION 2.8.3)
project(wibotic_connector_can)

find_package(catkin REQUIRED COMPONENTS
  rospy
  std_msgs
  message_generation
)

catkin_python_setup()

###################################
## catkin specific configuration ##
###################################

catkin_package(
 LIBRARIES wibotic_connector_can
 CATKIN_DEPENDS rospy std_msgs message_runtime wibotic_msg
)

###########
## Build ##
###########

include_directories(
  ${catkin_INCLUDE_DIRS}
)

#############
## Install ##
#############

catkin_install_python(
  PROGRAMS src/wibotic_connector_can/wiboticros.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)

install(
  DIRECTORY
  launch/
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}/launch
)

install(
  DIRECTORY
  src/wibotic_connector_can/uavcan_v0
  src/wibotic_connector_can/uavcan_v1
  src/wibotic_connector_can/uavcan_vendor_specific_types
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)