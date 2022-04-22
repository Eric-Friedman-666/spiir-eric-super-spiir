# syntax = docker/dockerfile:1.2
#### https://gitlab.com/nvidia/container-images/cuda/-/blob/master/dist/10.0/ubuntu1804/base/Dockerfile
FROM ubuntu:18.04

ENV NVARCH x86_64
ENV NVIDIA_REQUIRE_CUDA "cuda>=10.0 brand=tesla,driver>=384,driver<385 brand=tesla,driver>=410,driver<411"
ENV NV_CUDA_CUDART_VERSION 10.0.130-1

ENV NV_ML_REPO_ENABLED 1
ENV NV_ML_REPO_URL https://developer.download.nvidia.com/compute/machine-learning/repos/ubuntu1804/${NVARCH}

RUN apt-get update && apt-get install -y --no-install-recommends \
	gnupg2 curl ca-certificates && \
	curl -fsSL https://developer.download.nvidia.com/compute/cuda/repos/ubuntu1804/${NVARCH}/7fa2af80.pub | apt-key add - && \
	echo "deb https://developer.download.nvidia.com/compute/cuda/repos/ubuntu1804/${NVARCH} /" > /etc/apt/sources.list.d/cuda.list && \
	if [ ! -z ${NV_ML_REPO_ENABLED} ]; then echo "deb ${NV_ML_REPO_URL} /" > /etc/apt/sources.list.d/nvidia-ml.list; fi && \
	apt-get purge --autoremove -y curl \
	&& rm -rf /var/lib/apt/lists/*

ENV CUDA_VERSION 10.0.130

# For libraries in the cuda-compat-* package: https://docs.nvidia.com/cuda/eula/index.html#attachment-a
RUN apt-get update && apt-get install -y --no-install-recommends \
	cuda-cudart-10-0=${NV_CUDA_CUDART_VERSION} \
	cuda-compat-10-0 \
	&& ln -s cuda-10.0 /usr/local/cuda && \
	rm -rf /var/lib/apt/lists/*

# Required for nvidia-docker v1
RUN echo "/usr/local/nvidia/lib" >> /etc/ld.so.conf.d/nvidia.conf && \
	echo "/usr/local/nvidia/lib64" >> /etc/ld.so.conf.d/nvidia.conf

ENV PATH /usr/local/nvidia/bin:/usr/local/cuda/bin:${PATH}
ENV LD_LIBRARY_PATH /usr/local/nvidia/lib:/usr/local/nvidia/lib64

# nvidia-container-runtime
ENV NVIDIA_VISIBLE_DEVICES all
ENV NVIDIA_DRIVER_CAPABILITIES compute,utility

#### https://gitlab.com/nvidia/container-images/cuda/-/blob/master/dist/10.0/ubuntu1804/runtime/Dockerfile
ENV NV_CUDA_LIB_VERSION 10.0.130-1
ENV NV_NVTX_VERSION 10.0.130-1
ENV NV_LIBNPP_VERSION 10.0.130-1
ENV NV_LIBCUSPARSE_VERSION 10.0.130-1


ENV NV_LIBCUBLAS_PACKAGE_NAME cuda-cublas-10-0

ENV NV_LIBCUBLAS_VERSION 10.0.130-1
ENV NV_LIBCUBLAS_PACKAGE ${NV_LIBCUBLAS_PACKAGE_NAME}=${NV_LIBCUBLAS_VERSION}


ENV NV_LIBNCCL_PACKAGE_NAME "libnccl2"
ENV NV_LIBNCCL_PACKAGE_VERSION 2.6.4-1
ENV NCCL_VERSION 2.6.4
ENV NV_LIBNCCL_PACKAGE ${NV_LIBNCCL_PACKAGE_NAME}=${NV_LIBNCCL_PACKAGE_VERSION}+cuda10.0

RUN apt-get update && apt-get install -y --no-install-recommends \
	cuda-libraries-10-0=${NV_CUDA_LIB_VERSION} \
	cuda-npp-10-0=${NV_LIBNPP_VERSION} \
	cuda-nvtx-10-0=${NV_NVTX_VERSION} \
	cuda-cusparse-10-0=${NV_LIBCUSPARSE_VERSION} \
	${NV_LIBCUBLAS_PACKAGE} \
	${NV_LIBNCCL_PACKAGE} \
	&& rm -rf /var/lib/apt/lists/*

# Keep apt from auto upgrading the cublas and nccl packages. See https://gitlab.com/nvidia/container-images/cuda/-/issues/88
RUN apt-mark hold ${NV_LIBNCCL_PACKAGE_NAME} ${NV_LIBCUBLAS_PACKAGE_NAME}

#### https://gitlab.com/nvidia/container-images/cuda/-/blob/master/dist/10.0/ubuntu1804/devel/Dockerfile
ENV NV_CUDA_LIB_VERSION 10.0.130-1
ENV NV_CUDA_CUDART_DEV_VERSION 10.0.130-1
ENV NV_NVML_DEV_VERSION 10.0.130-1
ENV NV_LIBCUSPARSE_DEV_VERSION 10.0.130-1
ENV NV_LIBNPP_DEV_VERSION 10.0.130-1
ENV NV_LIBCUBLAS_DEV_PACKAGE_NAME cuda-cublas-dev-10-0

ENV NV_LIBCUBLAS_DEV_VERSION 10.0.130-1
ENV NV_LIBCUBLAS_DEV_PACKAGE ${NV_LIBCUBLAS_DEV_PACKAGE_NAME}=${NV_LIBCUBLAS_DEV_VERSION}

ENV NV_LIBNCCL_DEV_PACKAGE_NAME libnccl-dev
ENV NV_LIBNCCL_DEV_VERSION 2.6.4-1
ENV NCCL_VERSION ${NV_LIBNCCL_DEV_VERSION}
ENV NV_LIBNCCL_DEV_PACKAGE ${NV_LIBNCCL_DEV_PACKAGE_NAME}=${NV_LIBNCCL_DEV_VERSION}+cuda10.0

RUN apt-get update && apt-get install -y --no-install-recommends \
	cuda-nvml-dev-10-0=${NV_NVML_DEV_VERSION} \
	cuda-command-line-tools-10-0=${NV_CUDA_LIB_VERSION} \
	cuda-nvprof-10-0=${NV_CUDA_LIB_VERSION} \
	cuda-npp-dev-10-0=${NV_LIBNPP_DEV_VERSION} \
	cuda-libraries-dev-10-0=${NV_CUDA_LIB_VERSION} \
	cuda-minimal-build-10-0=${NV_CUDA_LIB_VERSION} \
	${NV_LIBCUBLAS_DEV_PACKAGE} \
	${NV_LIBNCCL_DEV_PACKAGE} \
	&& rm -rf /var/lib/apt/lists/*

# apt from auto upgrading the cublas package. See https://gitlab.com/nvidia/container-images/cuda/-/issues/88
RUN apt-mark hold ${NV_LIBCUBLAS_DEV_PACKAGE_NAME} ${NV_LIBNCCL_DEV_PACKAGE_NAME}

ENV LIBRARY_PATH /usr/local/cuda/lib64/stubs

#### https://gitlab.com/nvidia/container-images/cuda/-/blob/master/dist/10.0/ubuntu1804/devel/cudnn7/Dockerfile
ENV NV_CUDNN_PACKAGE_VERSION 7.6.5.32-1
ENV NV_CUDNN_VERSION 7.6.5.32

ENV NV_CUDNN_PACKAGE_NAME libcudnn7
ENV NV_CUDNN_PACKAGE ${NV_CUDNN_PACKAGE_NAME}=${NV_CUDNN_PACKAGE_VERSION}+cuda10.0
ENV NV_CUDNN_PACKAGE_DEV ${NV_CUDNN_PACKAGE_NAME}-dev=${NV_CUDNN_PACKAGE_VERSION}+cuda10.0

ENV CUDNN_VERSION ${NV_CUDNN_VERSION}

RUN apt-get update && apt-get install -y --no-install-recommends \
	${NV_CUDNN_PACKAGE} \
	${NV_CUDNN_PACKAGE_DEV} \
	&& apt-mark hold ${NV_CUDNN_PACKAGE_NAME} && \
	rm -rf /var/lib/apt/lists/*

#############################################################################################

RUN rm -f /etc/apt/apt.conf.d/docker-clean; echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache
RUN --mount=type=cache,target=/var/cache/apt --mount=type=cache,target=/var/lib/apt \
	apt-get update || DEBIAN_FRONTEND="noninteractive" apt-get install -y --no-install-recommends tzdata && \
	apt-get install -y \ 
	--no-install-recommends \
	software-properties-common vim git ccache wget ca-certificates zlib1g-dev build-essential cmake texinfo libopenmpi-dev \
	libscalapack-openmpi-dev liblapack-dev libblas-dev flex bison gtk-doc-tools libffi-dev doxygen libpcre3-dev libssl-dev \
	gfortran xorg-dev liblapack-dev sqlite3 libfreetype6-dev perlbrew patch libcurl4-openssl-dev \
	&& \
	apt-get -y remove libglib2.0 libtool automake autoconf && \
	apt-get -y autoremove && rm -rf /var/lib/apt/lists/*
RUN /usr/sbin/update-ccache-symlinks
RUN mkdir /root/ccache && ccache --set-config=cache_dir=/root/ccache

ENV SYSTEM_PYTHONPATH=${PYTHONPATH:-}
ENV SYSTEM_PATH=${PATH:-}
ENV SYSTEM_PKG_CONFIG_PATH=${PKG_CONFIG_PATH:-}
ENV SYSTEM_LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-}
ENV DEPPREFIX=/usr/spiir
ENV PREFIX=$DEPPREFIX
ENV PREFIX_DEPENDENCIES=$DEPPREFIX
ENV ACLOCAL_PATH=/usr/spiir/share/aclocal
ENV CC=mpicc
ENV CXX=mpiCC
ENV CFLAGS=-fPIC 
ENV CXXFLAGS=-fPIC
ENV CPPFLAGS=-fPIC
ENV FFLAGS=-fPIC
ENV FCFLAGS=-fPIC
ENV PATH=/Healpix_3.50/src/cxx/optimized_gcc/bin:/root/perl5/perlbrew/perls/perl-5.16.3/bin:$PREFIX/bin:/usr/local/cuda-10.1/bin:$PATH
ENV PKG_CONFIG_PATH=/Healpix_3.50/lib:$PREFIX/lib/pkgconfig/:$PREFIX/lib/x86_64-linux-gnu/pkgconfig:${PKG_CONFIG_PATH:-}
ENV LD_LIBRARY_PATH=/Healpix_3.50/lib:/Healpix_3.50/src/cxx/optimized_gcc/lib:$PREFIX/lib:$PREFIX/lib/x86_64-linux-gnu:/usr/local/cuda-10.1/lib64:${LD_LIBRARY_PATH:-}
ENV GST_PLUGIN_PATH=/usr/spiir/lib/gstreamer-0.10
#ENV CPATH=/HEALPIX_3.50/include:/Healpix_3.50/src/cxx/optimized_gcc/include

RUN perlbrew init && \
	perlbrew install -j 24 --thread --multi --64int --64all --ld 5.16.3 --notest

RUN --mount=type=cache,target=/root/ccache \
	p=autoconf-2.69 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts http://ftp.gnu.org/gnu/autoconf/$p.tar.gz && \
	tar -xzf $p.tar.gz && \
	cd $p && \
	./configure --prefix=$PREFIX && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p && rm $p.tar.gz

RUN --mount=type=cache,target=/root/ccache \
	p=automake-1.13.4 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts https://ftp.gnu.org/gnu/automake/$p.tar.gz && \
	tar -xzf $p.tar.gz && \
	cd $p && \
	./configure --prefix=$PREFIX && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p && rm $p.tar.gz

RUN --mount=type=cache,target=/root/ccache \
	p=libtool-2.4.2 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts https://ftpmirror.gnu.org/libtool/libtool-2.4.2.tar.gz && \
	tar -xzf $p.tar.gz && \
	cd $p && \
	./configure --prefix=$PREFIX && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p && rm $p.tar.gz

RUN wget -O ~/miniconda.sh https://repo.continuum.io/miniconda/Miniconda3-latest-Linux-x86_64.sh && \
	chmod +x ~/miniconda.sh && \
	~/miniconda.sh -b -p /root/.conda && \
	rm ~/miniconda.sh

ENV CONDA /root/.conda/bin/conda
ENV PYTHON2PREFIX ${PREFIX}
ENV PYTHON2 ${PYTHON2PREFIX}/bin/python
ENV PIP2 ${PYTHON2PREFIX}/bin/pip
RUN --mount=type=cache,target=/root/.cache \
	--mount=type=cache,target=/root/.conda/pkgs \
	${CONDA} create -p ${PYTHON2PREFIX} -y python=2.7.14 zlib==1.2.11 && \
	${PIP2} config set global.cache-dir false && \
	${PIP2} install --upgrade pip setuptools wheel==0.37.0 && \
	${PIP2} install numpy==1.14.1 scipy==1.0.0 matplotlib==2.2.2   h5py==2.7.1 healpy==1.12.4 astropy==2.0.3 \
	importlib-metadata==2.1.2 pathlib2==2.3.6 pytz==2021.3 \
	cryptography pyopenssl Cython==0.29.26 ligo-segments shapely yapf clang-format==13.0.0 && \
	${CONDA} clean -a

ENV PYTHON3PREFIX ${PREFIX}/python3
ENV PYTHON3 ${PYTHON3PREFIX}/bin/python
ENV PIP3 ${PYTHON3PREFIX}/bin/pip
RUN --mount=type=cache,target=/root/.cache \
	--mount=type=cache,target=/root/.conda/pkgs \
	${CONDA} create -p ${PYTHON3PREFIX} -y python=3.7.4 && \
	${PIP3} config set global.cache-dir false && \
	${PIP3} install --upgrade pip==21.3.1 setuptools==60.3.1 wheel==0.37.0 && \
	${PIP3} install meson==0.60.3 ninja==1.10.2.3 --prefix=$PREFIX/python3_stuff && \
	${CONDA} clean -a

ENV PATH=$PATH:$PREFIX/python3_stuff/bin:$PREFIX/python3/bin
RUN printenv

RUN p=pkg-config-0.27.1 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts https://pkgconfig.freedesktop.org/releases/$p.tar.gz && \
	tar -xzf $p.tar.gz && \
	cd $p && \
	./configure --with-internal-glib --prefix=$PREFIX && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p && rm $p.tar.gz

RUN --mount=type=cache,target=/root/ccache \
	p=libxml2-2.9.12 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts ftp://xmlsoft.org/libxml2/$p.tar.gz && \
	tar -xzf $p.tar.gz && \
	cd $p && \
	./configure --prefix=$PREFIX && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p && rm $p.tar.gz

RUN --mount=type=cache,target=/root/ccache \
	p=fftw-3.3.5 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts ftp://ftp.fftw.org/pub/fftw/$p.tar.gz && \
	tar -xzf $p.tar.gz && \
	cd $p && \
	./configure --prefix=$PREFIX --enable-sse2 --enable-avx && \
	make -j && \
	make install && \
	./configure --prefix=$PREFIX --enable-float --enable-sse --enable-avx && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p && rm $p.tar.gz

RUN --mount=type=cache,target=/root/ccache \
	p=hdf5-1.8.13 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts https://support.hdfgroup.org/ftp/HDF5/releases/hdf5-1.8/$p/src/$p.tar.gz && \
	tar -xzf $p.tar.gz && \
	cd $p && \
	./configure --prefix=$PREFIX && \
	make -j && \
	make install && \
	mkdir -p "$PREFIX/lib/pkgconfig" && \
	echo 'prefix= \
	exec_prefix=${prefix} \
	includedir=${prefix}/include \
	libdir=${exec_prefix}/lib \
	Name: hdf5 \
	Description: HDF5 \
	Version: 1.8.12 \
	Requires.private: zlib \
	Cflags: -I${includedir} \
	Libs: -L${libdir} -lhdf5' | \
	sed "s%^prefix=.*%prefix=$PREFIX%" > "$PREFIX/lib/pkgconfig/hdf5.pc" && \
	cd .. && \
	rm -r $p && rm $p.tar.gz

RUN --mount=type=cache,target=/root/ccache \
	p=libframe-8.30 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts http://software.ligo.org/lscsoft/source/$p.tar.gz && \
	tar -xzf $p.tar.gz && \
	cd $p && \
	# make sure frame files are opened in binary mode
	sed -i~ 's/\([Oo]pen.*"r\)"/\1b"/;' src/FrameL.c && \
	./configure --prefix=$PREFIX && \
	make -j && \
	make install && \
	mkdir -p "$PREFIX/lib/pkgconfig" && \
	sed "s%^prefix=.*%prefix=$PREFIX%" src/libframe.pc > $PREFIX/lib/pkgconfig/libframe.pc && \
	cd .. && \
	rm -r $p && rm $p.tar.gz

RUN --mount=type=cache,target=/root/ccache \
	p=metaio-8.3.0 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts http://software.ligo.org/lscsoft/source/$p.tar.gz && \
	tar -xzf $p.tar.gz && \
	cd $p && \
	./configure --prefix=$PREFIX && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p && rm $p.tar.gz

RUN --mount=type=cache,target=/root/ccache \
	p=swig-3.0.12 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts https://sourceforge.net/projects/swig/files/swig/$p/$p.tar.gz && \
	tar -xzf $p.tar.gz && \
	cd $p && \
	./configure --prefix=$PYTHON2PREFIX --without-tcl --with-python --without-python3 --without-perl5 --without-octave \
	--without-scilab --without-java --without-javascript --without-gcj --without-android --without-guile \
	--without-mzscheme --without-ruby --without-php --without-ocaml --without-pike --without-chicken \
	--without-csharp --without-lua --without-allegrocl --without-clisp --without-r --without-go --without-d && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p && rm $p.tar.gz

RUN --mount=type=cache,target=/root/ccache \
	p=swig-4.0.2 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts https://sourceforge.net/projects/swig/files/swig/$p/$p.tar.gz && \
	tar -xzf $p.tar.gz && \
	cd $p && \
	./configure --prefix=$PYTHON3PREFIX --without-tcl --without-python --with-python3 --without-perl5 --without-octave \
	--without-scilab --without-java --without-javascript --without-gcj --without-android --without-guile \
	--without-mzscheme --without-ruby --without-php --without-ocaml --without-pike --without-chicken \
	--without-csharp --without-lua --without-allegrocl --without-clisp --without-r --without-go --without-d && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p && rm $p.tar.gz

RUN --mount=type=cache,target=/root/ccache \
	p=gsl-2.6 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts ftp://ftp.fu-berlin.de/unix/gnu/gsl/$p.tar.gz && \
	tar -xzf $p.tar.gz && \
	cd $p && \
	./configure --prefix=$PREFIX && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p && rm $p.tar.gz

RUN --mount=type=cache,target=/root/ccache \
	p=gettext-0.20.1 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget -nc https://ftp.gnu.org/pub/gnu/gettext/$p.tar.gz && \
	tar -xzf $p.tar.gz && \
	cd $p && \
	./configure --prefix=$PREFIX && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p && rm $p.tar.gz

RUN --mount=type=cache,target=/root/ccache \
	p=ldas-tools-al-2.5.7 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts http://software.igwn.org/lscsoft/source/$p.tar.gz && \
	tar -xzf $p.tar.gz && \
	cd $p && \
	./configure --prefix=$PREFIX --disable-warnings-as-errors && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p && rm $p.tar.gz

COPY framecpp_0000_Makefile_fix.patch .
RUN --mount=type=cache,target=/root/ccache \
	p=ldas-tools-framecpp-2.5.8 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts http://software.igwn.org/lscsoft/source/$p.tar.gz && \
	tar -xzf $p.tar.gz && \
	cd $p && \
	./configure --prefix=$PREFIX --disable-warnings-as-errors && \
	cd swig/python && \
	patch framecpp_0000_Makefile_fix.patch && \
	cd ../.. && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p && rm $p.tar.gz

RUN --mount=type=cache,target=/root/ccache \
	p=util-linux-2.34 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts https://mirrors.edge.kernel.org/pub/linux/utils/util-linux/v2.34/$p.tar.xz && \
	tar -xJf $p.tar.xz && \
	cd $p && \
	./configure --prefix=$PREFIX --disable-use-tty-group --disable-all-programs --enable-libmount --enable-libblkid && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p && rm $p.tar.xz

# RUN --mount=type=cache,target=/root/ccache \
RUN p=glib-2.62.3 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts https://ftp.gnome.org/pub/gnome/sources/glib/2.62/$p.tar.xz && \
	tar -xJf $p.tar.xz && \
	cd $p && \
	PATH=$PREFIX/python3_stuff/bin:$PREFIX/python3/bin:$PATH PYTHONPATH=$PREFIX/python3_stuff/lib/python3.7/site-packages $PREFIX/python3_stuff/bin/meson _build --prefix=$PREFIX && \
	PATH=$PREFIX/python3_stuff/bin:$PREFIX/python3/bin:$PATH PYTHONPATH=$PREFIX/python3_stuff/lib/python3.7/site-packages $PREFIX/python3_stuff/bin/ninja -v -C _build && \
	PATH=$PREFIX/python3_stuff/bin:$PREFIX/python3/bin:$PATH PYTHONPATH=$PREFIX/python3_stuff/lib/python3.7/site-packages $PREFIX/python3_stuff/bin/ninja -C _build install && \
	cd .. && \
	rm -r $p && rm $p.tar.xz

# RUN --mount=type=cache,target=/root/ccache \
# ccache breaks the build for some reason which I thought wasn't possible with ccache, might have to remove it for the rest of the packages.
RUN p=gobject-introspection-1.63.1 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts https://ftp.gnome.org/pub/GNOME/sources/gobject-introspection/1.63/$p.tar.xz && \
	tar -xJf $p.tar.xz && \
	cd $p && \
	PATH=$PREFIX/python3_stuff/bin:$PREFIX/python3/bin:$PATH PYTHONPATH=$PREFIX/python3_stuff/lib/python3.7/site-packages $PREFIX/python3_stuff/bin/meson _build --prefix=$PREFIX && \
	PATH=$PREFIX/python3_stuff/bin:$PREFIX/python3/bin:$PATH PYTHONPATH=$PREFIX/python3_stuff/lib/python3.7/site-packages $PREFIX/python3_stuff/bin/ninja -v -C _build && \
	PATH=$PREFIX/python3_stuff/bin:$PREFIX/python3/bin:$PATH PYTHONPATH=$PREFIX/python3_stuff/lib/python3.7/site-packages $PREFIX/python3_stuff/bin/ninja -C _build install && \
	cd .. && \
	rm -r $p && rm $p.tar.xz

RUN --mount=type=cache,target=/root/ccache \
	p=pixman-0.38.4 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts https://www.cairographics.org/releases/$p.tar.gz && \
	tar -xzf $p.tar.gz && \
	cd $p && \
	./configure --prefix=$PREFIX && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p && rm $p.tar.gz

RUN --mount=type=cache,target=/root/ccache \
	p=libpng && \
	echo -e "\\n\\n>> [`date`] Cloning $p" && \
	git clone https://github.com/glennrp/$p.git && \
	cd $p && \
	# NOCONFIGURE=1 ./autogen.sh && \
	# git repo includes configure
	./configure --prefix=$PREFIX && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p

RUN --mount=type=cache,target=/root/ccache \
	p=cairo-1.16.0 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts https://www.cairographics.org/releases/$p.tar.xz && \
	tar -xJf $p.tar.xz && \
	cd $p && \
	./configure --prefix=$PREFIX && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p && rm $p.tar.xz

RUN --mount=type=cache,target=/root/ccache \
	p=pygobject-2.28.7 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts https://ftp.acc.umu.se/pub/GNOME/sources/pygobject/2.28/$p.tar.xz && \
	tar -xJf $p.tar.xz && \
	cd $p && \
	./configure --prefix=$PREFIX && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p && rm $p.tar.xz

RUN --mount=type=cache,target=/root/ccache \
	p=pygtk-2.24.0 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts https://ftp.gnome.org/pub/GNOME/sources/pygtk/2.24/$p.tar.bz2 && \
	tar -xjf $p.tar.bz2 && \
	cd $p && \
	./configure --prefix=$PREFIX && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p && rm $p.tar.bz2

COPY manoj_00_gstreamer.patch .
RUN --mount=type=cache,target=/root/ccache \
	p=gstreamer && \
	echo -e "\\n\\n>> [`date`] Cloning $p" && \
	git clone https://gitlab.freedesktop.org/gstreamer/$p.git && \
	cd $p && \
	git checkout 0.10 && \
	git apply ../manoj_00_gstreamer.patch && \
	NOCONFIGURE=1 ./autogen.sh && \
	./configure --prefix=$PREFIX && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p

RUN --mount=type=cache,target=/root/ccache \
	p=gst-plugins-base && \
	echo -e "\\n\\n>> [`date`] Cloning $p" && \
	git clone https://gitlab.freedesktop.org/gstreamer/$p.git && \
	cd $p && \
	git checkout 0.10 && \
	NOCONFIGURE=1 ./autogen.sh && \
	./configure --prefix=$PREFIX && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p

RUN --mount=type=cache,target=/root/ccache \
	p=gst-plugins-good && \
	echo -e "\\n\\n>> [`date`] Cloning $p" && \
	git clone https://gitlab.freedesktop.org/gstreamer/$p.git && \
	cd $p && \
	git checkout 0.10 && \
	NOCONFIGURE=1 ./autogen.sh && \
	./configure --prefix=$PREFIX --disable-gst_v4l2 && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p

RUN --mount=type=cache,target=/root/ccache \
	p=gst-python && \
	echo -e "\\n\\n>> [`date`] Cloning $p" && \
	git clone https://gitlab.freedesktop.org/gstreamer/$p.git && \
	cd $p && \
	git checkout 0.10 && \
	NOCONFIGURE=1 ./autogen.sh && \
	CFLAGS="-L$PREFIX/lib -Wno-error $CFLAGS" ./configure --prefix=$PREFIX && \
	# can't find python libs without specifically adding the link flag
	make -j && \
	make install && \
	cd .. && \
	rm -r $p

COPY lalsuite_0000_cleanup.patch .
COPY lalsuite_0001_variable_epsilon.patch .
RUN --mount=type=cache,target=/root/ccache \
	p=lalsuite && \
	echo -e "\\n\\n>> [`date`] Cloning $p" && \
	git clone https://git.ligo.org/lscsoft/$p.git && \
	cd $p && \
	git checkout 7893708fdb399c05ca56e1a072f7ba667dc35e83 && \
	git apply ../lalsuite_0000_cleanup.patch && \
	git apply ../lalsuite_0001_variable_epsilon.patch && \
	./00boot && \
	./configure --prefix=$PREFIX --enable-swig-python && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p

RUN --mount=type=cache,target=/root/ccache \
	p=lalsuite-extra && \
	echo -e "\\n\\n>> [`date`] Cloning $p" && \
	git clone https://git.ligo.org/lscsoft/$p.git && \
	cd $p && \
	git checkout 9d8b175df5348ee27159b669f9fe34693386c60c && \
	./00boot && \
	./configure --prefix=$PREFIX && \
	make -j && \
	make install && \
	cd .. && \
	rm -r $p

COPY glue_0000_zipsafe.patch .
RUN --mount=type=cache,target=/root/ccache \
	p=glue && \
	echo -e "\\n\\n>> [`date`] Cloning $p" && \
	git clone https://git.ligo.org/lscsoft/$p.git && \
	cd $p && \
	git checkout glue-release-1.59.2 && \
	git apply ../glue_0000_zipsafe.patch && \
	${PYTHON2} setup.py install --prefix=$PREFIX && \
	cd .. && \
	rm -r $p

RUN --mount=type=cache,target=/root/ccache \
	p=OpenBLAS && \
	git clone https://github.com/xianyi/$p.git && \
	cd $p && \
	git checkout v0.2.20 && \
	make -j TARGET=ZEN && \
	make PREFIX=$PREFIX install && \
	cd .. && \
	rm -r $p

RUN --mount=type=cache,target=/root/ccache \
	p=cfitsio3450 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts --no-check-certificate https://heasarc.gsfc.nasa.gov/FTP/software/fitsio/c/$p.tar.gz && \
	tar -xzf $p.tar.gz && \
	cd cfitsio && \
	./configure --prefix=$PREFIX && \
	make -j shared && \
	make install && \
	cd .. && \
	rm -r cfitsio && rm $p.tar.gz

RUN --mount=type=cache,target=/root/ccache \
	p=Healpix_3.50_2018Dec10 && \
	echo -e "\\n\\n>> [`date`] building $p" && \
	wget $wget_opts https://sourceforge.net/projects/healpix/files/Healpix_3.50/$p.tar.gz && \
	tar -xzf $p.tar.gz && \
	cd Healpix_3.50 && \
	printf '1\n\n\n\ngv\n\n2\n\n\n\n\n\n\n/usr/spiir/lib\n\ny\n4\n\n\n4\n0\n' | ./configure && \
	make -j
# Can't install into different directory
# cd .. && \
# rm -r $p && rm $p.tar.gz

# COPY gstlal_0001patrick_fix_includes_revised.patch .
RUN --mount=type=cache,target=/root/ccache \
	p=spiir && \
	echo -e "\\n\\n>> [`date`] Cloning $p" && \
	git clone --no-checkout https://git.ligo.org/lscsoft/$p.git

COPY gstlal /spiir/gstlal
RUN --mount=type=cache,target=/root/ccache \
	cd /spiir/gstlal && \
	make distclean || true && \
	# git apply ../../gstlal_0001patrick_fix_includes_revised.patch && \
	yes | head -n1 | ./00init.sh && \
	CFLAGS="-Og -fdebug-prefix-map=..=$(readlink -f ..) -gdwarf-2" CXXFLAGS="-Og -fdebug-prefix-map=..=$(readlink -f ..) -gdwarf-2" XDG_DATA_DIRS=$PREFIX/share:${XDG_DATA_DIRS:-} ./configure --prefix=$PREFIX && \
	XDG_DATA_DIRS=$PREFIX/share:${XDG_DATA_DIRS:-} make -j && \
	XDG_DATA_DIRS=$PREFIX/share:${XDG_DATA_DIRS:-} make install

COPY gstlal-inspiral /spiir/gstlal-inspiral
RUN --mount=type=cache,target=/root/ccache \
	cd /spiir/gstlal-inspiral && \
	make distclean || true && \
	yes | head -n1 | ./00init.sh && \
	CFLAGS="-Og -fdebug-prefix-map=..=$(readlink -f ..) -gdwarf-2" CXXFLAGS="-Og -fdebug-prefix-map=..=$(readlink -f ..) -gdwarf-2" ./configure --prefix=$PREFIX && \
	make -j && \
	make install

COPY gstlal-ugly /spiir/gstlal-ugly
RUN --mount=type=cache,target=/root/ccache \
	cd /spiir/gstlal-ugly && \
	make distclean || true && \
	yes | head -n1 | ./00init.sh && \
	CFLAGS="-Og -fdebug-prefix-map=..=$(readlink -f ..) -gdwarf-2" CXXFLAGS="-Og -fdebug-prefix-map=..=$(readlink -f ..) -gdwarf-2" ./configure --prefix=$PREFIX && \
	make -j && \
	make install

COPY gstlal-spiir /spiir/gstlal-spiir
RUN --mount=type=cache,target=/root/ccache \
	cd /spiir/gstlal-spiir && \
	make distclean || true && \
	yes | head -n1 | ./00init.sh && \
	CFLAGS="-Og -fdebug-prefix-map=..=$(readlink -f ..) -gdwarf-2" CXXFLAGS="-Og -fdebug-prefix-map=..=$(readlink -f ..) -gdwarf-2" ./configure --prefix=$PREFIX --with-cuda=/usr/local/cuda && \
	make && \
	make install

COPY generate_pipeline_artifacts.sh .

ENTRYPOINT ["gstlal_inspiral_postcohspiir_online", "--job-tag", "000", \
	"--iir-bank", "H1:/iir_H1-GSTLAL_SPLIT_BANK_0003-a1-0-0.xml.gz,L1:/iir_L1-GSTLAL_SPLIT_BANK_0003-a1-0-0.xml.gz,V1:/iir_V1-GSTLAL_SPLIT_BANK_0003-a1-0-0.xml.gz", \
	"--data-source", "frames", \
	"--frame-cache", "/frame.cache.C00", \
	"--gps-start-time", "1187008582", \
	"--gps-end-time", "1187008932", \
	"--track-psd", \
	"--channel-name", "H1=GDS-CALIB_STRAIN", \
	"--channel-name", "L1=GDS-CALIB_STRAIN", \
	"--channel-name", "V1=Hrec_hoft_16384Hz", \
	"--cohfar-accumbackground-output-prefix", "000/bank0_stats", \
	"--cohfar-accumbackground-snapshot-interval", "200", \
	"--cohfar-assignfar-silent-time", "0", \
	"--cohfar-assignfar-input-fname", "000/marginalized_1w.xml.gz,000/marginalized_1d.xml.gz,000/marginalized_2h.xml.gz", \
	"--cohfar-assignfar-refresh-interval", "200", \
	"--gpu-acc", "on", \
	"--ht-gate-threshold", "15.0", \
	"--cuda-postcoh-snglsnr-thresh", "4", \
	"--cuda-postcoh-hist-trials", "100", \
	"--cuda-postcoh-detrsp-fname", "/H1L1V1_detrsp_map_1187008882.xml", \
	"--cuda-postcoh-detrsp-refresh-interval", "86400", \
	"--cuda-postcoh-output-skymap", "7", \
	"--check-time-stamp", \
	"--finalsink-fapupdater-collect-walltime", "604800,86400,7200", \
	"--finalsink-fapupdater-interval", "1800", \
	"--finalsink-output-prefix", "000/000_zerolag", \
	"--finalsink-snapshot-interval", "1200", \
	"--finalsink-cluster-window", "1", \
	"--finalsink-far-factor", "2", \
	"--finalsink-singlefar-veto-thresh", "0.5", \
	"--finalsink-superevent-thresh", "0.0001", \
	"--finalsink-need-online-perform", "1", \
	"--finalsink-gracedb-far-threshold", "0.0001", \
	"--code-version", "unit_testing", \
	"--verbose"]
