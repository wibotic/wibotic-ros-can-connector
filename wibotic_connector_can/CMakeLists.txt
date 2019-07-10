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
