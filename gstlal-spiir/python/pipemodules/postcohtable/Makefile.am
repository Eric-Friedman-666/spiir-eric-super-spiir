COMMON_CPP_FLAGS = $(GSTLAL_SPIIR_CFLAGS)
COMMON_LIBS = $(GSTLAL_SPIIR_LIBS)


# This is a trick taken from the gst-python automake setup.
# All of the Python scripts will be installed under the exec dir,
# which prevents the module from getting spread across lib and lib64
# on e.g. CentOS.
pkgpythondir = $(pkgpyexecdir)

postcohtabledir = $(pkgpythondir)/pipemodules/postcohtable
postcohtable_PYTHON = \
	__init__.py \
	postcoh_table_def.py \
	postcohtable.py 
	
postcohtable_LTLIBRARIES = _postcohtable.la

_postcohtable_la_SOURCES = _postcohtable.c
_postcohtable_la_CPPFLAGS = $(AM_CPPFLAGS) $(LAL_CFLAGS) $(GSL_CFLAGS) $(GSTLAL_CFLAGS) $(PYTHON_CPPFLAGS) $(COMMON_CPP_FLAGS) -DMODULE_NAME="\"gstlal.pipemodules.postcohtable._postcohtable\"" -fno-strict-aliasing
_postcohtable_la_CXXFLAGS = -fpermissive
_postcohtable_la_CFLAGS = $(AM_CFLAGS) -fno-strict-aliasing
_postcohtable_la_LIBADD = $(COMMON_LIBS) $(LAL_LIBS) $(GSL_LIBS) $(PYTHON_LIBS)
_postcohtable_la_LDFLAGS = $(AM_LDFLAGS) -module -avoid-version
