# syntax = docker/dockerfile:1.4.2
FROM nvidia/cuda:11.2.2-devel-ubuntu20.04 as build

# Maintenance note
LABEL name="SPIIR Runtime Image" \
      maintainer="Luke Davis <luke.davis@uwa.edu.au>" \
      date="02-03-2023"

# Make sure apt doesn't clean cache
RUN rm -f /etc/apt/apt.conf.d/docker-clean; echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache

ARG DEBUG

# Cache mounts explained here: https://github.com/moby/buildkit/blob/master/frontend/dockerfile/docs/reference.md#example-cache-apt-packages
# EOF heredocs explained here: https://github.com/moby/buildkit/blob/master/frontend/dockerfile/docs/reference.md#here-documents
# Install required apt dependencies
RUN --mount=type=cache,target="/var/cache/apt",sharing=locked --mount=type=cache,target="/var/lib/apt",sharing=locked \
	<<EOF
	apt-get update
	DEBIAN_FRONTEND="noninteractive" apt-get install -y --no-install-recommends tzdata
	apt-get install -y --no-install-recommends \
		autoconf \
		automake \
		bison \
		build-essential \
		ca-certificates \
		ccache \
		cmake \
		doxygen \
		flex \
		git \
		gfortran \
		gtk-doc-tools \
		libblas-dev \
		libbz2-dev \
		libc6-dbg \
		libcairo2-dev \
		libcurl4-gnutls-dev \
		libffi-dev \
		libfreetype6-dev \
		libgdm-dev \
		libgeos-dev \
		libjsoncpp-dev \
		liblapack-dev \
		liblzma-dev \
		libncurses-dev \
		libopenmpi-dev \
		libpcre3-dev \
		libscalapack-openmpi-dev \
		libssl-dev \
		libsqlite3-dev \
		libtool \
		libtool-bin \
		patch \
		perlbrew \
		pkg-config \
		software-properties-common \
		sqlite3 \
		texinfo \
		tmux \
		uuid-dev \
		vim \
		wget \
		xorg-dev \
		zlib1g-dev
	apt-get -y autoremove
EOF

# Setup ccache compile caching
RUN mkdir -p "/root/ccache" && ccache --set-config=cache_dir="/root/ccache"

# Build flags
ENV SYSTEM_PYTHONPATH="${PYTHONPATH:-}"
ENV SYSTEM_PATH="${PATH:-}"
ENV SYSTEM_PKG_CONFIG_PATH="${PKG_CONFIG_PATH:-}"
ENV SYSTEM_LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
ENV PREFIX="/usr/spiir"
ENV ACLOCAL_PATH="${PREFIX}/share/aclocal"
ENV COMP_FLAGS="${DEBUG:+'-fPIC -O3'}"
ENV COMP_FLAGS="${COMP_FLAGS:--fPIC -O3 -DNDEBUG}"
ENV CFLAGS="${COMP_FLAGS}"
ENV CXXFLAGS="${COMP_FLAGS}"
ENV CPPFLAGS="${COMP_FLAGS}"
ENV FFLAGS="${COMP_FLAGS}"
ENV FCFLAGS="${COMP_FLAGS}"

ENV PATH="/usr/lib/ccache:${PREFIX}/gcc/bin:${PREFIX}/Healpix_3.50/src/cxx/optimized_gcc/bin:${PREFIX}/bin:${PREFIX}/python3/bin:${PATH}"
ENV LD_LIBRARY_PATH="${PREFIX}/Healpix_3.50/lib:${PREFIX}/Healpix_3.50/src/cxx/optimized_gcc/lib:${PREFIX}/lib:${PREFIX}/lib/x86_64-linux-gnu:/usr/local/lib/x86_64-unknown-linux-gnu:${LD_LIBRARY_PATH}"
ENV LIBRARY_PATH="${LD_LIBRARY_PATH}"
ENV PKG_CONFIG_PATH="${PREFIX}/Healpix_3.50/lib:${PREFIX}/lib/pkgconfig:${PREFIX}/lib/x86_64-linux-gnu/pkgconfig:/usr/lib/x86_64-linux-gnu/pkgconfig:${PKG_CONFIG_PATH}"

# Use ccache binaries instead of native gcc/g++/clang-13
RUN /usr/sbin/update-ccache-symlinks

RUN mkdir -p /src

# Helper functions to build packages.
COPY <<-EOF "/docker_build_helper_functions.sh"
	build_package() {
		if [ -z "\${SOURCE}" ] ; then
			echo "SOURCE must be set."
			exit
		fi

		BUILD_TYPE="\${BUILD_TYPE:-make}"
		FETCH_TYPE="\${FETCH_TYPE:-tarball}"

		SOURCE_BASE="\$(basename \${SOURCE})"
		echo "Building \${SOURCE_BASE}"

		if [[ "\$FETCH_TYPE" == "git" ]]; then
			if [ -z "\$GIT_VERSION" ]; then
				echo "GIT_VERSION must be set."
				exit
			fi

			eval \${PRE_FETCH}
			# We download/clone src files and then move them to cache instead of directly into cache in case build process is killed during download.
			(pushd "/src/\${SOURCE_BASE}" && git pull && popd && cp -r "/src/\${SOURCE_BASE}" .) || (git clone "\${SOURCE}" && cp -r "\${SOURCE_BASE}" "/src/")
			eval \${POST_FETCH}

			SOURCE_DIR="\${SOURCE_BASE}"
			pushd "\${SOURCE_DIR}"
			git checkout \${GIT_VERSION}
		elif [[ "\$FETCH_TYPE" == "tarball" ]]; then
			eval \${PRE_FETCH}
			# We download/clone src files and then move them to cache instead of directly into cache in case build process is killed during download.
			(test -f "/src/\${SOURCE_BASE}") || (wget "\${SOURCE}" && mv "\${SOURCE_BASE}" "/src/")
			eval \${POST_FETCH}

			tar -xf "/src/\${SOURCE_BASE}"
			SOURCE_DIR="\$(tar -tf /src/\${SOURCE_BASE} | sed -e 's@/.*@@' | uniq)"
			pushd "\${SOURCE_DIR}"
		fi

		if [[ "\$BUILD_TYPE" == "pip" ]]; then
			if [ -z "\$PIP" ]; then
				echo "PIP must be set."
				exit
			fi

			eval \${PRE_INSTALL}
			\${PIP} install \${PIP_FLAGS} .\${PIP_OPTIONAL}
			eval \${POST_INSTALL}
		elif [[ "\$BUILD_TYPE" == "meson" ]]; then
			eval \${PRE_CONFIGURE}
			meson _build --prefix="\${PREFIX}" \${MESON_FLAGS}
			eval \${POST_CONFIGURE}

			eval \${PRE_BUILD}
			ninja -v -C _build \${JOBS}
			eval \${POST_BUILD}

			eval \${PRE_INSTALL}
			ninja -C _build install \${JOBS}
			eval \${POST_INSTALL}
		elif [[ "\$BUILD_TYPE" == "cmake" ]]; then
			mkdir build
			pushd build

			eval \${PRE_CONFIGURE}
			cmake \${CMAKE_FLAGS} -DCMAKE_INSTALL_PREFIX:PATH="\${PREFIX}" ..
			eval \${POST_CONFIGURE}

			eval \${PRE_BUILD}
			cmake --build . -j \${JOBS}
			eval \${POST_BUILD}

			eval \${PRE_INSTALL}
			cmake --build . --target install -j \${JOBS}
			eval \${POST_INSTALL}

			popd
		elif [[ "\$BUILD_TYPE" == "make" ]]; then
			eval \${PRE_CONFIGURE}
			./configure --prefix="\${PREFIX}" \${CONFIGURE_FLAGS}
			eval \${POST_CONFIGURE}

			eval \${PRE_BUILD}
			make -j\${JOBS} \${MAKE_FLAGS}
			eval \${POST_BUILD}

			eval \${PRE_INSTALL}
			make install -j\${JOBS}
			eval \${POST_INSTALL}
		fi

		popd

		# Delete extracted source directory if not a DEBUG build, to save space.
		if [ -z "\${DEBUG}" ] ; then
			rm -r "\${SOURCE_DIR}"
		fi
	}
EOF

ENV BASH_ENV="/docker_build_helper_functions.sh"

SHELL ["/bin/bash", "-e", "-c"]

# We download/clone src files and then move them to cache instead of directly into cache in case build process is killed during download.
RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="https://ftp.gnu.org/gnu/gcc/gcc-10.4.0/gcc-10.4.0.tar.gz"
	PRE_CONFIGURE="./contrib/download_prerequisites"
	CFLAGS="${CFLAGS} -Wno-error"
	CXXFLAGS="${CXXFLAGS} -Wno-error"
	PREFIX="${PREFIX}/gcc"
	CONFIGURE_FLAGS="--disable-multilib --enable-languages=c,c++,fortran"
	build_package
	/usr/sbin/update-ccache-symlinks
EOF

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	SOURCE="https://support.hdfgroup.org/ftp/HDF5/releases/hdf5-1.8/hdf5-1.8.22/src/hdf5-1.8.22.tar.gz" build_package

# HDF5 does not include it's own pkg-config file, which we need.
COPY <<-EOF "${PREFIX}/lib/pkgconfig/hdf5.pc"
	prefix=${PREFIX}
	exec_prefix=\${prefix}
	includedir=\${prefix}/include
	libdir=\${exec_prefix}/lib
	Name: hdf5
	Description: HDF5
	Version: 1.8.22
	Requires.private: zlib
	Cflags: -I\${includedir}
	Libs: -L\${libdir} -lhdf5
EOF

ENV PYTHON2PREFIX="${PREFIX}"
RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="https://www.python.org/ftp/python/2.7.14/Python-2.7.14.tgz"
	PREFIX="${PYTHON2PREFIX}"
	if [ -z "${DEBUG}" ]; then 
		PYTHONFLAGS="--enable-optimizations"
		EXTRA_CFLAGS="-DLLTRACE -DWITH_PYMALLOC"
	fi
	CONFIGURE_FLAGS="--enable-shared ${PYTHONFLAGS}"
	POST_INSTALL="
		sed -i '127,185s/###//g' Misc/valgrind-python.supp &&
		cp 'Misc/valgrind-python.supp' '${PYTHON2PREFIX}/'"
	build_package
EOF

# Numpy needs to be installed first to be a dependency for the others.
ENV PIP_FLAGS="${DEBUG:+'--no-clean'}"
ENV PYTHON2="${PYTHON2PREFIX}/bin/python"
ENV PIP2="${PYTHON2PREFIX}/bin/pip"
RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/.cache" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	${PYTHON2} -m ensurepip --upgrade 
	${PIP2} install --upgrade pip setuptools wheel
	${PIP2} install --upgrade ${PIP_FLAGS} numpy==1.14.1
	${PIP2} install --upgrade ${PIP_FLAGS} \
		astropy==2.0.3 \
		clang-format==14.0.6 \
		cryptography==3.3.2 \
		Cython==0.29.32 \
		h5py==2.7.1 \
		healpy==1.12.4 \
		ligo.gracedb==2.7.7 \
		ligo-segments==1.4.0 \
		matplotlib==2.2.2 \
		pyopenssl==21.0.0 \
		scipy==1.0.0 \
		shapely==1.7.1 \
		yapf==0.32.0
EOF

ENV PYTHON3PREFIX="${PREFIX}/python3"
RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="https://www.python.org/ftp/python/3.8.13/Python-3.8.13.tgz"
	PREFIX="${PYTHON3PREFIX}"
	if [ -z "${DEBUG}" ]; then 
		PYTHONFLAGS="--enable-optimizations"
		EXTRA_CFLAGS="-DLLTRACE -DWITH_PYMALLOC"
	fi
	CONFIGURE_FLAGS="--enable-shared ${PYTHONFLAGS}"
	POST_INSTALL="
		sed -i '127,185s/###//g' Misc/valgrind-python.supp &&
		cp Misc/valgrind-python.supp ${PYTHON3PREFIX}/"
	build_package
EOF

# Python 3 is used to create skymaps, run meson, and run tests on pipeline output.
ENV LD_LIBRARY_PATH="${LD_LIBRARY_PATH}:${PYTHON3PREFIX}/lib"
ENV PYTHON3="${PYTHON3PREFIX}/bin/python3"
ENV PIP3="${PYTHON3PREFIX}/bin/pip3"
RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/.cache" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	${PYTHON3} -m ensurepip --upgrade
	${PIP3} install --upgrade pip setuptools==65.0.2 wheel
	${PIP3} install --upgrade ${PIP_FLAGS} \
		ligo.skymap==1.0.3 \
		meson==0.60.3 \
		ninja==1.10.2.4 \
		pandas==1.5.1
EOF

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	SOURCE="ftp://xmlsoft.org/libxml2/libxml2-2.9.12.tar.gz" build_package

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="ftp://ftp.fftw.org/pub/fftw/fftw-3.3.5.tar.gz"
	CONFIGURE_FLAGS="--enable-avx --enable-sse2"
	POST_INSTALL="
		./configure --prefix=${PREFIX} \
			--enable-avx \
			--enable-float \
			--enable-sse &&
		make -j &&
		make install -j"
	build_package
EOF

ARG IGWN_SOURCE=http://software.igwn.org/lscsoft/source

# Libframe's pkg-config file is not installed automatically
RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="${IGWN_SOURCE}/libframe-8.30.tar.gz"
	# make sure frame files are opened in binary mode
	PRE_CONFIGURE="sed -i~ 's/\\([Oo]pen.*\"r\\)\"/\\1b\"/;' src/FrameL.c"
	POST_INSTALL="
		mkdir -p '${PREFIX}/lib/pkgconfig' &&
		sed 's%^prefix=.*%prefix=${PREFIX}%' src/libframe.pc > ${PREFIX}/lib/pkgconfig/libframe.pc"
	build_package
EOF

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	SOURCE="${IGWN_SOURCE}/metaio-8.4.0.tar.gz" build_package

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="https://sourceforge.net/projects/swig/files/swig/swig-3.0.12/swig-3.0.12.tar.gz"
	PREFIX="${PYTHON2PREFIX}"
	CONFIGURE_FLAGS="--with-python \
		--without-allegrocl \
		--without-android \
		--without-chicken \
		--without-clisp \
		--without-csharp \
		--without-d \
		--without-gcj \
		--without-go \
		--without-guile \
		--without-java \
		--without-javascript \
		--without-lua \
		--without-mzscheme \
		--without-ocaml \
		--without-octave \
		--without-perl5 \
		--without-php \
		--without-pike \
		--without-python3 \
		--without-r \
		--without-ruby \
		--without-scilab \
		--without-tcl"
	POST_INSTALL="cp Examples/test-suite/python/pythonswig.supp ${PYTHON2PREFIX}/"
	build_package
EOF

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="https://sourceforge.net/projects/swig/files/swig/swig-4.0.2/swig-4.0.2.tar.gz"
	PREFIX="${PYTHON3PREFIX}"
	CONFIGURE_FLAGS="--without-allegrocl \
		--without-android \
		--without-chicken \
		--without-clisp \
		--without-csharp \
		--without-d \
		--without-gcj \
		--without-go \
		--without-guile \
		--without-java \
		--without-javascript \
		--without-lua \
		--without-mzscheme \
		--without-ocaml \
		--without-octave \
		--without-perl5 \
		--without-pike \
		--without-php \
		--with-python3 \
		--without-python \
		--without-r \
		--without-ruby \
		--without-scilab \
		--without-tcl"
	POST_INSTALL="cp Examples/test-suite/python/pythonswig.supp ${PYTHON3PREFIX}/"
	build_package
EOF

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	SOURCE="ftp://ftp.fu-berlin.de/unix/gnu/gsl/gsl-2.6.tar.gz" build_package

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	SOURCE="https://ftp.gnu.org/pub/gnu/gettext/gettext-0.20.1.tar.gz" build_package

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="${IGWN_SOURCE}/ldas-tools-al-2.5.7.tar.gz"
	CC="/usr/bin/gcc"
	CXX="/usr/bin/g++"
	CONFIGURE_FLAGS="--disable-warnings-as-errors"
	POST_INSTALL="cp src/std.supp ${PREFIX}/"
	build_package
EOF

COPY .gitlab-ci/patches/framecpp_0000_Makefile_fix.patch /src/.gitlab-ci/patches/framecpp_0000_Makefile_fix.patch
RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="${IGWN_SOURCE}/ldas-tools-framecpp-2.5.8.tar.gz"
	CONFIGURE_FLAGS="--disable-warnings-as-errors --without-doxygen"
	POST_CONFIGURE="
		pushd swig/python &&
		patch /src/.gitlab-ci/patches/framecpp_0000_Makefile_fix.patch &&
		popd"
	build_package
EOF

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="https://mirrors.edge.kernel.org/pub/linux/utils/util-linux/v2.34/util-linux-2.34.tar.xz"
	CONFIGURE_FLAGS="--disable-all-programs \
		--enable-libblkid \
		--enable-libmount \
		--disable-use-tty-group"
	build_package
EOF

ENV MESON_FLAGS="${DEBUG:+ }"
ENV MESON_FLAGS="${MESONFLAGS:---buildtype=release}"

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	SOURCE="https://ftp.gnome.org/pub/gnome/sources/glib/2.62/glib-2.62.3.tar.xz" BUILD_TYPE="meson" build_package

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	SOURCE="https://ftp.gnome.org/pub/GNOME/sources/gobject-introspection/1.63/gobject-introspection-1.63.1.tar.xz" BUILD_TYPE="meson" build_package

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	SOURCE="https://www.cairographics.org/releases/pixman-0.40.0.tar.gz" build_package

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	SOURCE="https://github.com/glennrp/libpng" FETCH_TYPE="git" GIT_VERSION="v1.6.39" build_package

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	SOURCE="https://ftp.acc.umu.se/pub/GNOME/sources/pygobject/2.28/pygobject-2.28.7.tar.xz" build_package

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	SOURCE="https://ftp.gnome.org/pub/GNOME/sources/pygtk/2.24/pygtk-2.24.0.tar.bz2" build_package

# Get valgrind
RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	SOURCE="https://sourceware.org/pub/valgrind/valgrind-3.20.0.tar.bz2" build_package

# Debug build flags
ARG DEBUGMEMORY
ARG DEBUGTHREADS
ARG DEBUGADDRESS
ARG DEBUGUB
ENV SAN="${SAN:-${DEBUGMEMORY}}"
ENV SAN="${SAN:-${DEBUGTHREADS}}"
ENV SAN="${SAN:-${DEBUGADDRESS}}"
ENV SAN="${SAN:-${DEBUGUB}}"
RUN <<EOF
	if [[ -n "${SAN}" && -z "${DEBUG}" ]] ; then 
		echo "DEBUG build-arg must be set if any sanitizer build-arg is set (DEBUGMEMORY, DEBUGTHREADS, DEBUGADDRESS, DEBUGUB)"
		exit 0
	fi
EOF
ENV DEBUGFLAGS="${DEBUG:+'-Og -ggdb -fno-omit-frame-pointer -rdynamic'}"
ENV GSTDEBUGFLAGS="${DEBUG:+'--enable-debug'}"
ENV CFLAGS="${CFLAGS} ${DEBUGFLAGS}"
ENV CXXFLAGS="${CXXFLAGS} ${DEBUGFLAGS}"

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	SOURCE="https://gstreamer.freedesktop.org/data/src/orc/orc-0.4.33.tar.xz" BUILD_TYPE="meson" build_package

ARG GSTREAMER_GIT="https://gitlab.freedesktop.org/gstreamer"
ENV GST_PLUGIN_PATH="${PREFIX}/lib/gstreamer-0.10"

COPY .gitlab-ci/patches/manoj_00_gstreamer.patch /repo/.gitlab-ci/patches/manoj_00_gstreamer.patch
RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="${GSTREAMER_GIT}/gstreamer"
	FETCH_TYPE="git"
	GIT_VERSION="0.10"
	PRE_CONFIGURE="
		git apply /repo/.gitlab-ci/patches/manoj_00_gstreamer.patch &&
		NOCONFIGURE=1 ./autogen.sh"
	CONFIGURE_FLAGS="${GSTDEBUGFLAGS}"
	POST_INSTALL="cp common/gst.supp ${PREFIX}/"
	build_package
EOF

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="${GSTREAMER_GIT}/gst-plugins-base"
	FETCH_TYPE="git"
	GIT_VERSION="0.10"
	PRE_CONFIGURE="NOCONFIGURE=1 ./autogen.sh"
	CONFIGURE_FLAGS="${GSTDEBUGFLAGS}"
	POST_INSTALL="cp tests/check/gst-plugins-base.supp ${PREFIX}/"
	build_package
EOF

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="${GSTREAMER_GIT}/gst-plugins-good"
	FETCH_TYPE="git"
	GIT_VERSION="0.10"
	PRE_CONFIGURE="NOCONFIGURE=1 ./autogen.sh"
	CONFIGURE_FLAGS="--disable-gst_v4l2 ${GSTDEBUGFLAGS}"
	POST_INSTALL="cp tests/check/gst-plugins-good.supp ${PREFIX}/"
	build_package
EOF

# gst-python can't find python libs without specifically adding the link flag
RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="${GSTREAMER_GIT}/gst-python"
	FETCH_TYPE="git"
	GIT_VERSION="0.10"
	PRE_CONFIGURE="NOCONFIGURE=1 ./autogen.sh"
	CFLAGS="-lpython2.7 -Wno-error ${CFLAGS}"
	CONFIGURE_FLAGS="${GSTDEBUGFLAGS}"
	POST_INSTALL="cp testsuite/gstpython.supp ${PREFIX}/"
	build_package
EOF

ARG LIGO_GIT=https://git.ligo.org/lscsoft

COPY .gitlab-ci/patches/LALInspiralIIR.patch /repo/.gitlab-ci/patches/LALInspiralIIR.patch
RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="${LIGO_GIT}/lalsuite"
	# Known working commit as of 23/5/2022
	FETCH_TYPE="git"
	GIT_VERSION="aee3feddee701355506c109029fd1ae574ae56c5"
	CFLAGS="-Wno-error ${CFLAGS}"
	CXXFLAGS="-Wno-error ${CXXFLAGS}"
	PRE_CONFIGURE="./00boot"
	PRE_BUILD="git apply /repo/.gitlab-ci/patches/LALInspiralIIR.patch"
	CONFIGURE_FLAGS="--enable-swig-python"
	build_package
EOF

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="${LIGO_GIT}/lalsuite-extra"
	# Known working commit as of 23/5/2022
	FETCH_TYPE="git"
	GIT_VERSION="9d8b175df5348ee27159b669f9fe34693386c60c"
	PRE_CONFIGURE="./00boot"
	build_package
EOF

COPY .gitlab-ci/patches/glue_0000_zipsafe.patch /repo/.gitlab-ci/patches/glue_0000_zipsafe.patch 
RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="${LIGO_GIT}/glue"
	FETCH_TYPE="git"
	GIT_VERSION="glue-release-1.59.2"
	BUILD_TYPE="pip"
	PIP="${PIP2}"
	PRE_INSTALL="git apply /repo/.gitlab-ci/patches/glue_0000_zipsafe.patch"
	build_package
EOF

ENV CMAKE_FLAGS="${DEBUG:+' '}"
ENV CMAKE_FLAGS="${CMAKE_FLAGS:--DCMAKE_BUILD_TYPE=Release}"

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="https://github.com/xianyi/OpenBLAS"
	FETCH_TYPE="git"
	GIT_VERSION="v0.3.21"
	BUILD_TYPE="cmake"
	CMAKE_FLAGS="${CMAKE_FLAGS} -DBUILD_SHARED_LIBS=ON -DDYNAMIC_ARCH=TRUE -DDYNAMIC_OLDER=1"
	build_package
EOF

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	SOURCE="https://heasarc.gsfc.nasa.gov/FTP/software/fitsio/c/cfitsio3450.tar.gz"	MAKE_FLAGS="shared"	build_package

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	# Healpix install is odd so we don't use build_package
	SOURCE="https://sourceforge.net/projects/healpix/files/Healpix_3.50/Healpix_3.50_2018Dec10.tar.gz"
	SOURCE_BASE="$(basename ${SOURCE})"
	echo "Building ${SOURCE_BASE}"
	(test -f "/src/${SOURCE_BASE}") || (wget "${SOURCE}" && mv "${SOURCE_BASE}" "/src/")
	tar -xf "/src/${SOURCE_BASE}" -C "${PREFIX}"
	pushd "${PREFIX}/Healpix_3.50"
	# Purely interactive configure script, doesn't take arguments
	printf "1\n\n\n\ngv\n\n2\n\n\n\n\n\n\n${PREFIX}/lib\n\ny\n4\n\n\n4\n0\n" | ./configure
	make -j
	popd
	if [ -z "${DEBUG}" ] ; then
		rm -r "${PREFIX}/Healpix_3.50/doc"
	fi
EOF

ENV CONDAPREFIX="${PREFIX}/conda"
ENV CONDA="${CONDAPREFIX}/bin/conda"
RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/.cache" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	# Conda install is odd so we don't use build_package
	SOURCE="https://repo.continuum.io/miniconda/Miniconda3-latest-Linux-x86_64.sh"
	SOURCE_BASE="$(basename ${SOURCE})"
	echo "Building ${SOURCE_BASE}"
	(test -f "/src/${SOURCE_BASE}") || (wget "${SOURCE}" && mv "${SOURCE_BASE}" "/src/")
	cp "/src/${SOURCE_BASE}" .
	bash "${SOURCE_BASE}" -b -p "${CONDAPREFIX}"
	${CONDA} clean -afy
EOF

# Conda is the only way to install gds
RUN --mount=type=cache,target="/root/.cache" \
	--mount=type=cache,target="/root/ccache" \
	--mount=type=cache,target="${PREFIX}/conda/pkgs",sharing=locked \
	<<EOF
	${CONDA} install -y -c conda-forge gds-base=3.0.0 gds-framexmit python-gds dtt-awggui libffi=3.4.2=h295c915_4
	rm "${CONDAPREFIX}/lib/libtinfo.so.6"
EOF

# Add conda paths to build flags
ENV PKG_CONFIG_PATH="${PREFIX}/Healpix_3.50/lib:${PREFIX}/lib/pkgconfig/:${PREFIX}/lib/x86_64-linux-gnu/pkgconfig:${CONDAPREFIX}/lib/pkgconfig:/usr/lib/x86_64-linux-gnu/pkgconfig"
ENV LD_LIBRARY_PATH="${PREFIX}/Healpix_3.50/lib:${PREFIX}/Healpix_3.50/src/cxx/optimized_gcc/lib:${PREFIX}/lib:${PREFIX}/lib/x86_64-linux-gnu:${CONDAPREFIX}/lib:/usr/local/lib/x86_64-unknown-linux-gnu:/usr/local/cuda/lib64:${LD_LIBRARY_PATH:-}"
ENV LIBRARY_PATH="${LD_LIBRARY_PATH}"

# Install Clang if sanitizers are to be used.
RUN --mount=type=cache,target="/var/cache/apt",sharing=locked --mount=type=cache,target="/var/lib/apt",sharing=locked \
	--mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	if [ -n "${SAN}" ] ; then
		apt-get update
		apt-get install -y lsb-release wget software-properties-common gnupg
		(test -f "/src/llvm.sh") || (wget "https://apt.llvm.org/llvm.sh" && mv "llvm.sh" "/src/")
		chmod +x "/src/llvm.sh"
		/src/llvm.sh 11
		ln -s `which llvm-symbolizer-11` "/usr/bin/llvm-symbolizer"
		apt-get -y autoremove
	fi
EOF

ENV CC="${SAN:+clang-11}"
ENV CXX="${SAN:+clang++-11}"
ENV CC="${CC:-gcc}"
ENV CXX="${CXX:-g++}"

# Sanitizer build flags
ENV DEBUGADDRESSFLAGS="${DEBUGADDRESS:+'-fsanitize=address -fsanitize-recover=all -shared-libsan -fsanitize-address-use-after-scope -fsanitize=pointer-compare -fsanitize=pointer-subtract'}"
ENV DEBUGMEMORYFLAGS="${DEBUGMEMORY:+'-fsanitize=memory -fsanitize-recover=all -mllvm -msan-keep-going=1 -shared-libsan -fPIE -pie -fno-optimize-sibling-calls -fsanitize-memory-track-origins'}"
ENV DEBUGTHREADFLAGS="${DEBUGTHREADS:+'-fsanitize=thread -fsanitize-recover=all -shared-libsan -fPIE -pie'}"
ENV DEBUGUBFLAGS="${DEBUGUB:+'-fsanitize=undefined -fsanitize-recover=all -shared-libsan -fsanitize=integer -fsanitize=float-divide-by-zero -fsanitize=implicit-conversion -fsanitize=nullability -fsanitize=local-bounds'}"
ENV ASAN_OPTIONS="${DEBUGADDRESS:+'protect_shadow_gap=0:detect_leaks=1:fast_unwind_on_malloc=0:detect_invalid_pointer_pairs=2:detect_stack_use_after_return=1:halt_on_error=0'}"
ENV MSAN_OPTIONS="${DEBUGMEMORY:+'halt_on_error=0'}"
ENV TSAN_OPTIONS="${DEBUGTHREADS:+'history_size=4 force_seq_cst_atomics=1 halt_on_error=0'}"
ENV UBSAN_OPTIONS="${DEBUGUB:+'print_stacktrace=1'}"
ENV LSAN_OPTIONS="${SAN:+'verbosity=1:log_threads=1'}"
ENV SAN_LD_LIBRARY_PATH="${SAN:+'/usr/lib/llvm-11/lib/clang/11.1.0/lib/linux'}"
ENV LD_LIBRARY_PATH="${LD_LIBRARY_PATH}:${SAN_LD_LIBRARY_PATH}"
ENV LDFLAGS="${LDFLAGS} ${DEBUGMEMORYFLAGS} ${DEBUGTHREADFLAGS} ${DEBUGUBFLAGS} ${DEBUGADDRESSFLAGS}"
ENV CFLAGS="${CFLAGS} ${DEBUGMEMORYFLAGS} ${DEBUGTHREADFLAGS} ${DEBUGUBFLAGS} ${DEBUGADDRESSFLAGS}"
ENV CXXFLAGS="${CXXFLAGS} ${DEBUGMEMORYFLAGS} ${DEBUGTHREADFLAGS} ${DEBUGUBFLAGS} ${DEBUGADDRESSFLAGS}"

# Python tests for pipeline results
RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/.cache" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="https://git.ligo.org/spiir-group/spiir"
	# Known working commit as of 10/3/2023
	FETCH_TYPE="git"
	GIT_VERSION="c0e7224be9c447471d10237e2d04ca3b9aac5354"
	BUILD_TYPE="pip"
	PIP="${PIP3}"
	PIP_OPTIONAL="[all]"
	build_package
EOF

# Spiir build debug flags
ENV NVCC_SAN_FLAGS="${SAN:+'-ccbin clang-11'}"
ENV NVCC_DEBUG_FLAGS="${DEBUG:+'-g -G'}"
ENV NVCC_APPEND_FLAGS="-gencode=arch=compute_86,code=sm_86 ${NVCC_SAN_FLAGS} ${NVCC_DEBUG_FLAGS}"

# Runtime flags
# Deterministic whitening
ENV GSTLAL_FIR_WHITEN="1"
ENV G_SLICE="${DEBUG:+'always-malloc'}"
ENV G_DEBUG="${DEBUG:+'gc-friendly'}"
ENV GST_DEBUG="${DEBUG:+'cohfar_accumbackground:4,cuda_postcoh:4,cohfar_assignfar:4,cuda_multiratespiir:4,postcoh_filesink:4'}"
ENV GST_DEBUG_NO_COLOR="${DEBUG:+'1'}"

FROM build AS runtime

COPY gstlal /repo/gstlal
COPY .gitlab-ci/patches/gstlal_0001patrick_fix_includes_revised.patch /repo/.gitlab-ci/patches/gstlal_0001patrick_fix_includes_revised.patch
RUN --mount=type=cache,target="/root/ccache" \
	<<EOF
	cd /repo
	git apply "/repo/.gitlab-ci/patches/gstlal_0001patrick_fix_includes_revised.patch"
	cd /repo/gstlal
	make distclean || true
	yes | head -n1 | ./00init.sh
	XDG_DATA_DIRS="${PREFIX}/share:${XDG_DATA_DIRS}" ./configure --prefix="${PREFIX}"
	XDG_DATA_DIRS="${PREFIX}/share:${XDG_DATA_DIRS}" make -j
	XDG_DATA_DIRS="${PREFIX}/share:${XDG_DATA_DIRS}" make install -j
EOF

COPY gstlal-inspiral /repo/gstlal-inspiral
RUN --mount=type=cache,target="/root/ccache" \
	<<EOF
	cd /repo/gstlal-inspiral
	make distclean || true
	yes | head -n1 | ./00init.sh
	./configure --prefix="${PREFIX}"
	make -j
	make install -j
EOF

COPY gstlal-ugly /repo/gstlal-ugly
RUN --mount=type=cache,target="/root/ccache" \
	<<EOF
	cd /repo/gstlal-ugly
	make distclean || true
	yes | head -n1 | ./00init.sh
	./configure --prefix="${PREFIX}"
	# When built with clang, the std=c++ build flag must be removed from the CFLAGS in 
	# the files that ./configure creates. The modification times of generated files and
	# dependencies must be updated in order so they don't get regenerated.
	find . -type f -exec sed -i 's/framecpp_CFLAGS\(.*\)-std=c++11/framecpp_CFLAGS\1/g' {} +
	touch *.m4
	touch *.am
	touch Makefile.in */Makefile.in
	touch configure
	touch config.status
	touch Makefile
	make -j
	make install -j
EOF

# If FORCE_EARLY_UPLOADS=1, patch postcoh_finalsink.py to skip far validation and output coinc.xml's on small runs.
ARG FORCE_EARLY_UPLOADS

# If SET_DETERMINISTIC=1, spiir will be built to be deterministic at the cost of performance.
ARG SET_DETERMINISTIC

COPY gstlal-spiir /repo/gstlal-spiir
COPY .gitlab-ci/patches/force_early_uploads.patch /repo/.gitlab-ci/patches/force_early_uploads.patch
RUN --mount=type=cache,target="/root/ccache" \
	<<EOF
	cd /repo
	if [ -n "${FORCE_EARLY_UPLOADS}" ] ; then git apply /repo/.gitlab-ci/patches/force_early_uploads.patch; fi
	if [ -n "${SET_DETERMINISTIC}" ] ; then CFLAGS="${CFLAGS} -DMULTIRATESPIIR_USE_WARP_REDUCE"; fi
	cd /repo/gstlal-spiir
	make distclean || true
	yes | head -n1 | ./00init.sh
	export CFLAGS="${CFLAGS} -Wno-cast-function-type -Wno-unused-command-line-argument -Wno-unknown-warning-option -Wno-unused-function"
	for FLAG in ${CFLAGS}; do
		NVCC_APPEND_FLAGS="${NVCC_APPEND_FLAGS} -Xcompiler ${FLAG}"
	done
	./configure --prefix="${PREFIX}" --with-cuda="/usr/local/cuda"
	make -j
	make install -j
EOF

RUN <<EOF
	apt list --installed
	${CONDA} list
	${PIP2} list
	${PIP3} list
	printenv
	# Reduce inode usage by deleting test and cache files.
	if [ -z "${DEBUG}" ] ; then
		find "${PREFIX}" | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

ENV PYTHONPATH="${PREFIX}/lib/python2.7/site-packages"

COPY .git /repo/.git
COPY .gitlab-ci /repo/.gitlab-ci
WORKDIR /repo

ENTRYPOINT [ "/repo/.gitlab-ci/submit_runs.sh", "-y" ]
