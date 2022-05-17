# syntax = docker/dockerfile:1.4.2
FROM nvidia/cuda:10.0-cudnn7-devel-ubuntu18.04

RUN rm -f /etc/apt/apt.conf.d/docker-clean; echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' > /etc/apt/apt.conf.d/keep-cache
RUN --mount=type=cache,target=/var/cache/apt --mount=type=cache,target=/var/lib/apt \
	<<EOF
	apt-get update || \
	DEBIAN_FRONTEND="noninteractive" apt-get install -y --no-install-recommends tzdata
	apt-get install -y --no-install-recommends \
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
		libcurl4-openssl-dev \
		libffi-dev \
		libfreetype6-dev \
		liblapack-dev \
		libopenmpi-dev \
		libpcre3-dev \
		libscalapack-openmpi-dev \
		libssl-dev \
		patch \
		perlbrew \
		software-properties-common \
		sqlite3 \
		texinfo \
		vim \
		wget \
		xorg-dev \
		zlib1g-dev
	apt-get -y remove \
		autoconf \
		automake \
		libglib2.0 \
		libtool
	apt-get -y autoremove
	rm -rf /var/lib/apt/lists/*
EOF

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

RUN <<EOF
	perlbrew init
	perlbrew install -j 24 \
		--64all \
		--64int \
		--ld 5.16.3 \
		--multi \
		--notest \
		--thread
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=autoconf-2.69
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts http://ftp.gnu.org/gnu/autoconf/$p.tar.gz
	tar -xzf $p.tar.gz
	cd $p
	./configure --prefix=$PREFIX
	make -j
	make install
	cd ..
	rm -r $p
	rm $p.tar.gz
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=automake-1.13.4
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts https://ftp.gnu.org/gnu/automake/$p.tar.gz
	tar -xzf $p.tar.gz
	cd $p
	./configure --prefix=$PREFIX
	make -j
	make install
	cd ..
	rm -r $p
	rm $p.tar.gz
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=libtool-2.4.2
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts https://ftpmirror.gnu.org/libtool/libtool-2.4.2.tar.gz
	tar -xzf $p.tar.gz
	cd $p
	./configure --prefix=$PREFIX
	make -j
	make install
	cd ..
	rm -r $p
	rm $p.tar.gz
EOF

RUN <<EOF
	wget -O ~/miniconda.sh https://repo.continuum.io/miniconda/Miniconda3-latest-Linux-x86_64.sh
	chmod +x ~/miniconda.sh
	~/miniconda.sh -b -p /root/.conda
	rm ~/miniconda.sh
EOF

ENV CONDA /root/.conda/bin/conda
ENV PYTHON2PREFIX ${PREFIX}
ENV PYTHON2 ${PYTHON2PREFIX}/bin/python
ENV PIP2 ${PYTHON2PREFIX}/bin/pip
RUN --mount=type=cache,target=/root/.cache \
	--mount=type=cache,target=/root/.conda/pkgs \
	<<EOF
	${CONDA} create -p ${PYTHON2PREFIX} -y python=2.7.14
	${PIP2} config set global.cache-dir false
	${PIP2} install --upgrade pip setuptools
	${PIP2} install \
		astropy==2.0.3 \
		clang-format \
		cryptography \
		Cython \
		h5py==2.7.1 \
		healpy==1.12.4 \
		ligo-segments \
		matplotlib==2.2.2 \
		numpy==1.14.1 \
		pyopenssl \
		scipy==1.0.0 \
		shapely \
		yapf
	${CONDA} clean -a
EOF

ENV PYTHON3PREFIX ${PREFIX}/python3
ENV PYTHON3 ${PYTHON3PREFIX}/bin/python
ENV PIP3 ${PYTHON3PREFIX}/bin/pip
RUN --mount=type=cache,target=/root/.cache \
	--mount=type=cache,target=/root/.conda/pkgs \
	<<EOF
	${CONDA} create -p ${PYTHON3PREFIX} -y python=3.7.4
	${PIP3} config set global.cache-dir false
	${PIP3} install --upgrade pip setuptools
	${PIP3} install --prefix=$PREFIX/python3_stuff \
		meson==0.60.3 \
		ninja
	${CONDA} clean -a
EOF

ENV PATH=$PATH:$PREFIX/python3_stuff/bin:$PREFIX/python3/bin
RUN printenv

RUN <<EOF
	p=pkg-config-0.27.1
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts https://pkgconfig.freedesktop.org/releases/$p.tar.gz
	tar -xzf $p.tar.gz
	cd $p
	./configure --prefix=$PREFIX \
		--with-internal-glib
	make -j
	make install
	cd ..
	rm -r $p
	rm $p.tar.gz
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=libxml2-2.9.12
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts ftp://xmlsoft.org/libxml2/$p.tar.gz
	tar -xzf $p.tar.gz
	cd $p
	./configure --prefix=$PREFIX
	make -j
	make install
	cd ..
	rm -r $p
	rm $p.tar.gz
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=fftw-3.3.5
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts ftp://ftp.fftw.org/pub/fftw/$p.tar.gz
	tar -xzf $p.tar.gz
	cd $p
	./configure --prefix=$PREFIX \
		--enable-avx \
		--enable-sse2
	make -j
	make install
	./configure --prefix=$PREFIX \
		--enable-avx \
		--enable-float \
		--enable-sse
	make -j
	make install
	cd ..
	rm -r $p
	rm $p.tar.gz
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=hdf5-1.8.13
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts https://support.hdfgroup.org/ftp/HDF5/releases/hdf5-1.8/$p/src/$p.tar.gz
	tar -xzf $p.tar.gz
	cd $p
	./configure --prefix=$PREFIX
	make -j
	make install
	mkdir -p "$PREFIX/lib/pkgconfig"
	cd ..
	rm -r $p
	rm $p.tar.gz
EOF

COPY <<-EOF "$PREFIX/lib/pkgconfig/hdf5.pc"
	prefix=$PREFIX
	exec_prefix=\${prefix}
	includedir=\${prefix}/include
	libdir=\${exec_prefix}/lib
	Name: hdf5
	Description: HDF5
	Version: 1.8.12
	Requires.private: zlib
	Cflags: -I\${includedir}
	Libs: -L\${libdir} -lhdf5
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=libframe-8.30
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts http://software.ligo.org/lscsoft/source/$p.tar.gz
	tar -xzf $p.tar.gz
	cd $p
	# make sure frame files are opened in binary mode
	sed -i~ 's/\([Oo]pen.*"r\)"/\1b"/;' src/FrameL.c
	./configure --prefix=$PREFIX
	make -j
	make install
	mkdir -p "$PREFIX/lib/pkgconfig"
	sed "s%^prefix=.*%prefix=$PREFIX%" src/libframe.pc > $PREFIX/lib/pkgconfig/libframe.pc
	cd ..
	rm -r $p
	rm $p.tar.gz
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=metaio-8.3.0
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts http://software.ligo.org/lscsoft/source/$p.tar.gz
	tar -xzf $p.tar.gz
	cd $p
	./configure --prefix=$PREFIX
	make -j
	make install
	cd ..
	rm -r $p
	rm $p.tar.gz
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=swig-3.0.12
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts https://sourceforge.net/projects/swig/files/swig/$p/$p.tar.gz
	tar -xzf $p.tar.gz
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
	make install
	cd ..
	rm -r $p
	rm $p.tar.gz
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=swig-4.0.2
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts https://sourceforge.net/projects/swig/files/swig/$p/$p.tar.gz
	tar -xzf $p.tar.gz
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
	make install
	cd ..
	rm -r $p
	rm $p.tar.gz
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=gsl-2.6
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts ftp://ftp.fu-berlin.de/unix/gnu/gsl/$p.tar.gz
	tar -xzf $p.tar.gz
	cd $p
	./configure --prefix=$PREFIX
	make -j
	make install
	cd ..
	rm -r $p
	rm $p.tar.gz
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=gettext-0.20.1
	echo -e "\\n\\n>> [`date`] building $p"
	wget -nc https://ftp.gnu.org/pub/gnu/gettext/$p.tar.gz
	tar -xzf $p.tar.gz
	cd $p
	./configure --prefix=$PREFIX
	make -j
	make install
	cd ..
	rm -r $p
	rm $p.tar.gz
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=ldas-tools-al-2.5.7
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts http://software.igwn.org/lscsoft/source/$p.tar.gz
	tar -xzf $p.tar.gz
	cd $p
	./configure --prefix=$PREFIX \
		--disable-warnings-as-errors
	make -j
	make install
	cd ..
	rm -r $p
	rm $p.tar.gz
EOF

COPY framecpp_0000_Makefile_fix.patch .
RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=ldas-tools-framecpp-2.5.8
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts http://software.igwn.org/lscsoft/source/$p.tar.gz
	tar -xzf $p.tar.gz
	cd $p
	./configure --prefix=$PREFIX \
		--disable-warnings-as-errors
	cd swig/python
	patch framecpp_0000_Makefile_fix.patch
	cd ../..
	make -j
	make install
	cd ..
	rm -r $p
	rm $p.tar.gz
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=util-linux-2.34
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts https://mirrors.edge.kernel.org/pub/linux/utils/util-linux/v2.34/$p.tar.xz
	tar -xJf $p.tar.xz
	cd $p
	./configure --prefix=$PREFIX \
		--disable-all-programs \
		--enable-libblkid \
		--enable-libmount \
		--disable-use-tty-group
	make -j
	make install
	cd ..
	rm -r $p
	rm $p.tar.xz
EOF

# RUN --mount=type=cache,target=/root/ccache \
RUN <<EOF
	p=glib-2.62.3
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts https://ftp.gnome.org/pub/gnome/sources/glib/2.62/$p.tar.xz
	tar -xJf $p.tar.xz
	cd $p
	PATH=$PREFIX/python3_stuff/bin:$PREFIX/python3/bin:$PATH PYTHONPATH=$PREFIX/python3_stuff/lib/python3.7/site-packages $PREFIX/python3_stuff/bin/meson _build --prefix=$PREFIX
	PATH=$PREFIX/python3_stuff/bin:$PREFIX/python3/bin:$PATH PYTHONPATH=$PREFIX/python3_stuff/lib/python3.7/site-packages $PREFIX/python3_stuff/bin/ninja -v -C _build
	PATH=$PREFIX/python3_stuff/bin:$PREFIX/python3/bin:$PATH PYTHONPATH=$PREFIX/python3_stuff/lib/python3.7/site-packages $PREFIX/python3_stuff/bin/ninja -C _build install
	cd ..
	rm -r $p
	rm $p.tar.xz
EOF

# RUN --mount=type=cache,target=/root/ccache \
# ccache breaks the build for some reason which I thought wasn't possible with ccache, might have to remove it for the rest of the packages.
RUN <<EOF
	p=gobject-introspection-1.63.1
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts https://ftp.gnome.org/pub/GNOME/sources/gobject-introspection/1.63/$p.tar.xz
	tar -xJf $p.tar.xz
	cd $p
	PATH=$PREFIX/python3_stuff/bin:$PREFIX/python3/bin:$PATH PYTHONPATH=$PREFIX/python3_stuff/lib/python3.7/site-packages $PREFIX/python3_stuff/bin/meson _build --prefix=$PREFIX
	PATH=$PREFIX/python3_stuff/bin:$PREFIX/python3/bin:$PATH PYTHONPATH=$PREFIX/python3_stuff/lib/python3.7/site-packages $PREFIX/python3_stuff/bin/ninja -v -C _build
	PATH=$PREFIX/python3_stuff/bin:$PREFIX/python3/bin:$PATH PYTHONPATH=$PREFIX/python3_stuff/lib/python3.7/site-packages $PREFIX/python3_stuff/bin/ninja -C _build install
	cd ..
	rm -r $p
	rm $p.tar.xz
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=pixman-0.38.4
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts https://www.cairographics.org/releases/$p.tar.gz
	tar -xzf $p.tar.gz
	cd $p
	./configure --prefix=$PREFIX
	make -j
	make install
	cd ..
	rm -r $p
	rm $p.tar.gz
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=libpng
	echo -e "\\n\\n>> [`date`] Cloning $p"
	git clone https://github.com/glennrp/$p.git
	cd $p
	# NOCONFIGURE=1 ./autogen.sh
	# git repo includes configure
	./configure --prefix=$PREFIX
	make -j
	make install
	cd ..
	rm -r $p
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=cairo-1.16.0
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts https://www.cairographics.org/releases/$p.tar.xz
	tar -xJf $p.tar.xz
	cd $p
	./configure --prefix=$PREFIX
	make -j
	make install
	cd ..
	rm -r $p
	rm $p.tar.xz
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=pygobject-2.28.7
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts https://ftp.acc.umu.se/pub/GNOME/sources/pygobject/2.28/$p.tar.xz
	tar -xJf $p.tar.xz
	cd $p
	./configure --prefix=$PREFIX
	make -j
	make install
	cd ..
	rm -r $p
	rm $p.tar.xz
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=pygtk-2.24.0
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts https://ftp.gnome.org/pub/GNOME/sources/pygtk/2.24/$p.tar.bz2
	tar -xjf $p.tar.bz2
	cd $p
	./configure --prefix=$PREFIX
	make -j
	make install
	cd ..
	rm -r $p
	rm $p.tar.bz2
EOF

COPY manoj_00_gstreamer.patch .
RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=gstreamer
	echo -e "\\n\\n>> [`date`] Cloning $p"
	git clone https://gitlab.freedesktop.org/gstreamer/$p.git
	cd $p
	git checkout 0.10
	git apply ../manoj_00_gstreamer.patch
	NOCONFIGURE=1 ./autogen.sh
	./configure --prefix=$PREFIX
	make -j
	make install
	cd ..
	rm -r $p
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=gst-plugins-base
	echo -e "\\n\\n>> [`date`] Cloning $p"
	git clone https://gitlab.freedesktop.org/gstreamer/$p.git
	cd $p
	git checkout 0.10
	NOCONFIGURE=1 ./autogen.sh
	./configure --prefix=$PREFIX
	make -j
	make install
	cd ..
	rm -r $p
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=gst-plugins-good
	echo -e "\\n\\n>> [`date`] Cloning $p"
	git clone https://gitlab.freedesktop.org/gstreamer/$p.git
	cd $p
	git checkout 0.10
	NOCONFIGURE=1 ./autogen.sh
	./configure --prefix=$PREFIX \
		--disable-gst_v4l2
	make -j
	make install
	cd ..
	rm -r $p
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=gst-python
	echo -e "\\n\\n>> [`date`] Cloning $p"
	git clone https://gitlab.freedesktop.org/gstreamer/$p.git
	cd $p
	git checkout 0.10
	NOCONFIGURE=1 ./autogen.sh
	CFLAGS="-L$PREFIX/lib -Wno-error $CFLAGS" ./configure --prefix=$PREFIX
	# can't find python libs without specifically adding the link flag
	make -j
	make install
	cd ..
	rm -r $p
EOF

COPY lalsuite_0000_cleanup.patch .
COPY lalsuite_0001_variable_epsilon.patch .
RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=lalsuite
	echo -e "\\n\\n>> [`date`] Cloning $p"
	git clone https://git.ligo.org/lscsoft/$p.git
	cd $p
	git checkout aee3feddee701355506c109029fd1ae574ae56c5
	git apply ../lalsuite_0000_cleanup.patch
	git apply ../lalsuite_0001_variable_epsilon.patch
	./00boot
	./configure --prefix=$PREFIX \
		--enable-swig-python
	make -j
	make install
	cd ..
	rm -r $p
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=lalsuite-extra
	echo -e "\\n\\n>> [`date`] Cloning $p"
	git clone https://git.ligo.org/lscsoft/$p.git
	cd $p
	git checkout 9d8b175df5348ee27159b669f9fe34693386c60c
	./00boot
	./configure --prefix=$PREFIX
	make -j
	make install
	cd ..
	rm -r $p
EOF

COPY glue_0000_zipsafe.patch .
RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=glue
	echo -e "\\n\\n>> [`date`] Cloning $p"
	git clone https://git.ligo.org/lscsoft/$p.git
	cd $p
	git checkout glue-release-1.59.2
	git apply ../glue_0000_zipsafe.patch
	${PYTHON2} setup.py install --prefix=$PREFIX
	cd ..
	rm -r $p
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=OpenBLAS
	git clone https://github.com/xianyi/$p.git
	cd $p
	git checkout v0.2.20
	make -j TARGET=ZEN
	make PREFIX=$PREFIX install
	cd ..
	rm -r $p
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=cfitsio3450
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts --no-check-certificate https://heasarc.gsfc.nasa.gov/FTP/software/fitsio/c/$p.tar.gz
	tar -xzf $p.tar.gz
	cd cfitsio
	./configure --prefix=$PREFIX
	make -j shared
	make install
	cd ..
	rm -r cfitsio
	rm $p.tar.gz
EOF

RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=Healpix_3.50_2018Dec10
	echo -e "\\n\\n>> [`date`] building $p"
	wget $wget_opts https://sourceforge.net/projects/healpix/files/Healpix_3.50/$p.tar.gz
	tar -xzf $p.tar.gz
	cd Healpix_3.50
	# Purely interactive configure script, doesn't take arguments
	printf '1\n\n\n\ngv\n\n2\n\n\n\n\n\n\n/usr/spiir/lib\n\ny\n4\n\n\n4\n0\n' | ./configure
	make -j
EOF
# Can't install into different directory
# cd ..
# rm -r $p
# rm $p.tar.gz

COPY gstlal_0001patrick_fix_includes_revised.patch .
RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	p=spiir
	echo -e "\\n\\n>> [`date`] Cloning $p"
	git clone --no-checkout https://git.ligo.org/lscsoft/$p.git
EOF

ARG DEBUG
RUN if [ -z "$DEBUG" ] ; then \
		export DEBUGFLAGS="-Og -fdebug-prefix-map=..=$(readlink -f ..) -gdwarf-2"; \
	fi

COPY gstlal /spiir/gstlal
RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	cd /spiir/gstlal
	make distclean || true
	# git apply ../../gstlal_0001patrick_fix_includes_revised.patch
	yes | head -n1 | ./00init.sh
	CFLAGS="$DEBUGFLAGS $CFLAGS" CXXFLAGS="$DEBUGFLAGS $CXXFLAGS" XDG_DATA_DIRS=$PREFIX/share:${XDG_DATA_DIRS:-} ./configure --prefix=$PREFIX
	XDG_DATA_DIRS=$PREFIX/share:${XDG_DATA_DIRS:-} make -j
	XDG_DATA_DIRS=$PREFIX/share:${XDG_DATA_DIRS:-} make install
EOF

COPY gstlal-inspiral /spiir/gstlal-inspiral
RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	cd /spiir/gstlal-inspiral
	make distclean || true
	yes | head -n1 | ./00init.sh
	CFLAGS="$DEBUGFLAGS $CFLAGS" CXXFLAGS="$DEBUGFLAGS $CXXFLAGS" ./configure --prefix=$PREFIX
	make -j
	make install
EOF

COPY gstlal-ugly /spiir/gstlal-ugly
RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	cd /spiir/gstlal-ugly
	make distclean || true
	yes | head -n1 | ./00init.sh
	CFLAGS="$DEBUGFLAGS $CFLAGS" CXXFLAGS="$DEBUGFLAGS $CXXFLAGS" ./configure --prefix=$PREFIX
	make -j
	make install
EOF

COPY gstlal-spiir /spiir/gstlal-spiir
RUN --mount=type=cache,target=/root/ccache \
	<<EOF
	cd /spiir/gstlal-spiir
	make distclean || true
	yes | head -n1 | ./00init.sh
	CFLAGS="$DEBUGFLAGS $CFLAGS" CXXFLAGS="$DEBUGFLAGS $CXXFLAGS" ./configure --prefix=$PREFIX --with-cuda=/usr/local/cuda
	make
	make install
EOF
