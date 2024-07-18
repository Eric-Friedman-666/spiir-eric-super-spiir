# syntax = docker/dockerfile:1.4.2
FROM nvidia/cuda:11.8.0-devel-ubuntu22.04 as build

# Maintenance note
LABEL name="SPIIR Runtime Image" \
	maintainer="Luke Davis <luke.davis@uwa.edu.au>" \
	date="28-08-2023"

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
		ccache \
		cmake \
		doxygen \
		flex \
		gfortran \
		git \
		gi-docgen \
		libboost1.74-all-dev \
		libbz2-dev \
		libc6-dbg \
		libchealpix-dev \
		libcups2-dev \
		libdaemon-dev \
		libdv4-dev \
		libepoxy-dev \
		libfdk-aac-dev \
		libfftw3-dev \
		libfontconfig-dev \
		libfribidi-dev \
		libgdk-pixbuf-2.0-dev \
		libgfortran-12-dev \
		libgraphene-1.0-dev \
		libgsl-dev \
		libharfbuzz-dev \
		libhdf5-dev \
		liblapack-dev \
		libmono-posix4.0-cil \
		libnice-dev \
		libogg-dev \
		libopus-dev \
		libpango1.0-dev \
		libpixman-1-dev \
		libpng-dev \
		libsoup-3.0-dev \
		libsqlite3-dev \
		libssl-dev \
		libtool \
		libtool-bin \
		libvorbis-dev \
		libxkbcommon-dev \
		lsb-release \
		meson \
		monodoc-base \
		mono-mcs \
		pkg-config \
		py3c-dev \
		qtbase5-dev \
		shared-mime-info \
		software-properties-common \
		tmux \
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
ENV COMP_FLAGS="${COMP_FLAGS:-'-fPIC -O3 -DNDEBUG'}"
ENV CFLAGS="${COMP_FLAGS}"
ENV CXXFLAGS="${COMP_FLAGS}"
ENV CPPFLAGS="${COMP_FLAGS}"
ENV FFLAGS="${COMP_FLAGS}"
ENV FCFLAGS="${COMP_FLAGS}"

ENV PATH="/usr/lib/ccache:${PREFIX}/bin:${PATH}"
ENV LD_LIBRARY_PATH="${PREFIX}/lib:${PREFIX}/lib/x86_64-linux-gnu:/usr/local/cuda/lib64:/usr/local/lib/x86_64-unknown-linux-gnu:${LD_LIBRARY_PATH}"
ENV LIBRARY_PATH="${LD_LIBRARY_PATH}"
ENV PKG_CONFIG_PATH="${PREFIX}/lib/pkgconfig:${PREFIX}/lib/x86_64-linux-gnu/pkgconfig:/usr/lib/x86_64-linux-gnu/pkgconfig:${PKG_CONFIG_PATH}"
ENV PYTHONPATH="${PREFIX}/lib/python3.10/site-packages"
ENV XDG_DATA_DIRS="${PREFIX}/share:/usr/share"

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

# Get valgrind
RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	SOURCE="https://sourceware.org/pub/valgrind/valgrind-3.21.0.tar.bz2" build_package

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

ENV PYTHON3PREFIX="${PREFIX}"
RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="https://www.python.org/ftp/python/3.10.13/Python-3.10.13.tgz"
	PREFIX="${PYTHON3PREFIX}"
	if [ -z "${DEBUG}" ]; then 
		PYTHONFLAGS="--enable-optimizations"
		export EXTRA_CFLAGS="-DLLTRACE -DWITH_PYMALLOC"
	fi
	CONFIGURE_FLAGS="--enable-shared ${PYTHONFLAGS}"
	POST_INSTALL="
		sed -i '127,185s/###//g' Misc/valgrind-python.supp &&
		cp Misc/valgrind-python.supp ${PYTHON3PREFIX}/"
	build_package
EOF

ENV PIP_FLAGS=${DEBUG:+"--no-clean"}
ENV PYTHON3="${PYTHON3PREFIX}/bin/python3"
ENV PIP3="${PYTHON3PREFIX}/bin/pip3"
ENV PIP="${PIP3}"
RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/.cache" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	ln -s $PREFIX/bin/python3 $PREFIX/bin/python
	${PYTHON3} -m ensurepip --upgrade
	${PIP3} install --upgrade pip setuptools wheel
	${PIP3} install --upgrade ${PIP_FLAGS} \
		astropy \
		clang-format \
		cryptography \
		Cython \
		h5py \
		healpy \
		matplotlib \
		meson \
		ninja \
		numpy \
		pandas \
		pyopenssl \
		scipy \
		shapely \
		yapf
EOF

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	SOURCE="https://ftp.gnome.org/pub/gnome/sources/glib/2.77/glib-2.77.2.tar.xz" BUILD_TYPE="meson" build_package

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	SOURCE="https://gitlab.freedesktop.org/cairo/cairo/-/archive/1.17.8/cairo.tar.gz" BUILD_TYPE="meson" build_package

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	SOURCE="https://download.gnome.org/sources/gobject-introspection/1.75/gobject-introspection-1.75.2.tar.xz" BUILD_TYPE="meson" build_package

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	SOURCE="https://download.gnome.org/sources/pygobject/3.44/pygobject-3.44.1.tar.xz" BUILD_TYPE="meson" build_package

ENV CMAKE_FLAGS=${DEBUG:+" "}
ENV CMAKE_FLAGS=${CMAKE_FLAGS:-"-DCMAKE_BUILD_TYPE=Release"}

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

ARG GSTREAMER_GIT="https://gitlab.freedesktop.org/gstreamer"
ENV GST_PLUGIN_PATH="${PREFIX}/lib/x86_64-linux-gnu/gstreamer-1.0:${PREFIX}/lib/gstreamer-1.0"
ENV GI_TYPELIB_PATH="${PREFIX}/lib/girepository-1.0:${PREFIX}/lib/x86_64-linux-gnu/girepository-1.0"

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="${GSTREAMER_GIT}/gstreamer"
	FETCH_TYPE="git"
	GIT_VERSION="1.22.5"
	BUILD_TYPE="meson"
	build_package
EOF

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	SOURCE="https://gstreamer.freedesktop.org/data/src/orc/orc-0.4.34.tar.xz" BUILD_TYPE="meson" build_package

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="https://download.gnome.org/sources/gtk/4.12/gtk-4.12.0.tar.xz"
	BUILD_TYPE="meson"
	# remove -DNDEBUG "Tests must not be built with NDEBUG defined, they rely on assert()."
	export CFLAGS="-fPIC -O3 $DEBUGFLAGS"
	export CPPFLAGS="-fPIC -O3 $DEBUGFLAGS"
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

ARG IGWN_SOURCE=http://software.igwn.org/lscsoft/source

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	SOURCE="${IGWN_SOURCE}/metaio-8.5.1.tar.gz" build_package

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="${IGWN_SOURCE}/ldas-tools-al-2.6.2.tar.gz"
	CC="/usr/bin/gcc"
	CXX="/usr/bin/g++"
	CONFIGURE_FLAGS="--disable-warnings-as-errors"
	POST_INSTALL="cp src/std.supp ${PREFIX}/"
	build_package
EOF

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="${IGWN_SOURCE}/ldas-tools-framecpp-2.6.5.tar.gz"
	CONFIGURE_FLAGS="--disable-warnings-as-errors --without-doxygen"
	build_package
EOF

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="${IGWN_SOURCE}/gwdatafind-1.0.5.tar.gz"
	BUILD_TYPE="pip"
	build_package
EOF

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="${IGWN_SOURCE}/ligo-segments-1.4.0.tar.gz"
	BUILD_TYPE="pip"
	build_package
EOF

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="${IGWN_SOURCE}/python-ligo-lw-1.8.3.tar.gz"
	BUILD_TYPE="pip"
	build_package
EOF

ARG LIGO_GIT=https://git.ligo.org/lscsoft

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="${LIGO_GIT}/lalsuite"
	FETCH_TYPE="git"
	# LIGOLwXMLInspiralRead.h is removed passed this version which we need for gstlal.
	GIT_VERSION="lalsuite-v7.11"
	CFLAGS="-Wno-error ${CFLAGS}"
	CXXFLAGS="-Wno-error ${CXXFLAGS}"
	PRE_CONFIGURE="./00boot"
	CONFIGURE_FLAGS="--enable-swig-python"
	build_package
EOF

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="${LIGO_GIT}/lalsuite-extra"
	# Known working commit as of 23/5/2022
	FETCH_TYPE="git"
	GIT_VERSION="v1.5.0"
	PRE_CONFIGURE="./00boot"
	CONFIGURE_FLAGS="--enable-swig-python"
	build_package
EOF

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	<<EOF
	SOURCE="${LIGO_GIT}/glue"
	FETCH_TYPE="git"
	GIT_VERSION="glue-release-3.0.1"
	BUILD_TYPE="pip"
	build_package
EOF

RUN --mount=type=cache,target=/root/.cache \
	<<EOF
	${PIP3} install ${PIPFLAGS} extension-helpers ligo.skymap
EOF

RUN --mount=type=cache,target="/src" \
	--mount=type=cache,target="/root/ccache" \
	SOURCE="https://dbus.freedesktop.org/releases/dbus-python/dbus-python-1.3.2.tar.gz" BUILD_TYPE="meson" build_package

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	cd /
	p=avahi-0.8
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.gz) || (wget $wget_opts https://github.com/lathiat/avahi/releases/download/v0.8/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz
	cd $p
	./configure --prefix=$PREFIX --disable-gdbm
	make -j
	make install -j || echo 'Ignore failure.'
EOF

ENV CONDABASEPREFIX="${PREFIX}/conda-base"
ENV CONDA="${CONDABASEPREFIX}/bin/conda"
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
	bash "${SOURCE_BASE}" -b -p "${CONDABASEPREFIX}"
	${CONDA} clean -afy
EOF

ENV CONDAPREFIX="${PREFIX}/conda"
# Conda is the only way to install gds
RUN --mount=type=cache,target="/root/.cache" \
	--mount=type=cache,target="/root/ccache" \
	--mount=type=cache,target="${PREFIX}/conda/pkgs",sharing=locked \
	<<EOF
	${CONDA} create -p ${CONDAPREFIX} -y -c conda-forge gds-base gds-framexmit python-gds dtt-awggui libffi
	rm "${CONDAPREFIX}/lib/libtinfo.so.6"
EOF

# Add conda paths to build flags
ENV PKG_CONFIG_PATH="${PKG_CONFIG_PATH}:${CONDAPREFIX}/lib/pkgconfig"
ENV LD_LIBRARY_PATH="${LD_LIBRARY_PATH}:/usr/lib/x86_64-linux-gnu:${CONDAPREFIX}/lib"
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
	# Known working commit as of 28/8/2023
	FETCH_TYPE="git"
	GIT_VERSION="debc0d03"
	BUILD_TYPE="pip"
	PIP_OPTIONAL="[all]"
	CFLAGS="${CFLAGS} -I${PREFIX}/include"
	build_package
EOF

COPY .gitlab-ci/patches/gstlal.patch /.gitlab-ci/patches/gstlal.patch
RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=gstlal
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -d /src/$p && pushd /src/$p && git pull && popd && cp -r /src/$p $p) || (git clone https://git.ligo.org/spiir-group/$p.git && cp -r $p /src/)
	cd $p
	# Known working commit as of 15/3/24
	git checkout 0368d859
	git apply /.gitlab-ci/patches/gstlal.patch
	cd gstlal
	./00init.sh
	./configure --prefix=$PREFIX --enable-introspection=yes
	make -j
	make install -j
	cd ../gstlal-ugly
	./00init.sh
	liblsmp_CFLAGS="-I${CONDAPREFIX}/include" ./configure --prefix=$PREFIX --with-framecpp
	make -j
	make install -j
	cd ../gstlal-burst
	./00init.sh
	./configure --prefix=$PREFIX
	make -j
	make install -j
	cd ../gstlal-inspiral
	./00init.sh
	./configure --prefix=$PREFIX
	make -j
	make install -j
	if [ -z "$DEBUG" ] ; then
		cd ../..
		rm -r $p
	fi
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
	export CFLAGS="${CFLAGS} -Wno-unknown-pragmas -Wno-sign-compare"
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
	${PIP3} list
	printenv
	# Reduce inode usage by deleting test and cache files.
	if [ -z "${DEBUG}" ] ; then
		find "${PREFIX}" | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

COPY .git /repo/.git
COPY .gitlab-ci /repo/.gitlab-ci
WORKDIR /repo

ENTRYPOINT [ "/repo/.gitlab-ci/submit_runs.sh", "-y" ]
