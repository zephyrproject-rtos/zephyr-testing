#!/bin/sh


export ZSDK_VERSION=0.10.0
export GCC_ARM_NAME=gcc-arm-none-eabi-7-2018-q2-update

sudo apt-get install --no-install-recommends -y \
	autoconf \
	automake \
	build-essential \
	ccache \
	cmake \
	doxygen \
	gcc-multilib \
	gperf \
	make \
	ninja-build \
	wget


if [ ! -d "/home/semaphore/zephyr-sdk-0.9.5" ]; then
wget -q "https://github.com/zephyrproject-rtos/meta-zephyr-sdk/releases/download/${ZSDK_VERSION}/zephyr-sdk-${ZSDK_VERSION}-setup.run" && \
  sh "zephyr-sdk-${ZSDK_VERSION}-setup.run" --quiet -- -d $HOME/zephyr-sdk-${ZSDK_VERSION} && \
  rm "zephyr-sdk-${ZSDK_VERSION}-setup.run"

fi
