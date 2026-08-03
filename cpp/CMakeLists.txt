cmake_minimum_required(VERSION 3.23)
project(cppbridge LANGUAGES CXX)


find_package(Qt6 REQUIRED COMPONENTS Core Qml)
qt_standard_project_setup(REQUIRES 6.5)

qt_add_qml_module(cppbridge
    URI cppbridge
    VERSION 1.0
    OUTPUT_DIRECTORY "${CMAKE_SOURCE_DIR}/../package/contents/ui/cppbridge"
    SOURCES
        src/timebridge.h
        src/timebridge.cpp
)

target_include_directories(cppbridge PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src")
target_link_libraries(cppbridge PRIVATE Qt6::Core Qt6::Qml)
set_target_properties(cppbridge PROPERTIES
    CXX_STANDARD 20
    CXX_STANDARD_REQUIRED ON
)
