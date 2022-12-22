# syntax = docker/dockerfile:1.4.2
FROM nvidia/cuda:11.8.0-devel-ubuntu22.04 as build
SHELL ["/bin/bash", "-e", "-c"]

# Maintenance note
LABEL name="SPIIR Python 3 Runtime Image" \
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
		ccache \
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
ENV PYTHONPATH=$PREFIX/lib/python3.8/site-packages
ENV PATH=/usr/lib/ccache:$PREFIX/bin:/usr/local/cuda/bin:$PATH
ENV PKG_CONFIG_PATH=$PREFIX/lib/pkgconfig/:$PREFIX/lib/x86_64-linux-gnu/pkgconfig/:/usr/lib/x86_64-linux-gnu/pkgconfig/:${PKG_CONFIG_PATH:-}
ENV LD_LIBRARY_PATH=$PREFIX/lib:$PREFIX/lib/x86_64-linux-gnu:/usr/local/cuda/lib64:/usr/local/lib/x86_64-unknown-linux-gnu:${LD_LIBRARY_PATH:-}
ENV LIBRARY_PATH=$LD_LIBRARY_PATH
ENV XDG_DATA_DIRS=$PREFIX/share:/usr/share
ENV GST_PLUGIN_PATH=$PREFIX/lib/x86_64-linux-gnu/gstreamer-1.0:$PREFIX/lib/gstreamer-1.0
ENV GI_TYPELIB_PATH=$PREFIX/lib/girepository-1.0:$PREFIX/lib/x86_64-linux-gnu/girepository-1.0

# Use ccache binaries instead of native gcc/g++/clang-13
RUN /usr/sbin/update-ccache-symlinks

RUN mkdir -p /src

# We download/clone src files and then move them to cache instead of directly into cache in case build process is killed during download.
RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=gcc-12.2.0
	echo -e "\\n\\n>> [`date`] building $p"
	(test -f /src/$p.tar.gz) || (wget $wget_opts https://ftp.gnu.org/gnu/gcc/gcc-12.2.0/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz
	cd $p
	./contrib/download_prerequisites
	CFLAGS="$CFLAGS -Wno-error" CXXFLAGS="$CXXFLAGS -Wno-error" ./configure --prefix=$PREFIX/gcc \
		--disable-multilib \
		--enable-languages=c,c++,fortran
	make -j
	make install -j
	/usr/sbin/update-ccache-symlinks
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
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
ENV SAN=${DEBUGMEMORY:-$DEBUGTHREADS}
ENV SAN=${SAN:-$DEBUGADDRESS}
ENV SAN=${SAN:-$DEBUGUB}
RUN <<EOF
	if [[ -n "$SAN" && -z "$DEBUG" ]] ; then 
		echo "DEBUG build-arg must be set if any sanitizer build-arg is set (DEBUGMEMORY, DEBUGTHREADS, DEBUGADDRESS, DEBUGUB)"
		exit 0
	fi
EOF
ENV DEBUGFLAGS=${DEBUG:+"-Og -ggdb -fno-omit-frame-pointer -rdynamic"}
ENV LDFLAGS="$LDFLAGS $DEBUGFLAGS"
ENV CFLAGS="$CFLAGS $DEBUGFLAGS"
ENV CXXFLAGS="$CXXFLAGS $DEBUGFLAGS"

ENV PYTHON3PREFIX ${PREFIX}
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
		rm -r ${PREFIX}/lib/python3.8/test
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

ENV PIPFLAGS=${DEBUG:+"--no-clean"}
ENV PYTHON3 ${PYTHON3PREFIX}/bin/python3
ENV PIP3 ${PYTHON3PREFIX}/bin/pip3
RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/.cache \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	ln -s $PREFIX/bin/python3 $PREFIX/bin/python
	${PYTHON3} -m ensurepip --upgrade
	${PIP3} install --upgrade ${PIPFLAGS} pip setuptools wheel
	${PIP3} install --upgrade ${PIPFLAGS} numpy==1.23.4
	if [ -z "$DEBUG" ] ; then
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

ENV MESON_FLAGS=${DEBUG:+" "}
ENV MESON_FLAGS=${MESONFLAGS:-"--buildtype=release"}

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=glib-2.73.3
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.xz) || (wget $wget_opts https://download.gnome.org/sources/glib/2.73/$p.tar.xz && mv $p.tar.xz /src/)
	echo "df1a2b841667d6b48b2ef6969ebda4328243829f6e45866726f806f90f64eead /src/glib-2.73.3.tar.xz" | sha256sum -c -
	tar -xJf /src/$p.tar.xz
	cd $p
	meson setup --prefix=$PREFIX ${MESON_FLAGS} _build
	meson compile -C _build
	meson install -C _build
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=cairo-1.17.6
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.gz) || (wget $wget_opts https://gitlab.freedesktop.org/cairo/cairo/-/archive/1.17.6/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz
	cd $p
	meson setup --prefix=$PREFIX ${MESON_FLAGS} _build
	meson compile -C _build
	meson install -C _build
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=gobject-introspection-1.73.1
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.xz) || (wget $wget_opts https://download.gnome.org/sources/gobject-introspection/1.73/$p.tar.xz && mv $p.tar.xz /src/)
	echo "64d4d6b9abaa6ff5450d082592f332b24fc81d1172ccc30d12620fadc4e86bbe /src/gobject-introspection-1.73.1.tar.xz" | sha256sum -c -
	tar -xJf /src/$p.tar.xz
	cd $p
	meson setup --prefix=$PREFIX ${MESON_FLAGS} _build
	meson compile -C _build
	meson install -C _build
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=pygobject-3.42.2
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.xz) || (wget $wget_opts https://download.gnome.org/sources/pygobject/3.42/pygobject-3.42.2.tar.xz && mv $p.tar.xz /src/)
	echo "ade8695e2a7073849dd0316d31d8728e15e1e0bc71d9ff6d1c09e86be52bc957 /src/pygobject-3.42.2.tar.xz" | sha256sum -c -
	tar -xJf /src/$p.tar.xz
	cd $p
	meson setup --prefix=$PREFIX ${MESON_FLAGS} _build
	meson compile -C _build
	meson install -C _build
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=gstreamer
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -d /src/$p && cp -r /src/$p $p) || (git clone https://gitlab.freedesktop.org/gstreamer/$p.git && cp -r $p /src/)
	cd $p
	git checkout 1.20.3
	meson setup --prefix=$PREFIX ${MESON_FLAGS} _build
	meson compile -C _build
	meson install -C _build
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=gtk-4.8.1
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.xz) || (wget $wget_opts https://download.gnome.org/sources/gtk/4.8/$p.tar.xz && mv $p.tar.xz /src/)
	echo "5ce8d8de98a23bd0c8eca1a61094e1c009b5f009dcbd60b45e990a8db1b742fd /src/gtk-4.8.1.tar.xz" | sha256sum -c -
	tar -xJf /src/$p.tar.xz
	cd $p
	meson setup --prefix=$PREFIX ${MESON_FLAGS} _build
	meson compile -C _build
	meson install -C _build
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
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
	cp Examples/test-suite/python/pythonswig.supp $PREFIX/
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

ARG IGWN_SOURCE=http://software.igwn.org/lscsoft/source

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=metaio-8.5.1
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
	p=ldas-tools-al-2.6.2
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

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=ldas-tools-framecpp-2.6.5
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.gz) || (wget $wget_opts $IGWN_SOURCE/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz
	cd $p
	./configure --prefix=$PREFIX \
		--disable-warnings-as-errors
	make -j
	make install -j
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
	fi
EOF

RUN --mount=type=cache,target=/root/.cache \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	$PIP3 install --upgrade ${PIPFLAGS} \
		astropy==5.1.1 \
		clang-format==15.0.4 \
		cryptography==38.0.1 \
		Cython==0.29.32 \
		h5py==3.7.0 \
		healpy==1.16.1 \
		matplotlib==3.6.2 \
		meson==0.64.0 \
		ninja==1.11.1 \
		pandas==1.5.1 \
		pyopenssl==22.0.0 \
		scipy==1.9.3 \
		shapely==1.8.5.post1 \
		yapf==0.32.0
	if [ -z "$DEBUG" ] ; then
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/.cache \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=gwdatafind-1.0.5
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.gz) || (wget $wget_opts $IGWN_SOURCE/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz
	cd $p
	${PYTHON3} setup.py install --prefix=$PREFIX
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/.cache \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=ligo-segments-1.4.0
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.gz) || (wget $wget_opts $IGWN_SOURCE/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz
	cd $p
	${PYTHON3} setup.py install --prefix=$PREFIX
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
	--mount=type=cache,target=/root/.cache \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=python-ligo-lw-1.8.3
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.gz) || (wget $wget_opts $IGWN_SOURCE/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz
	cd $p
	${PYTHON3} setup.py install --prefix=$PREFIX
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/.cache \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=glue
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -d /src/$p && cp -r /src/$p $p) || (git clone $LIGO_GIT/$p.git && cp -r $p /src/)
	cd $p
	${PYTHON3} setup.py install --prefix=$PREFIX
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

RUN --mount=type=cache,target=/root/.cache \
	<<EOF
	$PIP3 install ${PIPFLAGS} extension-helpers==1.0.0
EOF

RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/.cache \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=ligo.skymap
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -d /src/$p && cp -r /src/$p $p) || (git clone $LIGO_GIT/$p.git && cp -r $p /src/)
	cd $p
	${PYTHON3} setup.py install --prefix=$PREFIX
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF


RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=dbus-python-1.3.2
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -f /src/$p.tar.gz) || (wget $wget_opts https://dbus.freedesktop.org/releases/dbus-python/$p.tar.gz && mv $p.tar.gz /src/)
	tar -xzf /src/$p.tar.gz
	cd $p
	meson setup --prefix=$PREFIX ${MESON_FLAGS} _build
	meson compile -C _build
	meson install -C _build
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

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
	if [ -z "$DEBUG" ] ; then
		cd ..
		rm -r $p
		find ${PREFIX} | grep -E "(/__pycache__$|\.pyc$|\.pyo$)" | xargs rm -rf
	fi
EOF

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
ENV PKG_CONFIG_PATH=$PKG_CONFIG_PATH:$CONDAPREFIX/lib/pkgconfig
ENV LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/usr/lib/x86_64-linux-gnu:$CONDAPREFIX/lib
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
ENV LD_LIBRARY_PATH="$LD_LIBRARY_PATH:$SAN_LD_LIBRARY_PATH:"
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


RUN --mount=type=cache,target=/src \
	--mount=type=cache,target=/root/ccache \
	<<EOF
	p=gstlal
	echo -e "\\n\\n>> [`date`] Building $p"
	(test -d /src/$p && pushd /src/$p && git pull && popd && cp -r /src/$p $p) || (git clone https://git.ligo.org/spiir-group/$p.git && cp -r $p /src/)
	cd $p
	# Known working commit as of 14/11/22
	git checkout 07979eb6a9de749188639ad8e753285766c9b3b9
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
		cd ..
		rm -r $p
	fi
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
	export CFLAGS="$CFLAGS -Wno-unknown-pragmas -Wno-sign-compare"
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
