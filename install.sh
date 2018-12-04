export ZSDK_VERSION=0.9.5
export GCC_ARM_NAME=gcc-arm-none-eabi-7-2018-q2-update

apt-get -y update && \
	apt-get -y upgrade && \
	apt-get install --no-install-recommends -y \
	autoconf \
	automake \
	build-essential \
	ccache \
	cmake \
	device-tree-compiler \
	dfu-util \
	doxygen \
	file \
	g++ \
	gcc \
	gcc-multilib \
	gcovr \
	git \
	git-core \
	gperf \
	iproute2 \
	lcov \
	libglib2.0-dev \
	libpcap-dev \
	libtool \
	locales \
	make \
	net-tools \
	ninja-build \
	ninja-build \
	pkg-config \
	python3-pip \
	python3-ply \
	python3-setuptools \
	qemu \
	socat \
	sudo \
	texinfo \
	valgrind \
	wget \
	xz-utils

pip3 install wheel west sh
pip3 install -r requirements.txt

wget -q "https://github.com/zephyrproject-rtos/meta-zephyr-sdk/releases/download/${ZSDK_VERSION}/zephyr-sdk-${ZSDK_VERSION}-setup.run" && \
  sh "zephyr-sdk-${ZSDK_VERSION}-setup.run" --quiet -- -d /opt/toolchains/zephyr-sdk-${ZSDK_VERSION} && \
  rm "zephyr-sdk-${ZSDK_VERSION}-setup.run"

wget -q https://developer.arm.com/-/media/Files/downloads/gnu-rm/7-2018q2/${GCC_ARM_NAME}-linux.tar.bz2  && \
    tar xf ${GCC_ARM_NAME}-linux.tar.bz2 && \
    rm -f ${GCC_ARM_NAME}-linux.tar.bz2 && \
    mv ${GCC_ARM_NAME} /opt/toolchains/${GCC_ARM_NAME}

