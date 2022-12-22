# syntax = docker/dockerfile:1.4.2
FROM nvidia/cuda:11.2.2-devel-ubuntu20.04 as build
SHELL ["/bin/bash", "-e", "-c"]

# Maintenance note
LABEL name="SPIIR Runtime Image" \
      maintainer="Luke Davis <luke.davis@uwa.edu.au>" \
      date="2022-11-11"

# Make sure apt doesn't clean cache
RUN rm -f /etc/apt/apt.conf.d/docker-clean; echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache

ARG DEBUG

# Cache mounts explained here: https://github.com/moby/buildkit/blob/master/frontend/dockerfile/docs/reference.md#example-cache-apt-packages
# EOF heredocs explained here: https://github.com/moby/buildkit/blob/master/frontend/dockerfile/docs/reference.md#here-documents
# Install required apt dependencies
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked --mount=type=cache,target=/var/lib/apt,sharing=locked \
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
RUN mkdir -p /root/ccache && ccache --set-config=cache_dir=/root/ccache

# Build flags
ENV SYSTEM_PYTHONPATH=${PYTHONPATH:-}
ENV SYSTEM_PATH=${PATH:-}
ENV SYSTEM_PKG_CONFIG_PATH=${PKG_CONFIG_PATH:-}
ENV SYSTEM_LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-}
ENV PREFIX=/usr/spiir
ENV ACLOCAL_PATH=$PREFIX/share/aclocal
ENV COMP_FLAGS=${DEBUG:+"-fPIC -O3"}
ENV COMP_FLAGS=${COMP_FLAGS:-"-fPIC -O3 -DNDEBUG"}
ENV CFLAGS=$COMP_FLAGS
ENV CXXFLAGS=$COMP_FLAGS
ENV CPPFLAGS=$COMP_FLAGS
ENV FFLAGS=$COMP_FLAGS
ENV FCFLAGS=$COMP_FLAGS

ENV PATH=/usr/lib/ccache:$PREFIX/gcc/bin:$PREFIX/Healpix_3.50/src/cxx/optimized_gcc/bin:$PREFIX/bin:$PREFIX/python3/bin:$PATH
ENV LD_LIBRARY_PATH=$PREFIX/Healpix_3.50/lib:$PREFIX/Healpix_3.50/src/cxx/optimized_gcc/lib:$PREFIX/lib:$PREFIX/lib/x86_64-linux-gnu:/usr/local/lib/x86_64-unknown-linux-gnu:$LD_LIBRARY_PATH
ENV LIBRARY_PATH=$LD_LIBRARY_PATH
ENV PKG_CONFIG_PATH=$PREFIX/Healpix_3.50/lib:$PREFIX/lib/pkgconfig:$PREFIX/lib/x86_64-linux-gnu/pkgconfig:/usr/lib/x86_64-linux-gnu/pkgconfig:$PKG_CONFIG_PATH

# Use ccache binaries instead of native gcc/g++/clang-13
RUN /usr/sbin/update-ccache-symlinks

RUN mkdir -p /src

# We download/clone src files and then move them to cache instead of directly into cache in case build process is killed during download.
RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=gcc-10.4.0
	echo -e "\\n\\n>> [`date`] building $p"
	(test -f /src/$p.tar.gz) || (wget $wget_opts https://ftp.gnu.org/gnu/gcc/gcc-10.4.0/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz
	cd $p
	./contrib/download_prerequisites
	CFLAGS="$CFLAGS -Wno-error" CXXFLAGS="$CXXFLAGS -Wno-error" ./configure --prefix=$PREFIX/gcc \
		--disable-multilib \
		--enable-languages=c,c++,fortran
	make -j
	make install -j
	/usr/sbin/update-ccache-symlinks
	# Delete extracted source directory if not a DEBUG build, to save space.
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=hdf5-1.8.22
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.gz) || (wget $wget_opts https://support.hdfgroup.org/ftp/HDF5/releases/hdf5-1.8/$p/src/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz
	cd $p
	./configure --prefix=$PREFIX
	make -j
	make install -j
	mkdir -p "$PREFIX/lib/pkgconfig"
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

# HDF5 does not include it's own pkg-config file, which we need.
COPY <<-EOF "$PREFIX/lib/pkgconfig/hdf5.pc"
prefix=$PREFIX
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

ENV PYTHON2PREFIX ${PREFIX}
RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=Python-2.7.14
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tgz) || (wget $wget_opts https://www.python.org/ftp/python/2.7.14/$p.tgz && mv $p.tgz /src/)
	tar -xzf /src/$p.tgz
	cd $p
	mkdir build
	cd build
	if [ -z $DEBUG ]; then PYTHONFLAGS="--enable-optimizations" ; fi
	../configure --prefix=${PYTHON2PREFIX} --enable-shared ${PYTHONFLAGS}
	make -j EXTRA_CFLAGS=${DEBUG:+"-DLLTRACE -DWITH_PYMALLOC"}
	make install -j
	sed -i '127,185s/###//g' ../Misc/valgrind-python.supp
	cp ../Misc/valgrind-python.supp $PREFIX/
	if [ -z "$DEBUG" ] ; then
		cd ../..
		rm -r $p
		# Delete unused test files and pycache files to reduce inode usage.
		rm -r ${PREFIX}/lib/python2.7/test
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

# Numpy needs to be installed first to be a dependency for the others.
ENV PIPFLAGS=${DEBUG:+"--no-clean"}
ENV PYTHON2 ${PYTHON2PREFIX}/bin/python
ENV PIP2 ${PYTHON2PREFIX}/bin/pip
RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/.cache \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	${PYTHON2} -m ensurepip --upgrade 
	${PIP2} install --upgrade pip setuptools wheel
	${PIP2} install --upgrade ${PIPFLAGS} numpy==1.14.1
	${PIP2} install --upgrade ${PIPFLAGS} \
		astropy==2.0.3 \
		clang-format==14.0.6 \
		cryptography==3.3.2 \
		Cython==0.29.32 \
		h5py==2.7.1 \
		healpy==1.12.4 \
		ligo-segments==1.4.0 \
		matplotlib==2.2.2 \
		pyopenssl==21.0.0 \
		scipy==1.0.0 \
		shapely==1.7.1 \
		yapf==0.32.0
	if [ -z "$DEBUG" ] ; then
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

ENV PYTHON3PREFIX ${PREFIX}/python3
RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=Python-3.8.13
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tgz) || (wget $wget_opts https://www.python.org/ftp/python/3.8.13/$p.tgz && mv $p.tgz /src/)
	tar -xzf /src/$p.tgz
	cd $p
	mkdir build
	cd build
	if [ -z $DEBUG ]; then PYTHONFLAGS="--enable-optimizations" ; fi
	../configure --prefix=${PYTHON3PREFIX} --enable-shared $PYTHONFLAGS
	make -j EXTRA_CFLAGS=${DEBUG:+"-DLLTRACE -DWITH_PYMALLOC"}
	make install -j
	sed -i '127,185s/###//g' ../Misc/valgrind-python.supp
	cp ../Misc/valgrind-python.supp $PYTHON3PREFIX/
	if [ -z "$DEBUG" ] ; then
		cd ../..
		rm -r $p
		rm -r ${PREFIX}/python3/lib/python3.8/test
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

# Python 3 is used to create skymaps and run tests on pipeline output.
ENV PYTHON3 ${PYTHON3PREFIX}/bin/python3
ENV PIP3 ${PYTHON3PREFIX}/bin/pip3
RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/.cache \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	${PYTHON3} -m ensurepip --upgrade
	${PIP3} install --upgrade pip setuptools==65.0.2 wheel
	${PIP3} install --upgrade ${PIPFLAGS} \
		ligo.skymap==1.0.3 \
		meson==0.60.3 \
		ninja==1.10.2.4 \
		pandas==1.5.1
	if [ -z "$DEBUG" ] ; then
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=libxml2-2.9.12
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.gz) || (wget $wget_opts ftp://xmlsoft.org/libxml2/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz
	cd $p
	./configure --prefix=$PREFIX
	make -j
	make install -j
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=fftw-3.3.5
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.gz) || (wget $wget_opts ftp://ftp.fftw.org/pub/fftw/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz
	cd $p
	./configure --prefix=$PREFIX \
		--enable-avx \
		--enable-sse2
	make -j
	make install -j
	./configure --prefix=$PREFIX \
		--enable-avx \
		--enable-float \
		--enable-sse
	make -j
	make install -j
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

ARG IGWN_SOURCE=http://software.igwn.org/lscsoft/source

# Libframe's pkg-config file is not installed automatically
RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=libframe-8.30
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.gz) || (wget $wget_opts $IGWN_SOURCE/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz
	cd $p
	# make sure frame files are opened in binary mode
	sed -i~ 's/\([Oo]pen.*"r\)"/\1b"/;' src/FrameL.c
	./configure --prefix=$PREFIX
	make -j
	make install -j
	mkdir -p "$PREFIX/lib/pkgconfig"
	sed "s%^prefix=.*%prefix=$PREFIX%" src/libframe.pc > $PREFIX/lib/pkgconfig/libframe.pc
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=metaio-8.3.0
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.gz) || (wget $wget_opts $IGWN_SOURCE/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz
	cd $p
	./configure --prefix=$PREFIX
	make -j
	make install -j
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=swig-3.0.12
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.gz) || (wget $wget_opts https://sourceforge.net/projects/swig/files/swig/$p/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz
	cd $p
	./configure --prefix=$PYTHON2PREFIX \
		--with-python \
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
		--without-tcl
	make -j
	make install -j
	cp Examples/test-suite/python/pythonswig.supp $PREFIX/
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=swig-4.0.2
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.gz) || (wget $wget_opts https://sourceforge.net/projects/swig/files/swig/$p/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz
	cd $p
	./configure --prefix=$PYTHON3PREFIX \
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
		--without-pike \
		--without-php \
		--with-python3 \
		--without-python \
		--without-r \
		--without-ruby \
		--without-scilab \
		--without-tcl
	make -j
	make install -j
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=gsl-2.6
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.gz) || (wget $wget_opts ftp://ftp.fu-berlin.de/unix/gnu/gsl/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz
	cd $p
	./configure --prefix=$PREFIX
	make -j
	make install -j
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=gettext-0.20.1
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.gz) || (wget -nc https://ftp.gnu.org/pub/gnu/gettext/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz
	cd $p
	./configure --prefix=$PREFIX
	make -j
	make install -j
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=ldas-tools-al-2.5.7
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.gz) || (wget $wget_opts $IGWN_SOURCE/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz
	cd $p
	./configure --prefix=$PREFIX \
		--disable-warnings-as-errors
	make -j
	make install -j
	cp src/std.supp $PREFIX/
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

COPY .gitlab-ci/patches/framecpp_0000_Makefile_fix.patch /.gitlab-ci/patches/framecpp_0000_Makefile_fix.patch
RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=ldas-tools-framecpp-2.5.8
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.gz) || (wget $wget_opts $IGWN_SOURCE/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz
	cd $p
	./configure --prefix=$PREFIX \
		--disable-warnings-as-errors \
		--without-doxygen
	cd swig/python
	patch /.gitlab-ci/patches/framecpp_0000_Makefile_fix.patch
	cd ../..
	make -j
	make install -j
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=util-linux-2.34
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.xz) || (wget $wget_opts https://mirrors.edge.kernel.org/pub/linux/utils/util-linux/v2.34/$p.tar.xz && mv $p.tar.xz /src/)
	tar -xJf /src/$p.tar.xz
	cd $p
	./configure --prefix=$PREFIX \
		--disable-all-programs \
		--enable-libblkid \
		--enable-libmount \
		--disable-use-tty-group
	make -j
	make install -j
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

ENV MESON_FLAGS=${DEBUG:+" "}
ENV MESON_FLAGS=${MESONFLAGS:-"--buildtype=release"}

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=glib-2.62.3
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.xz) || (wget $wget_opts https://ftp.gnome.org/pub/gnome/sources/glib/2.62/$p.tar.xz && mv $p.tar.xz /src/)
	tar -xJf /src/$p.tar.xz
	cd $p
	meson _build $MESON_FLAGS --prefix=$PREFIX
	ninja -v -C _build
	ninja -C _build install
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=gobject-introspection-1.63.1
	echo -e "\\n\\n>> [`date`] Building  $p"
	(test -f /src/$p.tar.xz) || (wget $wget_opts https://ftp.gnome.org/pub/GNOME/sources/gobject-introspection/1.63/$p.tar.xz && mv $p.tar.xz /src/)
	tar -xJf /src/$p.tar.xz
	cd $p
	meson _build $MESON_FLAGS --prefix=$PREFIX
	ninja -v -C _build
	ninja -C _build install
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=pixman-0.40.0
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.gz) || (wget $wget_opts https://www.cairographics.org/releases/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz
	cd $p
	./configure --prefix=$PREFIX
	make -j
	make install -j
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=libpng
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -d /src/$p && cp -r /src/$p $p) || (git clone https://github.com/glennrp/$p.git && cp -r $p /src/)
	cd $p
	# NOCONFIGURE=1 ./autogen.sh
	# git repo includes configure
	./configure --prefix=$PREFIX
	make -j
	make install -j
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=pygobject-2.28.7
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.xz) || (wget $wget_opts https://ftp.acc.umu.se/pub/GNOME/sources/pygobject/2.28/$p.tar.xz && mv $p.tar.xz /src/)
	tar -xJf /src/$p.tar.xz
	cd $p
	./configure --prefix=$PREFIX
	make -j
	make install -j
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=pygtk-2.24.0
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.bz2) || (wget $wget_opts https://ftp.gnome.org/pub/GNOME/sources/pygtk/2.24/$p.tar.bz2 && mv $p.tar.bz2 /src/)
	tar -xjf /src/$p.tar.bz2
	cd $p
	./configure --prefix=$PREFIX
	make -j
	make install -j
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

# Get valgrind
RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=valgrind-3.20.0
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.bz2) || (wget $wget_opts https://sourceware.org/pub/valgrind/$p.tar.bz2 && mv $p.tar.bz2 /src/)
	tar -xjf /src/$p.tar.bz2
	cd $p
	./configure --prefix=$PREFIX
	make -j
	make install -j
EOF

# Debug build flags
ARG DEBUGMEMORY
ARG DEBUGTHREADS
ARG DEBUGADDRESS
ARG DEBUGUB
ENV SAN=${SAN:-$DEBUGMEMORY}
ENV SAN=${SAN:-$DEBUGTHREADS}
ENV SAN=${SAN:-$DEBUGADDRESS}
ENV SAN=${SAN:-$DEBUGUB}
RUN <<EOF
	if [[ -n "$SAN" && -z "$DEBUG" ]] ; then 
		echo "DEBUG build-arg must be set if any sanitizer build-arg is set (DEBUGMEMORY, DEBUGTHREADS, DEBUGADDRESS, DEBUGUB)"
		exit 0
	fi
EOF
ENV DEBUGFLAGS=${DEBUG:+"-Og -ggdb -fno-omit-frame-pointer -rdynamic"}
ENV GSTDEBUGFLAGS=${DEBUG:+"--enable-debug"}
ENV CFLAGS="$CFLAGS $DEBUGFLAGS"
ENV CXXFLAGS="$CXXFLAGS $DEBUGFLAGS"

ENV GST_PLUGIN_PATH=$PREFIX/lib/gstreamer-0.10

COPY .gitlab-ci/patches/manoj_00_gstreamer.patch /.gitlab-ci/patches/manoj_00_gstreamer.patch
RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=gstreamer
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -d /src/$p && cp -r /src/$p $p) || (git clone https://gitlab.freedesktop.org/gstreamer/$p.git && cp -r $p /src/)
	cd $p
	git checkout 0.10
	git apply /.gitlab-ci/patches/manoj_00_gstreamer.patch
	NOCONFIGURE=1 ./autogen.sh
	./configure --prefix=$PREFIX $GSTDEBUGFLAGS
	make -j
	make install -j
	cp common/gst.supp $PREFIX/
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=gst-plugins-base
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -d /src/$p && cp -r /src/$p $p) || (git clone https://gitlab.freedesktop.org/gstreamer/$p.git && cp -r $p /src/)
	cd $p
	git checkout 0.10
	NOCONFIGURE=1 ./autogen.sh
	./configure --prefix=$PREFIX $GSTDEBUGFLAGS
	make -j
	make install -j
	cp tests/check/gst-plugins-base.supp $PREFIX/
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=gst-plugins-good
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -d /src/$p && cp -r /src/$p $p) || (git clone https://gitlab.freedesktop.org/gstreamer/$p.git && cp -r $p /src/)
	cd $p
	git checkout 0.10
	NOCONFIGURE=1 ./autogen.sh
	./configure --prefix=$PREFIX \
		--disable-gst_v4l2 $GSTDEBUGFLAGS
	make -j
	make install -j
	cp tests/check/gst-plugins-good.supp $PREFIX/
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

# gst-python can't find python libs without specifically adding the link flag
RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=gst-python
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -d /src/$p && cp -r /src/$p $p) || (git clone https://gitlab.freedesktop.org/gstreamer/$p.git && cp -r $p /src/)
	cd $p
	git checkout 0.10
	NOCONFIGURE=1 ./autogen.sh
	CFLAGS="-lpython2.7 -Wno-error $CFLAGS" ./configure --prefix=$PREFIX $GSTDEBUGFLAGS
	make -j
	make install -j
	cp testsuite/gstpython.supp $PREFIX/
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

ARG LIGO_GIT=https://git.ligo.org/lscsoft

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=lalsuite
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -d /src/$p && cp -r /src/$p $p) || (git clone $LIGO_GIT/$p.git && cp -r $p /src/)
	cd $p
	# Known working commit as of 23/5/2022
	git checkout aee3feddee701355506c109029fd1ae574ae56c5
	export CFLAGS="-Wno-error $CFLAGS"
	export CXXFLAGS="-Wno-error $CXXFLAGS"
	./00boot
	./configure --prefix=$PREFIX \
		--enable-swig-python
	make -j
	make install -j
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=lalsuite-extra
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -d /src/$p && cp -r /src/$p $p) || (git clone $LIGO_GIT/$p.git && cp -r $p /src/)
	cd $p
	# Known working commit as of 23/5/2022
	git checkout 9d8b175df5348ee27159b669f9fe34693386c60c
	./00boot
	./configure --prefix=$PREFIX
	make -j
	make install -j
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

COPY .gitlab-ci/patches/glue_0000_zipsafe.patch /.gitlab-ci/patches/glue_0000_zipsafe.patch 
RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=glue
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -d /src/$p && cp -r /src/$p $p) || (git clone $LIGO_GIT/$p.git && cp -r $p /src/)
	cd $p
	git checkout glue-release-1.59.2
	git apply /.gitlab-ci/patches/glue_0000_zipsafe.patch
	${PYTHON2} setup.py install --prefix=$PREFIX
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

ENV CMAKE_FLAGS=${DEBUG:+" "}
ENV CMAKE_FLAGS=${CMAKE_FLAGS:-"-DCMAKE_BUILD_TYPE=Release"}

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=OpenBLAS
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -d /src/$p && cp -r /src/$p $p) || (git clone https://github.com/xianyi/$p.git && cp -r $p /src/)
	cd $p
	git checkout v0.3.21
	mkdir build
	cd build
	cmake $CMAKE_FLAGS -DBUILD_SHARED_LIBS=ON -DDYNAMIC_ARCH=TRUE -DDYNAMIC_OLDER=1 -DCMAKE_INSTALL_PREFIX:PATH=$PREFIX ..
	cmake --build . -j
	cmake --build . --target install -j
	if [ -z "$DEBUG" ] ; then
		cd ../..
		rm -r $p
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=cfitsio3450
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.gz) || (wget $wget_opts --no-check-certificate https://heasarc.gsfc.nasa.gov/FTP/software/fitsio/c/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz
	cd cfitsio
	./configure --prefix=$PREFIX
	make -j shared
	make install -j
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r cfitsio
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=Healpix_3.50_2018Dec10
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.gz) || (wget $wget_opts https://sourceforge.net/projects/healpix/files/Healpix_3.50/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz -C $PREFIX
	cd $PREFIX/Healpix_3.50
	# Purely interactive configure script, doesn't take arguments
	printf "1\n\n\n\ngv\n\n2\n\n\n\n\n\n\n$PREFIX/lib\n\ny\n4\n\n\n4\n0\n" | ./configure
	make -j
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r Healpix_3.50/doc
	fi
EOF
# Can't install into different directory
# cd ..
# rm -r $p
# rm $p.tar.gz

ENV CONDAPREFIX ${PREFIX}/conda
ENV CONDA ${CONDAPREFIX}/bin/conda
RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/.cache \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=Miniconda3-latest-Linux-x86_64
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.sh) || (wget $wget_opts https://repo.continuum.io/miniconda/$p.sh && mv $p.sh /src/)
	cp /src/$p.sh .
	chmod +x $p.sh
	./$p.sh -b -p ${CONDAPREFIX}
	${CONDA} clean -afy
EOF

# Conda is the only way to install gds
RUN --mount=type=cache,target=/root/.cache \
	--mount=type=cache,target=/root/ccache \
	--mount=type=cache,target=${PREFIX}/conda/pkgs,sharing=locked \
	<<EOF
	${CONDA} install -y -c conda-forge gds-base==3.0.0 gds-framexmit python-gds dtt-awggui
	rm ${CONDAPREFIX}/lib/libtinfo.so.6
EOF

# Add conda paths to build flags
ENV PKG_CONFIG_PATH=$PREFIX/Healpix_3.50/lib:$PREFIX/lib/pkgconfig/:$PREFIX/lib/x86_64-linux-gnu/pkgconfig:$CONDAPREFIX/lib/pkgconfig:/usr/lib/x86_64-linux-gnu/pkgconfig
ENV LD_LIBRARY_PATH=$PREFIX/Healpix_3.50/lib:$PREFIX/Healpix_3.50/src/cxx/optimized_gcc/lib:$PREFIX/lib:$PREFIX/lib/x86_64-linux-gnu:$CONDAPREFIX/lib:/usr/local/lib/x86_64-unknown-linux-gnu:/usr/local/cuda/lib64:${LD_LIBRARY_PATH:-}
ENV LIBRARY_PATH=$LD_LIBRARY_PATH

# Install Clang if sanitizers are to be used.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked --mount=type=cache,target=/var/lib/apt,sharing=locked \
	--mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	if [ -n "$SAN" ] ; then
		apt-get update
		apt-get install -y lsb-release wget software-properties-common gnupg
		(test -f /src/llvm.sh) || (wget https://apt.llvm.org/llvm.sh && mv llvm.sh /src/)
		chmod +x /src/llvm.sh
		/src/llvm.sh 11
		ln -s `which llvm-symbolizer-11` /usr/bin/llvm-symbolizer
		apt-get -y autoremove
	fi
EOF

ENV CC=${SAN:+"clang-11"}
ENV CXX=${SAN:+"clang++-11"}
ENV CC=${CC:-"gcc"}
ENV CXX=${CXX:-"g++"}

# Sanitizer build flags
ENV DEBUGADDRESSFLAGS=${DEBUGADDRESS:+"-fsanitize=address -fsanitize-recover=all -shared-libsan -fsanitize-address-use-after-scope -fsanitize=pointer-compare -fsanitize=pointer-subtract"}
ENV DEBUGMEMORYFLAGS=${DEBUGMEMORY:+"-fsanitize=memory -fsanitize-recover=all -mllvm -msan-keep-going=1 -shared-libsan -fPIE -pie -fno-optimize-sibling-calls -fsanitize-memory-track-origins "}
ENV DEBUGTHREADFLAGS=${DEBUGTHREADS:+"-fsanitize=thread -fsanitize-recover=all -shared-libsan -fPIE -pie"}
ENV DEBUGUBFLAGS=${DEBUGUB:+"-fsanitize=undefined -fsanitize-recover=all -shared-libsan -fsanitize=integer -fsanitize=float-divide-by-zero -fsanitize=implicit-conversion -fsanitize=nullability -fsanitize=local-bounds"}
ENV ASAN_OPTIONS=${DEBUGADDRESS:+"protect_shadow_gap=0:detect_leaks=1:fast_unwind_on_malloc=0:detect_invalid_pointer_pairs=2:detect_stack_use_after_return=1:halt_on_error=0"}
ENV MSAN_OPTIONS=${DEBUGMEMORY:+"halt_on_error=0"}
ENV TSAN_OPTIONS=${DEBUGTHREADS:+"history_size=4 force_seq_cst_atomics=1 halt_on_error=0"}
ENV UBSAN_OPTIONS=${DEBUGUB:+"print_stacktrace=1"}
ENV LSAN_OPTIONS=${SAN:+"verbosity=1:log_threads=1"}
ENV SAN_LD_LIBRARY_PATH=${SAN:+"/usr/lib/llvm-11/lib/clang/11.1.0/lib/linux"}
ENV LD_LIBRARY_PATH=$LD_LIBRARY_PATH:$SAN_LD_LIBRARY_PATH
ENV LDFLAGS="$LDFLAGS $DEBUGMEMORYFLAGS $DEBUGTHREADFLAGS $DEBUGUBFLAGS $DEBUGADDRESSFLAGS"
ENV CFLAGS="$CFLAGS $DEBUGMEMORYFLAGS $DEBUGTHREADFLAGS $DEBUGUBFLAGS $DEBUGADDRESSFLAGS"
ENV CXXFLAGS="$CXXFLAGS $DEBUGMEMORYFLAGS $DEBUGTHREADFLAGS $DEBUGUBFLAGS $DEBUGADDRESSFLAGS"

# Python tests for pipeline results
RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/.cache \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	mkdir tanghyd
	cd tanghyd
	p=spiir-python-tests
	(test -d /src/$p && pushd /src/$p && git pull && popd && cp -r /src/$p $p) || (git clone https://github.com/tanghyd/$p.git && cp -r $p /src/)
	p=spiir
	(test -d /src/tanghyd-$p && pushd /src/tanghyd-$p && git pull && popd && cp -r /src/tanghyd-$p $p) || (git clone https://github.com/tanghyd/$p.git && cp -r $p /src/tanghyd-$p)
	cd $p
	${PIP3} install ${PIPFLAGS} .[pycbc]
	if [ -z "$DEBUG" ] ; then
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

COPY gstlal /spiir/gstlal
COPY .gitlab-ci/patches/gstlal_0001patrick_fix_includes_revised.patch /.gitlab-ci/patches/gstlal_0001patrick_fix_includes_revised.patch
RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	cd /spiir
	git apply /.gitlab-ci/patches/gstlal_0001patrick_fix_includes_revised.patch
	cd /spiir/gstlal
	make distclean || true
	yes | head -n1 | ./00init.sh
	XDG_DATA_DIRS=$PREFIX/share:${XDG_DATA_DIRS:-} ./configure --prefix=$PREFIX
	XDG_DATA_DIRS=$PREFIX/share:${XDG_DATA_DIRS:-} make -j
	XDG_DATA_DIRS=$PREFIX/share:${XDG_DATA_DIRS:-} make install -j
EOF

COPY gstlal-inspiral /spiir/gstlal-inspiral
RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	cd /spiir/gstlal-inspiral
	make distclean || true
	yes | head -n1 | ./00init.sh
	./configure --prefix=$PREFIX
	make -j
	make install -j
EOF

COPY gstlal-ugly /spiir/gstlal-ugly
RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	cd /spiir/gstlal-ugly
	make distclean || true
	yes | head -n1 | ./00init.sh
	./configure --prefix=$PREFIX
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

# Spiir build debug flags
ENV NVCCFLAGS=${SAN:+"-ccbin clang-11"}
ENV NVCC_APPEND_FLAGS=${DEBUG:+"-g -G $NVCCFLAGS"}
ENV DEBUGFLAGS2=${DEBUG:+"-fdebug-prefix-map=..=/spiir"}
ENV LDFLAGS="$LDFLAGS $DEBUGFLAGS2"
ENV CFLAGS="$CFLAGS $DEBUGFLAGS2"
ENV CXXFLAGS="$CXXFLAGS $DEBUGFLAGS2"

FROM build AS runtime

# If PATCH_FINALSINK=1, patch postcoh_finalsink.py to skip far validation and output coinc.xml's on small runs.
ARG PATCH_FINALSINK

COPY gstlal-spiir /spiir/gstlal-spiir
COPY .gitlab-ci/patches/force_early_uploads.patch /.gitlab-ci/patches/force_early_uploads.patch
RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	cd /spiir
	if [ -n "$PATCH_FINALSINK" ] ; then git apply /.gitlab-ci/patches/force_early_uploads.patch; fi
	cd /spiir/gstlal-spiir
	make distclean || true
	yes | head -n1 | ./00init.sh
	export CFLAGS="$CFLAGS -Wno-cast-function-type -Wno-unused-command-line-argument -Wno-unknown-warning-option -Wno-unused-function"
	for FLAG in $CFLAGS; do NVCC_APPEND_FLAGS="$NVCC_APPEND_FLAGS -Xcompiler $FLAG"; done;
	./configure --prefix=$PREFIX --with-cuda=/usr/local/cuda
	make -j
	make install -j
	if [ -z "$DEBUG" ] ; then
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

RUN <<EOF
	apt list --installed
	$CONDA list
	$PIP2 list
	$PIP3 list
	printenv
EOF

# Runtime flags
# Deterministic whitening
ENV GSTLAL_FIR_WHITEN=1
ENV G_SLICE=${DEBUG:+"always-malloc"}
ENV G_DEBUG=${DEBUG:+"gc-friendly"}
ENV GST_DEBUG=${DEBUG:+"cohfar_accumbackground:6,cuda_postcoh:6,cohfar_assignfar:6,cuda_multiratespiir:6,postcoh_filesink:6"}
ENV GST_DEBUG_NO_COLOR=${DEBUG:+"1"}

COPY .git /spiir/.git
COPY .gitlab-ci /.gitlab-ci
WORKDIR /spiir

ENTRYPOINT [ "/.gitlab-ci/submit_runs.sh", "-y" ]
