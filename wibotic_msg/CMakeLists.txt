cmake_minimum_required(VERSION 2.8.3)
project(wibotic_msg)

find_package(catkin REQUIRED COMPONENTS
  std_msgs
  message_generation
)

################################################
## Declare ROS messages, services and actions ##
################################################

## Generate messages in the 'msg' folder
add_message_files(
  FILES
  WiBoticInfo.msg
)

## Generate services in the 'srv' folder
add_service_files(
  FILES
  ReadParameter.srv
  WriteParameter.srv
  ListParameters.srv
  SaveParameters.srv
)

## Generate added messages and services with any dependencies listed here
generate_messages(
  DEPENDENCIES
  std_msgs
)

###################################
## catkin specific configuration ##
###################################
catkin_package(
 CATKIN_DEPENDS std_msgs message_runtime
)

###########
## Build ##
###########

include_directories(
  ${catkin_INCLUDE_DIRS}
)

