COMMON_CFLAGS = $(GSTLAL_SPIIR_CFLAGS)
COMMON_LIBS = $(GSTLAL_SPIIR_LIBS)

lib_LTLIBRARIES = libgstcommon.la

libgstcommon_la_SOURCES = \
	flag_segment/flag_segment.c \
	ifo_set/ifo_set.c

libgstcommon_la_CFLAGS = $(AM_CFLAGS) $(AM_CPPFLAGS) $(GSTREAMER_CFLAGS) $(GLIB_CFLAGS) $(COMMON_CFLAGS) $(GST_COMMON_SPIIR_CFLAGS)
libgstcommon_la_LIBADD = $(COMMON_LIBS) $(GSL_LIBS) $(GSTREAMER_LIBS) $(GLIB_LIBS)
libgstcommon_la_LDFLAGS = $(AM_LDFLAGS)
