# syntax = docker/dockerfile:1.2
FROM nvidia/cuda:10.0-cudnn7-devel-ubuntu18.04

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
	${CONDA} create -p ${PYTHON2PREFIX} -y python=2.7.14 && \
	${PIP2} config set global.cache-dir false && \
	${PIP2} install --upgrade pip setuptools && \
	${PIP2} install numpy==1.14.1 scipy==1.0.0 matplotlib==2.2.2   h5py==2.7.1 healpy==1.12.4 astropy==2.0.3 \
	cryptography pyopenssl Cython ligo-segments shapely yapf clang-format && \
	${CONDA} clean -a

ENV PYTHON3PREFIX ${PREFIX}/python3
ENV PYTHON3 ${PYTHON3PREFIX}/bin/python
ENV PIP3 ${PYTHON3PREFIX}/bin/pip
RUN --mount=type=cache,target=/root/.cache \
	--mount=type=cache,target=/root/.conda/pkgs \
	${CONDA} create -p ${PYTHON3PREFIX} -y python=3.7.4 && \
	${PIP3} config set global.cache-dir false && \
	${PIP3} install --upgrade pip setuptools && \
	${PIP3} install meson==0.60.3 ninja --prefix=$PREFIX/python3_stuff && \
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

RUN $CONDA create -n postprocess tqdm pandas lscsoft-glue ligo.skymap ligo-proxy-utils gwpy -c conda-forge

ENV POSTPROCESS /root/.conda/envs/postprocess/bin/python

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

COPY submit.sh .
COPY generate_pipeline_artifacts.sh .
COPY pipeline.sh .
COPY create_skymaps.py .

# # create injection xml
# lalapps_inspinj --m-distr totalMass --min-mass1 1 --max-mass1 3 --min-mass2 1 --max-mass2 3 --min-mtotal 2 --max-mtotal 6 --gps-start-time 1187006000 --gps-end-time 1187006500 --enable-spin --min-spin1 0 --max-spin1 0.4 --min-spin2 0 --max-spin2 0.4 --waveform SpinTaylorT4threePointFivePN --f-lower 20 --i-distr uniform --l-distr random --t-distr uniform --time-step 30 --taper-injection start --seed 1 --output fake_inj.xml --d-distr uniform --min-distance 5000 --max-distance 20000 --verbose
# ligolw_print −t sim_inspiral −c h_end_time −c mass1 −c mass2 −c mchirp −c eta −c spin1z −c spin2z −c eff_dist_h −c alpha4 −c longitude −c latitude fake_inj_1.xml

# # create fake frames from injection xml
# gstlal_fake_frames --data-source LIGO --output-path fake_frames_inj --gps-start-time 1187006000 --frame-type H1_INJECTIONS --gps-end-time 1187006500 --frame-duration 16 --frames-per-file 125 --verbose --channel-name=H1=FAKE_INJECTIONS --injections fake_inj.xml
# gstlal_fake_frames --data-source AdvLIGO --output-path fake_frames_inj --gps-start-time 1187006000 --frame-type L1_INJECTIONS --gps-end-time 1187006500 --frame-duration 16 --frames-per-file 125 --verbose --channel-name=L1=FAKE_INJECTIONS --injections fake_inj.xml
# gstlal_fake_frames --data-source AdvVirgo --output-path fake_frames_inj --gps-start-time 1187006000 --frame-type V1_INJECTIONS --gps-end-time 1187006500 --frame-duration 16 --frames-per-file 125 --verbose --channel-name=V1=FAKE_INJECTIONS --injections fake_inj.xml
# gstlal_fake_frames --data-source AdvVirgo --output-path fake_frames_inj --gps-start-time 1187006000 --frame-type K1_INJECTIONS --gps-end-time 1187006500 --frame-duration 16 --frames-per-file 125 --verbose --channel-name=K1=FAKE_INJECTIONS --injections fake_inj.xml

# # append fake frames to cache file
# ls fake_frames_inj/*/*.gwf | lalapps_path2cache >> fake-frame.cache

# # # generate reference_psd
# # gstlal_reference_psd --data-source frames --frame-cache fake-frame.cache --gps-start-time=1187006000 --gps-end-time=1187006500 --channel-name=H1=FAKE_INJECTIONS --channel-name=L1=FAKE_INJECTIONS --channel-name=V1=FAKE_INJECTIONS --channel-name=K1=FAKE_INJECTIONS --write-psd H1L1V1K1-REFERENCE_PSD-1187006000-500.xml.gz --verbose --psd-fft-length 16

# # # generate spiir bank
# # gstlal_iir_bank --reference-psd ${PSD} --template-bank /fred/oz016/manoj/test/new_split/${SBNK}/${IFO}_split_bank/${IFO}-GSTLAL_SPLIT_BANK_${BNK}-0-0.xml.gz --flow 15.0 --waveform-domain FD --padding 1.3 --instrument ${IFO} --output gstlal_iir_bank_${SUF}/iir_${IFO}-GSTLAL_SPLIT_BANK_${BNK}-a1-0-0.xml.gz --autocorrelation-length 351 --sampleRate 2048.0 -v --epsilon-options "'{"epsilon_start":1.0,"nround_max":25,"initial_overlap_min":0.95,"b0_optimized_overlap_min":0.'"${BOPTPERC}"',"epsilon_factor":1.2,"filters_max":350}'" --optimizer-options "'{"verbose":true,"passes":16,"indv":true,"hessian":true}'" --approximant ${APPROX} --negative-latency ${NEGLAT}"

# # generate detrsp map
# gstlal_postcoh_gen_detrsp_map --ifo-horizons H1:111,L1:212,V1:56,K1:56 --chealpix-order 5 --output-coh-coeff H1L1V1K1_detrsp_map.xml --output-prob-coeff H1L1V1K1_prob_map.xml --gps-time 1187006000

# # run pipeline
# gstlal_inspiral_postcohspiir_online --job-tag 000  --iir-bank H1:iir_H1-GSTLAL_SPLIT_BANK_0003-a1-0-0.xml.gz,L1:iir_L1-GSTLAL_SPLIT_BANK_0003-a1-0-0.xml.gz,V1:iir_V1-GSTLAL_SPLIT_BANK_0003-a1-0-0.xml.gz,K1:iir_K1-GSTLAL_SPLIT_BANK_0003-a1-0-0.xml.gz  --gpu-acc on  --data-source frames  --frame-cache fake-frame.cache --gps-start-time 1187006000  --gps-end-time 1187006500  --track-psd --channel-name H1=FAKE_INJECTIONS  --channel-name L1=FAKE_INJECTIONS  --channel-name V1=FAKE_INJECTIONS  --channel-name K1=FAKE_INJECTIONS  --cohfar-accumbackground-output-prefix 000/bank0_stats  --cohfar-accumbackground-snapshot-interval 200  --cohfar-assignfar-silent-time 0  --cohfar-assignfar-input-fname 000/marginalized_1w.xml.gz,000/marginalized_1d.xml.gz,000/marginalized_2h.xml.gz  --cohfar-assignfar-refresh-interval 200  --gpu-acc on  --ht-gate-threshold 15.0  --cuda-postcoh-snglsnr-thresh 4  --cuda-postcoh-hist-trials 100  --cuda-postcoh-detrsp-fname H1L1V1K1_detrsp_map.xml  --cuda-postcoh-detrsp-refresh-interval 86400  --cuda-postcoh-output-skymap 7  --check-time-stamp  --finalsink-fapupdater-collect-walltime 604800,86400,7200  --finalsink-fapupdater-interval 1800  --finalsink-output-prefix 000/000_zerolag  --finalsink-snapshot-interval 1200  --finalsink-cluster-window 1  --finalsink-far-factor 2  --finalsink-singlefar-veto-thresh 0.5  --finalsink-superevent-thresh 0.0001  --finalsink-need-online-perform 1  --finalsink-gracedb-far-threshold 0.0001  --code-version unit_testing  --verbose

# # find top event_id, time, and skymap name
# ligolw_print -c bankid -c cohsnr -c fap -c far -c ifos -c event_id -c end_time -c skymap_fname 000/000_zerolag_1187006000_469.xml.gz -v

# # generate fits from skymap binary
# gstlal_postcoh_skymap2fits --output-cohsnr cohsnr_skymap.fits.gz --output-prob spiir.fits.gz --cuda-postcoh-detrsp-fname H1L1V1K1_prob_map.xml --event-id 0 --event-time 1187006432 H1L1_skymap/H1_1187006432_89355469_3_29

# # generate .png from fits
# bayestar_plot_allsky_postcohspiir -o cohsnr_skymap --label "Coherent SNR" spiir.fits.gz --colorbar --colormap "cylon"
# ligo-skymap-plot -o cohsnr.png --colorbar --colormap viridis cohsnr_skymap.fits.gz
# ligo-skymap-plot -o prob.png --colorbar --colormap viridis spiir.fits.gz

# /apps/skylake/software/compiler/gcc/6.4.0/python/2.7.14/bin/python
