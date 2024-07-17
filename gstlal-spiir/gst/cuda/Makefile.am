COMMON_CPP_FLAGS = $(GSTLAL_SPIIR_CFLAGS) $(GST_COMMON_SPIIR_CFLAGS) $(LIBXML_CFLAGS) $(LAPACKE_CFLAGS) $(SPIIR_FIX_COMPLEX_CFLAGS)
COMMON_LIBS = $(GSTLAL_SPIIR_LIBS) $(GST_COMMON_SPIIR_LIBS) $(LIBXML_LIBS) $(LAPACKE_LIBS)

plugin_LTLIBRARIES = libgstcuda.la

libgstcuda_la_SOURCES = \
	libgstcuda.c \
	multiratespiir/multiratespiir_kernel.cu \
	multiratespiir/multiratespiir_utils.c \
	multiratespiir/multiratespiir.c \
	postcoh/postcoh_kernel.cu \
	postcoh/postcoh_utils.c \
	postcoh/postcoh.c \
	postcoh/postcohtable_utils.c \
	postcoh/postcoh_filesink.c \
	cohfar/knn_kde.c \
	cohfar/ssvkernel.c \
	cohfar/background_stats_utils.c \
	cohfar/cohfar_accumbackground.c \
	cohfar/cohfar_assignfar.c

libgstcuda_la_CPPFLAGS = $(AM_CPPFLAGS) $(GSL_CFLAGS) $(LAL_CFLAGS) $(GSTLAL_CFLAGS) $(GSTREAMER_CFLAGS) $(GLIB_CFLAGS) $(NVCC_CFLAGS) $(CHEALPIX_CFLAGS) $(COMMON_CPP_FLAGS)
libgstcuda_la_CFLAGS = $(AM_CFLAGS)
libgstcuda_la_LIBADD = $(COMMON_LIBS) $(GSL_LIBS) $(LAL_LIBS) $(GSTLAL_LIBS) $(GSTREAMER_LIBS) $(GLIB_LIBS) $(NVCC_LIBS) $(CHEALPIX_LIBS)
libgstcuda_la_LDFLAGS = $(AM_LDFLAGS) -lstdc++ 

.cu.lo:
	$(top_srcdir)/gnuscripts/cudalt.py $@ $(NVCC) $(NVCC_CFLAGS) $(NVCC_ADD_CFLAGS) $(DEFAULT_INCLUDES) --ptxas-options=-v -O0  -maxrregcount=0 -gencode=arch=compute_75,code=sm_75 -gencode=arch=compute_70,code=sm_70 -gencode=arch=compute_61,code=sm_61 -gencode=arch=compute_60,code=sm_60 -gencode=arch=compute_52,code=sm_52 --compiler-options=\"$(libgstcuda_la_CPPFLAGS) $(libgstcuda_la_CFLAGS)\" -c $<

noinst_HEADERS = \
	complex_f.h \
	multiratespiir/multiratespiir_state.h \
	multiratespiir/multiratespiir_kernel.h \
	multiratespiir/multiratespiir_utils.h \
	multiratespiir/multiratespiir.h \
	postcoh/postcoh_state.h \
	postcoh/postcoh_kernel.h \
	postcoh/postcoh_utils.h \
	postcoh/postcoh.h \
	postcoh/postcohtable_utils.h \
	cohfar/background_stats.h \
	cohfar/background_stats_utils.h \
	cohfar/cohfar_accumbackground.h \
	cohfar/cohfar_assignfar.h

gstlal_cohfar_calc_fap_SOURCES = \
	cohfar/ssvkernel.c \
	cohfar/knn_kde.c \
	cohfar/background_stats_utils.c \
	cohfar/cohfar_calc_fap.c

gstlal_cohfar_calc_fap_CFLAGS = $(AM_CFLAGS) $(AM_CPPFLAGS) $(GSL_CFLAGS) $(LAL_CFLAGS) $(GSTLAL_CFLAGS) $(GSTREAMER_CFLAGS) $(GLIB_CFLAGS) $(COMMON_CPP_FLAGS)
gstlal_cohfar_calc_fap_LDADD = $(COMMON_LIBS) $(GSL_LIBS) $(LAL_LIBS) $(GSTLAL_LIBS) $(GSTREAMER_LIBS) $(GLIB_LIBS)
gstlal_cohfar_calc_fap_LDFLAGS = $(AM_LDFLAGS)
gstlal_postcoh_gen_detrsp_map_SOURCES = \
	postcoh/postcoh_detrsp_map.c

gstlal_postcoh_gen_detrsp_map_CFLAGS = $(AM_CFLAGS) $(AM_CPPFLAGS) $(GSL_CFLAGS) $(LAL_CFLAGS) $(GSTLAL_CFLAGS) $(GSTREAMER_CFLAGS) $(GLIB_CFLAGS) $(NVCC_CFLAGS) $(CHEALPIX_CFLAGS) $(COMMON_CPP_FLAGS)
gstlal_postcoh_gen_detrsp_map_LDADD = $(COMMON_LIBS) $(GSL_LIBS) $(LAL_LIBS) $(GSTLAL_LIBS) $(GSTREAMER_LIBS) $(GLIB_LIBS) $(NVCC_LIBS) $(CHEALPIX_LIBS)
gstlal_postcoh_gen_detrsp_map_LDFLAGS = $(AM_LDFLAGS)

bin_PROGRAMS = \
	gstlal_cohfar_calc_fap \
	gstlal_postcoh_gen_detrsp_map
