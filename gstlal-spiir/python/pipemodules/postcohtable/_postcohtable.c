/*
 * Copyright (C) 2010	Kipp Cannon
 *
 * This program is free software; you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation; either version 2 of the License, or (at your
 * option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.	See the GNU
 * General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street, Fifth Floor, Boston, MA	02110-1301, USA.
 */

/*
 * ============================================================================
 *
 *									Preamble
 *
 * ============================================================================
 */

#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION

#define PY_SSIZE_T_CLEAN
#include <IFOMap.h>
#include <Python.h>
#include <lal/TimeSeries.h>
#include <lal/Units.h>
#include <numpy/ndarrayobject.h>
#include <pipe_macro.h>
#include <postcohtable.h>
#include <structmember.h>

// NOTE: This must be included after Python.h due to redefinition of
// _POSIX_C_SOURCE.
// Revisit after python upgrade
// See #15
#include <string.h>

/*
 * ============================================================================
 *
 *									Type
 *
 * ============================================================================
 */

/*
 * Cached ID types
 */

typedef struct {
    PyObject_HEAD
    PyStringObject *name;
    PyIntObject *epoch_gpsSeconds;
    PyIntObject *epoch_gpsNanoSeconds;
    PyFloatObject *f0;
    PyFloatObject *deltaT;
    PyStringObject *sampleUnits;
    PyIntObject *data_length;
    PyArrayObject *data;
    COMPLEX8TimeSeries *complex8_snr_series;
} Complex8TimeSeriesWrapper;

static void __del_snr_series__(PyObject *self) {
    Complex8TimeSeriesWrapper *self_typed = (Complex8TimeSeriesWrapper *)self;
    if (self_typed->complex8_snr_series) {
        XLALDestroyCOMPLEX8TimeSeries(self_typed->complex8_snr_series);
    }
    Py_XDECREF(self_typed->name);
    Py_XDECREF(self_typed->epoch_gpsSeconds);
    Py_XDECREF(self_typed->epoch_gpsNanoSeconds);
    Py_XDECREF(self_typed->f0);
    Py_XDECREF(self_typed->deltaT);
    Py_XDECREF(self_typed->sampleUnits);
    Py_XDECREF(self_typed->data_length);
    Py_XDECREF(self_typed->data);

    Py_TYPE(self)->tp_free(self);
}

static PyMemberDef members_snr_series[] = {
    { "name", T_OBJECT_EX, offsetof(Complex8TimeSeriesWrapper, name), 0,
      "name" },
    { "epoch_gpsSeconds", T_OBJECT_EX,
      offsetof(Complex8TimeSeriesWrapper, epoch_gpsSeconds), 0,
      "epoch_gpsSeconds" },
    { "epoch_gpsNanoSeconds", T_OBJECT_EX,
      offsetof(Complex8TimeSeriesWrapper, epoch_gpsNanoSeconds), 0,
      "epoch_gpsNanoSeconds" },
    { "f0", T_OBJECT_EX, offsetof(Complex8TimeSeriesWrapper, f0), 0, "f0" },
    { "deltaT", T_OBJECT_EX, offsetof(Complex8TimeSeriesWrapper, deltaT), 0,
      "deltaT" },
    { "sampleUnits", T_OBJECT_EX,
      offsetof(Complex8TimeSeriesWrapper, sampleUnits), 0, "sampleUnits" },
    { "data_length", T_OBJECT_EX,
      offsetof(Complex8TimeSeriesWrapper, data_length), 0, "data_length" },
    { "data", T_OBJECT_EX, offsetof(Complex8TimeSeriesWrapper, data), 0,
      "data" },
    { NULL },
};

static PyTypeObject snr_series_wrapper_type = {
    // clang-format off
  PyObject_HEAD_INIT(NULL) // PyObject_HEAD_INIT includes a trailing comma
  .tp_basicsize = sizeof(Complex8TimeSeriesWrapper), // clang-format on
    .tp_doc = "SNR Series structure",
    .tp_flags =
      Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_CHECKTYPES,
    .tp_members = members_snr_series,
    .tp_name    = MODULE_NAME ".SNRSeries",
    .tp_dealloc = __del_snr_series__,
};

static PyObject *
  new_wrapped_snr_series(PostcohInspiralTable *buffer_postcohtable) {

    PyObject *pyModule =
      PyImport_ImportModule("gstlal.pipemodules.postcohtable.postcohtable");
    PyObject *wrapped_snr_series_class =
      PyObject_GetAttrString(pyModule, "SNRSeries");

    PyObject *wrapped_snr_series_list = PyList_New(MAX_NIFO);
    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        Complex8TimeSeriesWrapper *wrapped_snr_series =
          (Complex8TimeSeriesWrapper *)PyType_GenericNew(
            (PyTypeObject *)wrapped_snr_series_class, NULL, NULL);
        if (!wrapped_snr_series) return NULL;

        COMPLEX8TimeSeries *complex8_snr_series =
          buffer_postcohtable->snr_series[ifo_id];

        if (complex8_snr_series && complex8_snr_series->data->length > 0) {
            wrapped_snr_series->complex8_snr_series = complex8_snr_series;

            wrapped_snr_series->name =
              PyString_FromString(complex8_snr_series->name);
            wrapped_snr_series->epoch_gpsSeconds =
              PyInt_FromLong(complex8_snr_series->epoch.gpsSeconds);
            wrapped_snr_series->epoch_gpsNanoSeconds =
              PyInt_FromLong(complex8_snr_series->epoch.gpsNanoSeconds);
            wrapped_snr_series->f0 =
              PyFloat_FromDouble(complex8_snr_series->f0);
            wrapped_snr_series->deltaT =
              PyFloat_FromDouble(complex8_snr_series->deltaT);
            char *s = XLALUnitToString(&complex8_snr_series->sampleUnits);
            wrapped_snr_series->sampleUnits = PyString_FromString(s);
            XLALFree(s);
            wrapped_snr_series->data_length =
              PyInt_FromLong(complex8_snr_series->data->length);

            npy_intp snr_series_dims[1] = { complex8_snr_series->data->length };
            wrapped_snr_series->data = PyArray_SimpleNewFromData(
              1, snr_series_dims, NPY_CFLOAT, complex8_snr_series->data->data);
        }
        PyList_SetItem(wrapped_snr_series_list, ifo_id,
                       (PyObject *)wrapped_snr_series);
    }
    return (PyObject *)wrapped_snr_series_list;
}

typedef struct {
    PyObject_HEAD
    PostcohInspiralTable row;
    PyStringObject *ifos;
    PyStringObject *pivotal_ifo;
    PyStringObject *skymap_fname;
    PyArrayObject *end_time_sngl;
    PyArrayObject *snglsnr;
    PyArrayObject *coaphase;
    PyArrayObject *chisq;
    PyArrayObject *far_sngl;
    PyArrayObject *far_1w_sngl;
    PyArrayObject *far_1d_sngl;
    PyArrayObject *far_2h_sngl;
    PyArrayObject *deff;
} PostcohInspiralWrapper;

static void __del_postcohinspiral__(PyObject *self) {
    PostcohInspiralWrapper *self_typed = (PostcohInspiralWrapper *)self;

    Py_XDECREF(self_typed->ifos);
    Py_XDECREF(self_typed->pivotal_ifo);
    Py_XDECREF(self_typed->skymap_fname);
    Py_XDECREF(self_typed->end_time_sngl);
    Py_XDECREF(self_typed->snglsnr);
    Py_XDECREF(self_typed->coaphase);
    Py_XDECREF(self_typed->chisq);
    Py_XDECREF(self_typed->far_sngl);
    Py_XDECREF(self_typed->far_1w_sngl);
    Py_XDECREF(self_typed->far_1d_sngl);
    Py_XDECREF(self_typed->far_2h_sngl);
    Py_XDECREF(self_typed->deff);

    Py_TYPE(self)->tp_free(self);
}

static PyMemberDef members_postcohinspiral[] = {
    { "ifos", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, ifos), 0, "ifos" },
    { "pivotal_ifo", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, pivotal_ifo),
      0, "pivotal_ifo" },
    { "skymap_fname", T_OBJECT_EX,
      offsetof(PostcohInspiralWrapper, skymap_fname), 0, "skymap_fname" },
    // Not dependent on the number of detectors
    { "end_time", T_INT,
      offsetof(PostcohInspiralWrapper, row.end_time.gpsSeconds), 0,
      "end_time" },
    { "end_time_ns", T_INT,
      offsetof(PostcohInspiralWrapper, row.end_time.gpsNanoSeconds), 0,
      "end_time_ns" },
    { "is_background", T_INT,
      offsetof(PostcohInspiralWrapper, row.is_background), 0,
      "is_background" },
    { "livetime", T_INT, offsetof(PostcohInspiralWrapper, row.livetime),
      0, "livetime" },
    { "tmplt_idx", T_INT, offsetof(PostcohInspiralWrapper, row.tmplt_idx),
      0, "tmplt_idx" },
    { "bankid", T_INT, offsetof(PostcohInspiralWrapper, row.bankid), 0,
      "bankid" },
    { "pix_idx", T_INT, offsetof(PostcohInspiralWrapper, row.pix_idx), 0,
      "pix_idx" },
    { "cohsnr", T_FLOAT, offsetof(PostcohInspiralWrapper, row.cohsnr), 0,
      "cohsnr" },
    { "nullsnr", T_FLOAT, offsetof(PostcohInspiralWrapper, row.nullsnr),
      0, "nullsnr" },
    { "cmbchisq", T_FLOAT, offsetof(PostcohInspiralWrapper, row.cmbchisq),
      0, "cmbchisq" },
    { "spearman_pval", T_FLOAT,
      offsetof(PostcohInspiralWrapper, row.spearman_pval), 0,
      "spearman_pval" },
    { "fap", T_FLOAT, offsetof(PostcohInspiralWrapper, row.fap), 0,
      "fap" },
    { "far_2h", T_FLOAT, offsetof(PostcohInspiralWrapper, row.far_2h), 0,
      "far_2h" },
    { "far_1d", T_FLOAT, offsetof(PostcohInspiralWrapper, row.far_1d), 0,
      "far_1d" },
    { "far_1w", T_FLOAT, offsetof(PostcohInspiralWrapper, row.far_1w), 0,
      "far_1w" },
    { "far", T_FLOAT, offsetof(PostcohInspiralWrapper, row.far), 0,
      "far" },
    { "rank", T_DOUBLE, offsetof(PostcohInspiralWrapper, row.rank), 0,
      "rank" },
    { "template_duration", T_DOUBLE,
      offsetof(PostcohInspiralWrapper, row.template_duration), 0,
      "template_duration" },
    { "mass1", T_FLOAT, offsetof(PostcohInspiralWrapper, row.mass1), 0,
      "mass1" },
    { "mass2", T_FLOAT, offsetof(PostcohInspiralWrapper, row.mass2), 0,
      "mass2" },
    { "mchirp", T_FLOAT, offsetof(PostcohInspiralWrapper, row.mchirp), 0,
      "mchirp" },
    { "mtotal", T_FLOAT, offsetof(PostcohInspiralWrapper, row.mtotal), 0,
      "mtotal" },
    { "eta", T_FLOAT, offsetof(PostcohInspiralWrapper, row.eta), 0,
      "eta" },
    { "spin1x", T_FLOAT, offsetof(PostcohInspiralWrapper, row.spin1x), 0,
      "spin1x" },
    { "spin1y", T_FLOAT, offsetof(PostcohInspiralWrapper, row.spin1y), 0,
      "spin1y" },
    { "spin1z", T_FLOAT, offsetof(PostcohInspiralWrapper, row.spin1z), 0,
      "spin1z" },
    { "spin2x", T_FLOAT, offsetof(PostcohInspiralWrapper, row.spin2x), 0,
      "spin2x" },
    { "spin2y", T_FLOAT, offsetof(PostcohInspiralWrapper, row.spin2y), 0,
      "spin2y" },
    { "spin2z", T_FLOAT, offsetof(PostcohInspiralWrapper, row.spin2z), 0,
      "spin2z" },
    { "ra", T_DOUBLE, offsetof(PostcohInspiralWrapper, row.ra), 0, "ra" },
    { "dec", T_DOUBLE, offsetof(PostcohInspiralWrapper, row.dec), 0,
      "dec" },
    { "f_final", T_FLOAT, offsetof(PostcohInspiralWrapper, row.f_final),
      0, "f_final" },
    { "_process_id", T_LONG,
      offsetof(PostcohInspiralWrapper, row.process_id), 0,
      "process_id (long)" },
    { "_event_id", T_LONG, offsetof(PostcohInspiralWrapper, row.event_id),
      0, "event_id (long)" },

    // Things that are done single detector are ndarrays
    { "end_time_sngl", T_OBJECT_EX,
      offsetof(PostcohInspiralWrapper, end_time_sngl), 0, "end_time_sngl" },
    { "snglsnr", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, snglsnr), 0,
      "snglsnr" },
    { "coaphase", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, coaphase), 0,
      "coaphase" },
    { "chisq", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, chisq), 0,
      "chisq" },
    { "far_sngl", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, far_sngl), 0,
      "far_sngl" },
    { "far_1w_sngl", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, far_1w_sngl),
      0, "far_1w_sngl" },
    { "far_1d_sngl", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, far_1d_sngl),
      0, "far_1d_sngl" },
    { "far_2h_sngl", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, far_2h_sngl),
      0, "far_2h_sngl" },
    { "deff", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, deff), 0, "deff" },
    { NULL },
};

static PyTypeObject postcoh_inspiral_wrapper_type = {
    // clang-format off
  PyObject_HEAD_INIT(NULL) // PyObject_HEAD_INIT includes a trailing comma
  .tp_basicsize = sizeof(PostcohInspiralWrapper), // clang-format on
    .tp_doc = "LAL's PostcohInspiral structure",
    .tp_flags =
      Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_CHECKTYPES,
    .tp_members = members_postcohinspiral,
    .tp_name    = MODULE_NAME ".PostcohInspiral",
    .tp_dealloc = __del_postcohinspiral__,
};

static PostcohInspiralWrapper *
  new_wrapped_postcohtable(PostcohInspiralTable *buffer_postcohtable) {
    PyObject *pyModule =
      PyImport_ImportModule("gstlal.pipemodules.postcohtable.postcohtable");
    PyObject *wrapped_postcohtable_class =
      PyObject_GetAttrString(pyModule, "PostcohInspiral");

    PostcohInspiralWrapper *self = (PostcohInspiralWrapper *)PyType_GenericNew(
      (PyTypeObject *)wrapped_postcohtable_class, NULL, NULL);
    if (!self) return NULL;

    npy_intp dims[1]          = { MAX_NIFO };
    npy_intp end_time_dims[2] = { 2, MAX_NIFO };

    self->row           = *buffer_postcohtable;

    self->ifos         = PyString_FromString(self->row.ifos);
    self->pivotal_ifo  = PyString_FromString(self->row.pivotal_ifo);
    self->skymap_fname = PyString_FromString(self->row.skymap_fname);
    self->end_time_sngl = PyArray_SimpleNewFromData(
      2, end_time_dims, NPY_INT, self->row.end_time_sngl);
    self->snglsnr  = PyArray_SimpleNewFromData(1, dims, NPY_FLOAT,
                                               self->row.snglsnr);
    self->coaphase = PyArray_SimpleNewFromData(1, dims, NPY_FLOAT,
                                               self->row.coaphase);
    self->chisq =
      PyArray_SimpleNewFromData(1, dims, NPY_FLOAT, self->row.chisq);
    self->far_sngl    = PyArray_SimpleNewFromData(1, dims, NPY_FLOAT,
                                                  self->row.far_sngl);
    self->far_1w_sngl = PyArray_SimpleNewFromData(
      1, dims, NPY_FLOAT, self->row.far_1w_sngl);
    self->far_1d_sngl = PyArray_SimpleNewFromData(
      1, dims, NPY_FLOAT, self->row.far_1d_sngl);
    self->far_2h_sngl = PyArray_SimpleNewFromData(
      1, dims, NPY_FLOAT, self->row.far_2h_sngl);
    self->deff =
      PyArray_SimpleNewFromData(1, dims, NPY_DOUBLE, self->row.deff);

    return (PyObject *)self;
}

typedef struct {
    PyObject_HEAD
    PostcohInspiralWrapper *postcohinspiral;
    PyListObject *snr_series;
} PostcohEvent;

static void __del_postcohevent__(PyObject *self) {
    PostcohEvent *self_typed = (PostcohEvent *)self;

    Py_XDECREF(self_typed->postcohinspiral);
    Py_XDECREF(self_typed->snr_series);

    Py_TYPE(self)->tp_free(self);
}

static PyMemberDef members_postcohevent[] = {
    { "postcohinspiral", T_OBJECT_EX, offsetof(PostcohEvent, postcohinspiral),
      0, "postcohinspiral" },
    // Things that are done single detector are ndarrays
    { "snr_series", T_OBJECT_EX, offsetof(PostcohEvent, snr_series), 0,
      "snr_series" },
    { NULL },
};

static PyTypeObject postcohevent_type = {
    // clang-format off
  PyObject_HEAD_INIT(NULL) // PyObject_HEAD_INIT includes a trailing comma
  .tp_basicsize = sizeof(PostcohEvent), // clang-format on
    .tp_doc = "Postcoh Event structure",
    .tp_flags =
      Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_CHECKTYPES,
    .tp_members = members_postcohevent,
    .tp_name    = MODULE_NAME ".PostcohEvent",
    .tp_dealloc = __del_postcohevent__,
};

static PostcohEvent *
  new_postcohevent(PostcohInspiralTable *buffer_postcohtable) {
    PyObject *pyModule =
      PyImport_ImportModule("gstlal.pipemodules.postcohtable.postcohtable");
    PyObject *postcohevent_class =
      PyObject_GetAttrString(pyModule, "PostcohEvent");

    PostcohEvent *self = (PostcohEvent *)PyType_GenericNew(
      (PyTypeObject *)postcohevent_class, NULL, NULL);
    if (!self) return NULL;

    PostcohInspiralWrapper *wrapped_postcohtable =
      new_wrapped_postcohtable(buffer_postcohtable);
    if (!wrapped_postcohtable) {
        Py_DECREF(self);
        PyErr_SetString(PyExc_ValueError, "wrapped_postcohtable error");
        return NULL;
    }

    self->postcohinspiral = wrapped_postcohtable;

    PyObject *wrapped_snr_series_list =
      new_wrapped_snr_series(buffer_postcohtable);
    if (!wrapped_snr_series_list) {
        Py_DECREF(self);
        PyErr_SetString(PyExc_ValueError, "wrapped_snr_series error");
        return NULL;
    }

    self->snr_series = wrapped_snr_series_list;
    return (PyObject *)self;
}

/*
 * ============================================================================
 *
 *							Module Registration
 *
 * ============================================================================
 */

static PyObject *from_buffer(PyObject *cls, PyObject *args) {
    const char *data;
    Py_ssize_t length;

    if (!PyArg_ParseTuple(args, "s#", &data, &length)) return NULL;
    const char *const end = data + length;

    PyObject *event_list = PyList_New(0);

    if (!event_list) {
        PyErr_SetString(PyExc_ValueError, "event list error");
        return NULL;
    }

    while (data < end) {
        /* memcpy postcoh postcohtable */
        const PostcohInspiralTable *buffer_postcohtable =
          (const PostcohInspiralTable *)data;
        data += sizeof(PostcohInspiralTable);
        /* if the data read in is less then expected amount */
        if (data > end) {
            Py_DECREF(event_list);
            PyErr_SetString(PyExc_ValueError,
                            "overran end of buffer while deserializing a "
                            "PostcohInspiralTable");
            return NULL;
        }

        PostcohEvent *postcohevent = new_postcohevent(buffer_postcohtable);

        if (!postcohevent) {
            Py_DECREF(event_list);
            PyErr_SetString(PyExc_ValueError, "postcohevent error");
            return NULL;
        }

        PyList_Append(event_list, (PyObject *)postcohevent);
        Py_DECREF(postcohevent);
    }

    if (data != end) {
        Py_DECREF(event_list);
        PyErr_SetString(PyExc_ValueError, "did not consume entire buffer");
        return NULL;
    }

    return event_list;
}

static struct PyMethodDef methods[] = {
    { "from_buffer", from_buffer, METH_VARARGS,
      "Construct a tuple of PostcohInspiralTable objects from a buffer "
      "object. "
      " The buffer is interpreted as a C array of PostcohInspiralTable "
      "structures." },
    {
      NULL,
    }
};

PyMODINIT_FUNC init_postcohtable(void) {
    PyObject *module = Py_InitModule3(
      MODULE_NAME, methods, "Wrapper for LAL's PostcohInspiralTable type.");

    if (module == NULL) return;
    import_array();

    PyObject *ifo_map = PyList_New(MAX_NIFO);
    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        PyObject *str = PyString_FromStringAndSize(
          get_ifo_string(ifo_id), strlen(get_ifo_string(ifo_id)));
        assert(str);
        Py_INCREF(str);
        PyList_SetItem(ifo_map, ifo_id, str);
    }
    PyModule_AddObject(module, "ifo_map", ifo_map);

    if (PyType_Ready(&postcoh_inspiral_wrapper_type) < 0) return;
    Py_INCREF(&postcoh_inspiral_wrapper_type);

    PyModule_AddObject(module, "PostcohInspiral",
                       (PyObject *)&postcoh_inspiral_wrapper_type);

    if (PyType_Ready(&snr_series_wrapper_type) < 0) return;
    Py_INCREF(&snr_series_wrapper_type);

    PyModule_AddObject(module, "SNRSeries",
                       (PyObject *)&snr_series_wrapper_type);

    if (PyType_Ready(&postcohevent_type) < 0) return;
    Py_INCREF(&postcohevent_type);

    PyModule_AddObject(module, "PostcohEvent", (PyObject *)&postcohevent_type);
}
