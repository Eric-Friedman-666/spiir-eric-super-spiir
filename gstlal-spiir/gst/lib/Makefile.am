lib_LTLIBRARIES = libgstcommon.la

libgstcommon_la_SOURCES = \
	flag_segment/flag_segment.c \
	ifo_set/ifo_set.c

libgstcommon_la_CFLAGS = $(AM_CFLAGS) $(AM_CPPFLAGS) $(gstreamer_CFLAGS) $(GSTLAL_SPIIR_CFLAGS) $(GST_COMMON_SPIIR_CFLAGS)
libgstcommon_la_LIBADD = $(GSTLAL_SPIIR_LIBS)
libgstcommon_la_LDFLAGS = $(AM_LDFLAGS) $(GSL_LIBS) $(gstreamer_LIBS)
