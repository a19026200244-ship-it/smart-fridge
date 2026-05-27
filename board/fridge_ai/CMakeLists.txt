cmake_minimum_required(VERSION 3.10)
project(fridge_ai LANGUAGES C CXX)

if(DEFINED ENV{LUCKFOX_SDK_PATH})
    set(SDK $ENV{LUCKFOX_SDK_PATH})
    set(TC "${SDK}/tools/linux/toolchain/arm-rockchip830-linux-uclibcgnueabihf")
    set(CMAKE_CXX_COMPILER "${TC}/bin/arm-rockchip830-linux-uclibcgnueabihf-g++")
    set(CMAKE_C_COMPILER   "${TC}/bin/arm-rockchip830-linux-uclibcgnueabihf-gcc")
else()
    message(FATAL_ERROR "Set LUCKFOX_SDK_PATH")
endif()

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Os")

# 路径
set(RKMPI  "$ENV{HOME}/luckfox_pico_rkmpi_example")
set(YOLOD  "$ENV{HOME}/luckfox_pico_rknn_example/example/luckfox_pico_yolov5")

add_definitions(-DRV1106_1103)
add_compile_options(-DISP_HW_V30 -DRKPLATFORM=ON -DARCH64=OFF -DROCKIVA -DUAPI2
    -D_LARGEFILE_SOURCE -D_LARGEFILE64_SOURCE -D_FILE_OFFSET_BITS=64)

# 包含目录 (不包含sample_comm需要的rockiva路径, 只包含rkaiq API)
include_directories(
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${RKMPI}/include                    # rk_mpi_*.h
    ${RKMPI}/include/rkaiq              # rk_aiq_user_api2_*.h (直接在rkaiq下)
    ${RKMPI}/include/rkaiq/uAPI2
    ${RKMPI}/include/rkaiq/common
    ${RKMPI}/include/rkaiq/xcore
    ${RKMPI}/include/rkaiq/algos
    ${RKMPI}/include/rkaiq/iq_parser
    ${RKMPI}/common                     # ISP common
    ${RKMPI}/common/isp3.x
    ${RKMPI}/utils
    ${YOLOD}/include
    ${YOLOD}/src
    ${RKMPI}/3rdparty/librga/include
    ${RKMPI}/3rdparty/allocator/dma
)

link_directories(${RKMPI}/lib/uclibc)

# OpenCV
set(OpenCV_DIR "${RKMPI}/lib/uclibc/lib/cmake/opencv4")
find_package(OpenCV REQUIRED)
include_directories(${OpenCV_INCLUDE_DIRS})
find_package(Threads REQUIRED)

set(SOURCES main.c sqlite3.c ${YOLOD}/src/yolov5.cc ${YOLOD}/src/postprocess.cc)
set_source_files_properties(main.c PROPERTIES LANGUAGE CXX)

add_executable(fridge_ai ${SOURCES})
target_link_libraries(fridge_ai ${OpenCV_LIBS} rknnmrt sample_comm rockit rockchip_mpp rkaiq rga Threads::Threads dl m)

set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} -Wl,-rpath-link,${RKMPI}/lib/uclibc")
install(TARGETS fridge_ai DESTINATION bin)
