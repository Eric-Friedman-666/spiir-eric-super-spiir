lib_LTLIBRARIES = libgstlalspiir.la

libgstlalspiir_la_SOURCES = \
	LIGOLw_xmllib/LIGOLwReader.c \
	LIGOLw_xmllib/LIGOLwWriter.c \
	LIGOLw_xmllib/LIGOLwUtils.c \
	IFOMap/IFOMap.c

libgstlalspiir_la_CFLAGS = $(AM_CFLAGS) $(AM_CPPFLAGS) $(LIBXML_CFLAGS) $(GLIB_CFLAGS) $(GSTLAL_SPIIR_CFLAGS)
libgstlalspiir_la_LIBADD = $(LIBXML_LIBS) $(GLIB_LIBS)
libgstlalspiir_la_LDFLAGS = $(AM_LDFLAGS)
