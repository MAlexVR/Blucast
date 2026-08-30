cmake_minimum_required(VERSION 3.10)
project(BluCast LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(OpenCV REQUIRED)

# Maxine Video Effects SDK v0.7.2.0 headers, vendored in third_party/ (MIT,
# from github.com/NVIDIA-Maxine/Maxine-VFX-SDK). The compiled .so's + models
# are proprietary and NOT vendored here - the Containerfile copies them from
# the already-published runtime image instead of requiring an SDK download.
set(VFX_LIB "/usr/local/lib/blucast")

include_directories(
    ${CMAKE_CURRENT_SOURCE_DIR}/third_party
    ${OpenCV_INCLUDE_DIRS}
)

link_directories(${VFX_LIB})

add_executable(blucast_server server.cpp)

target_link_libraries(blucast_server
    ${OpenCV_LIBS}
    ${VFX_LIB}/libVideoFX.so
    ${VFX_LIB}/libNVCVImage.so
    pthread
    dl
)

set_target_properties(blucast_server PROPERTIES
    BUILD_RPATH   "${VFX_LIB}"
    INSTALL_RPATH "${VFX_LIB}"
)
