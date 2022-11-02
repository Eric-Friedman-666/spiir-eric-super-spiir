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

#include <string.h>
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

    Py_XDECREF(self_typed->name);
    Py_XDECREF(self_typed->epoch_gpsSeconds);
    Py_XDECREF(self_typed->epoch_gpsNanoSeconds);
    Py_XDECREF(self_typed->f0);
    Py_XDECREF(self_typed->deltaT);
    Py_XDECREF(self_typed->sampleUnits);
    Py_XDECREF(self_typed->data_length);
    Py_XDECREF(self_typed->data);
    if (self_typed->complex8_snr_series) {
        XLALDestroyCOMPLEX8TimeSeries(self_typed->complex8_snr_series);
    }

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

            npy_intp snr_series_dims[1] = { complex8_snr_series->data->length };

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
    PyStringObject *ifos;
    PyStringObject *pivotal_ifo;
    PyStringObject *skymap_fname;
    PyIntObject *end_time;
    PyIntObject *end_time_ns;
    PyIntObject *is_background;
    PyIntObject *livetime;
    PyIntObject *tmplt_idx;
    PyIntObject *bankid;
    PyIntObject *pix_idx;
    PyFloatObject *cohsnr;
    PyFloatObject *nullsnr;
    PyFloatObject *cmbchisq;
    PyFloatObject *spearman_pval;
    PyFloatObject *fap;
    PyFloatObject *far_2h;
    PyFloatObject *far_1d;
    PyFloatObject *far_1w;
    PyFloatObject *far;
    PyFloatObject *rank;
    PyFloatObject *template_duration;
    PyFloatObject *mass1;
    PyFloatObject *mass2;
    PyFloatObject *mchirp;
    PyFloatObject *mtotal;
    PyFloatObject *eta;
    PyFloatObject *spin1x;
    PyFloatObject *spin1y;
    PyFloatObject *spin1z;
    PyFloatObject *spin2x;
    PyFloatObject *spin2y;
    PyFloatObject *spin2z;
    PyFloatObject *ra;
    PyFloatObject *dec;
    PyFloatObject *f_final;
    PyLongObject *_process_id;
    PyLongObject *_event_id;

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
    Py_XDECREF(self_typed->end_time);
    Py_XDECREF(self_typed->end_time_ns);
    Py_XDECREF(self_typed->is_background);
    Py_XDECREF(self_typed->livetime);
    Py_XDECREF(self_typed->tmplt_idx);
    Py_XDECREF(self_typed->bankid);
    Py_XDECREF(self_typed->pix_idx);
    Py_XDECREF(self_typed->cohsnr);
    Py_XDECREF(self_typed->nullsnr);
    Py_XDECREF(self_typed->cmbchisq);
    Py_XDECREF(self_typed->spearman_pval);
    Py_XDECREF(self_typed->fap);
    Py_XDECREF(self_typed->far_2h);
    Py_XDECREF(self_typed->far_1d);
    Py_XDECREF(self_typed->far_1w);
    Py_XDECREF(self_typed->far);
    Py_XDECREF(self_typed->rank);
    Py_XDECREF(self_typed->template_duration);
    Py_XDECREF(self_typed->mass1);
    Py_XDECREF(self_typed->mass2);
    Py_XDECREF(self_typed->mchirp);
    Py_XDECREF(self_typed->mtotal);
    Py_XDECREF(self_typed->eta);
    Py_XDECREF(self_typed->spin1x);
    Py_XDECREF(self_typed->spin1y);
    Py_XDECREF(self_typed->spin1z);
    Py_XDECREF(self_typed->spin2x);
    Py_XDECREF(self_typed->spin2y);
    Py_XDECREF(self_typed->spin2z);
    Py_XDECREF(self_typed->ra);
    Py_XDECREF(self_typed->dec);
    Py_XDECREF(self_typed->f_final);
    Py_XDECREF(self_typed->_process_id);
    Py_XDECREF(self_typed->_event_id);
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
    { "end_time", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, end_time), 0,
      "end_time" },
    { "end_time_ns", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, end_time_ns),
      0, "end_time_ns" },
    { "is_background", T_OBJECT_EX,
      offsetof(PostcohInspiralWrapper, is_background), 0, "is_background" },
    { "livetime", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, livetime), 0,
      "livetime" },
    { "tmplt_idx", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, tmplt_idx), 0,
      "tmplt_idx" },
    { "bankid", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, bankid), 0,
      "bankid" },
    { "pix_idx", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, pix_idx), 0,
      "pix_idx" },
    { "cohsnr", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, cohsnr), 0,
      "cohsnr" },
    { "nullsnr", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, nullsnr), 0,
      "nullsnr" },
    { "cmbchisq", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, cmbchisq), 0,
      "cmbchisq" },
    { "spearman_pval", T_OBJECT_EX,
      offsetof(PostcohInspiralWrapper, spearman_pval), 0, "spearman_pval" },
    { "fap", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, fap), 0, "fap" },
    { "far_2h", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, far_2h), 0,
      "far_2h" },
    { "far_1d", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, far_1d), 0,
      "far_1d" },
    { "far_1w", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, far_1w), 0,
      "far_1w" },
    { "far", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, far), 0, "far" },
    { "rank", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, rank), 0, "rank" },
    { "template_duration", T_OBJECT_EX,
      offsetof(PostcohInspiralWrapper, template_duration), 0,
      "template_duration" },
    { "mass1", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, mass1), 0,
      "mass1" },
    { "mass2", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, mass2), 0,
      "mass2" },
    { "mchirp", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, mchirp), 0,
      "mchirp" },
    { "mtotal", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, mtotal), 0,
      "mtotal" },
    { "eta", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, eta), 0, "eta" },
    { "spin1x", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, spin1x), 0,
      "spin1x" },
    { "spin1y", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, spin1y), 0,
      "spin1y" },
    { "spin1z", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, spin1z), 0,
      "spin1z" },
    { "spin2x", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, spin2x), 0,
      "spin2x" },
    { "spin2y", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, spin2y), 0,
      "spin2y" },
    { "spin2z", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, spin2z), 0,
      "spin2z" },
    { "ra", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, ra), 0, "ra" },
    { "dec", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, dec), 0, "dec" },
    { "f_final", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, f_final), 0,
      "f_final" },
    { "_process_id", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, _process_id),
      0, "process_id (long)" },
    { "_event_id", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, _event_id), 0,
      "event_id (long)" },

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

    self->ifos         = PyString_FromString(buffer_postcohtable->ifos);
    self->pivotal_ifo  = PyString_FromString(buffer_postcohtable->pivotal_ifo);
    self->skymap_fname = PyString_FromString(buffer_postcohtable->skymap_fname);
    self->end_time = PyInt_FromLong(buffer_postcohtable->end_time.gpsSeconds);
    self->end_time_ns =
      PyInt_FromLong(buffer_postcohtable->end_time.gpsNanoSeconds);
    self->is_background = PyInt_FromLong(buffer_postcohtable->is_background);
    self->livetime      = PyInt_FromLong(buffer_postcohtable->livetime);
    self->tmplt_idx     = PyInt_FromLong(buffer_postcohtable->tmplt_idx);
    self->bankid        = PyInt_FromLong(buffer_postcohtable->bankid);
    self->pix_idx       = PyInt_FromLong(buffer_postcohtable->pix_idx);
    self->cohsnr        = PyFloat_FromDouble(buffer_postcohtable->cohsnr);
    self->nullsnr       = PyFloat_FromDouble(buffer_postcohtable->nullsnr);
    self->cmbchisq      = PyFloat_FromDouble(buffer_postcohtable->cmbchisq);
    self->spearman_pval =
      PyFloat_FromDouble(buffer_postcohtable->spearman_pval);
    self->fap    = PyFloat_FromDouble(buffer_postcohtable->fap);
    self->far_2h = PyFloat_FromDouble(buffer_postcohtable->far_2h);
    self->far_1d = PyFloat_FromDouble(buffer_postcohtable->far_1d);
    self->far_1w = PyFloat_FromDouble(buffer_postcohtable->far_1w);
    self->far    = PyFloat_FromDouble(buffer_postcohtable->far);
    self->rank   = PyFloat_FromDouble(buffer_postcohtable->rank);
    self->template_duration =
      PyFloat_FromDouble(buffer_postcohtable->template_duration);
    self->mass1       = PyFloat_FromDouble(buffer_postcohtable->mass1);
    self->mass2       = PyFloat_FromDouble(buffer_postcohtable->mass2);
    self->mchirp      = PyFloat_FromDouble(buffer_postcohtable->mchirp);
    self->mtotal      = PyFloat_FromDouble(buffer_postcohtable->mtotal);
    self->eta         = PyFloat_FromDouble(buffer_postcohtable->eta);
    self->spin1x      = PyFloat_FromDouble(buffer_postcohtable->spin1x);
    self->spin1y      = PyFloat_FromDouble(buffer_postcohtable->spin1y);
    self->spin1z      = PyFloat_FromDouble(buffer_postcohtable->spin1z);
    self->spin2x      = PyFloat_FromDouble(buffer_postcohtable->spin2x);
    self->spin2y      = PyFloat_FromDouble(buffer_postcohtable->spin2y);
    self->spin2z      = PyFloat_FromDouble(buffer_postcohtable->spin2z);
    self->ra          = PyFloat_FromDouble(buffer_postcohtable->ra);
    self->dec         = PyFloat_FromDouble(buffer_postcohtable->dec);
    self->f_final     = PyFloat_FromDouble(buffer_postcohtable->f_final);
    self->_process_id = PyLong_FromLong(buffer_postcohtable->process_id);
    self->_event_id   = PyLong_FromLong(buffer_postcohtable->event_id);

    self->end_time_sngl = PyArray_SimpleNewFromData(
      2, end_time_dims, NPY_INT, buffer_postcohtable->end_time_sngl);
    self->snglsnr  = PyArray_SimpleNewFromData(1, dims, NPY_FLOAT,
                                               buffer_postcohtable->snglsnr);
    self->coaphase = PyArray_SimpleNewFromData(1, dims, NPY_FLOAT,
                                               buffer_postcohtable->coaphase);
    self->chisq =
      PyArray_SimpleNewFromData(1, dims, NPY_FLOAT, buffer_postcohtable->chisq);
    self->far_sngl    = PyArray_SimpleNewFromData(1, dims, NPY_FLOAT,
                                                  buffer_postcohtable->far_sngl);
    self->far_1w_sngl = PyArray_SimpleNewFromData(
      1, dims, NPY_FLOAT, buffer_postcohtable->far_1w_sngl);
    self->far_1d_sngl = PyArray_SimpleNewFromData(
      1, dims, NPY_FLOAT, buffer_postcohtable->far_1d_sngl);
    self->far_2h_sngl = PyArray_SimpleNewFromData(
      1, dims, NPY_FLOAT, buffer_postcohtable->far_2h_sngl);
    self->deff =
      PyArray_SimpleNewFromData(1, dims, NPY_DOUBLE, buffer_postcohtable->deff);

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

    if (module == NULL) return NULL;
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

    if (PyType_Ready(&postcoh_inspiral_wrapper_type) < 0) return NULL;
    Py_INCREF(&postcoh_inspiral_wrapper_type);

    PyModule_AddObject(module, "PostcohInspiral",
                       (PyObject *)&postcoh_inspiral_wrapper_type);

    if (PyType_Ready(&snr_series_wrapper_type) < 0) return NULL;
    Py_INCREF(&snr_series_wrapper_type);

    PyModule_AddObject(module, "SNRSeries",
                       (PyObject *)&snr_series_wrapper_type);

    if (PyType_Ready(&postcohevent_type) < 0) return NULL;
    Py_INCREF(&postcohevent_type);

    PyModule_AddObject(module, "PostcohEvent", (PyObject *)&postcohevent_type);
}
