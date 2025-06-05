# SPDX-License-Identifier: Apache-2.0

zephyr_get(GNUARMEMB_TOOLCHAIN_PATH)
assert(    GNUARMEMB_TOOLCHAIN_PATH "GNUARMEMB_TOOLCHAIN_PATH is not set")

if(NOT EXISTS ${GNUARMEMB_TOOLCHAIN_PATH})
  message(FATAL_ERROR "Nothing found at GNUARMEMB_TOOLCHAIN_PATH: '${GNUARMEMB_TOOLCHAIN_PATH}'")
endif()

set(TOOLCHAIN_HOME ${GNUARMEMB_TOOLCHAIN_PATH})

set(COMPILER gcc)
set(LINKER ld)
set(BINTOOLS gnu)

set(CROSS_COMPILE_TARGET arm-none-eabi)
set(SYSROOT_TARGET       arm-none-eabi)

set(CROSS_COMPILE ${TOOLCHAIN_HOME}/bin/${CROSS_COMPILE_TARGET}-)
set(SYSROOT_DIR   ${TOOLCHAIN_HOME}/${SYSROOT_TARGET})

set(TOOLCHAIN_HAS_NEWLIB ON CACHE BOOL "True if toolchain supports newlib")
set(TOOLCHAIN_HAS_GLIBCXX ON CACHE BOOL "True if toolchain supports libstdc++")

message(STATUS "Found toolchain: gnuarmemb (${GNUARMEMB_TOOLCHAIN_PATH})")
