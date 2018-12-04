export ZSDK_VERSION=0.9.5
export GCC_ARM_NAME=gcc-arm-none-eabi-7-2018-q2-update

apt-get install --no-install-recommends -y \
	autoconf \
	automake \
	build-essential \
	ccache \
	cmake \
	device-tree-compiler \
	doxygen \
	file \
	g++ \
	gcc \
	gcc-multilib \
	gcovr \
	git \
	git-core \
	gperf \
	lcov \
	libtool \
	locales \
	make \
	ninja-build \
	pkg-config \
	texinfo \
	valgrind \
	wget \
	xz-utils \
	python3-pip \
	python3-ply \
	python3-setuptools


wget -q "https://github.com/zephyrproject-rtos/meta-zephyr-sdk/releases/download/${ZSDK_VERSION}/zephyr-sdk-${ZSDK_VERSION}-setup.run" && \
  sh "zephyr-sdk-${ZSDK_VERSION}-setup.run" --quiet -- -d /opt/toolchains/zephyr-sdk-${ZSDK_VERSION} && \
  rm "zephyr-sdk-${ZSDK_VERSION}-setup.run"

wget -q https://developer.arm.com/-/media/Files/downloads/gnu-rm/7-2018q2/${GCC_ARM_NAME}-linux.tar.bz2  && \
    tar xf ${GCC_ARM_NAME}-linux.tar.bz2 && \
    rm -f ${GCC_ARM_NAME}-linux.tar.bz2 && \
    mv ${GCC_ARM_NAME} /opt/toolchains/${GCC_ARM_NAME}

