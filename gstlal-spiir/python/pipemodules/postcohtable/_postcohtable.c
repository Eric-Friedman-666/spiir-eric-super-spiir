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

#define NPY_NO_DEPRECATED_API NPY_1_23_API_VERSION

// Standard includes
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

// Python includes
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <numpy/ndarrayobject.h>
#include <py3c.h>
#include <py3c/tpflags.h>
#include <structmember.h>

// Our includes
#include <IFOMap.h>
#include <lal/TimeSeries.h>
#include <lal/Units.h>
#include <pipe_macro.h>
#include <postcohtable.h>

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
    COMPLEX8TimeSeries *complex8_snr_series;
} Complex8TimeSeriesWrapper;

static void __del_snr_series__(PyObject *self) {
    Complex8TimeSeriesWrapper *self_typed = (Complex8TimeSeriesWrapper *)self;
    if (self_typed->complex8_snr_series) {
        XLALDestroyCOMPLEX8TimeSeries(self_typed->complex8_snr_series);
    }

    Py_TYPE(self)->tp_free(self);
}

#define GENERIC_SNR_SERIES_GETTER(name, member, wrapper)                       \
    static PyObject *get_##name(Complex8TimeSeriesWrapper *self,               \
                                void *closure) {                               \
        return wrapper(self->complex8_snr_series->member);                     \
    }

GENERIC_SNR_SERIES_GETTER(name, name, PyStr_FromString)
GENERIC_SNR_SERIES_GETTER(epoch_gpsSeconds, epoch.gpsSeconds, PyInt_FromLong)
GENERIC_SNR_SERIES_GETTER(epoch_gpsNanoSeconds,
                          epoch.gpsNanoSeconds,
                          PyInt_FromLong)
GENERIC_SNR_SERIES_GETTER(f0, f0, PyFloat_FromDouble)
GENERIC_SNR_SERIES_GETTER(deltaT, deltaT, PyFloat_FromDouble)
GENERIC_SNR_SERIES_GETTER(data_length, data->length, PyInt_FromLong)

static PyObject *get_sampleUnits(Complex8TimeSeriesWrapper *self,
                                 void *closure) {
    char *s = XLALUnitToString(&self->complex8_snr_series->sampleUnits);
    PyObject *sampleUnits = PyStr_FromString(s);
    XLALFree(s);
    return sampleUnits;
}

static PyObject *get_data(Complex8TimeSeriesWrapper *self, void *closure) {
    npy_intp snr_series_dims[1] = { self->complex8_snr_series->data->length };
    PyObject *data              = PyArray_SimpleNewFromData(
      1, snr_series_dims, NPY_CFLOAT, self->complex8_snr_series->data->data);
    return data;
}

static PyGetSetDef getset_snr_series[] = {
    { "name", (getter)get_name, NULL, "name", NULL },
    { "epoch_gpsSeconds", (getter)get_epoch_gpsSeconds, NULL,
      "epoch_gpsSeconds", NULL },
    { "epoch_gpsNanoSeconds", (getter)get_epoch_gpsNanoSeconds, NULL,
      "epoch_gpsNanoSeconds", NULL },
    { "f0", (getter)get_f0, NULL, "f0", NULL },
    { "deltaT", (getter)get_deltaT, NULL, "deltaT", NULL },
    { "sampleUnits", (getter)get_sampleUnits, NULL, "sampleUnits", NULL },
    { "data_length", (getter)get_data_length, NULL, "data_length", NULL },
    { "data", (getter)get_data, NULL, "data", NULL },
    { NULL } /* Sentinel */
};

static PyTypeObject snr_series_wrapper_type = {
    // clang-format off
  PyVarObject_HEAD_INIT(NULL, 0) // PyVarObject_HEAD_INIT includes a trailing comma
  .tp_basicsize = sizeof(Complex8TimeSeriesWrapper), // clang-format on
    .tp_doc = "SNR Series structure",
    .tp_flags =
      Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_CHECKTYPES,
    .tp_getset  = getset_snr_series,
    .tp_name    = MODULE_NAME ".SNRSeries",
    .tp_dealloc = __del_snr_series__,
};

static Complex8TimeSeriesWrapper *
  new_wrapped_snr_series(COMPLEX8TimeSeries *complex8_snr_series) {
    PyObject *pyModule = PyImport_ImportModule(
      "gstlal_spiir.pipemodules.postcohtable.postcohtable");
    PyObject *wrapped_snr_series_class =
      PyObject_GetAttrString(pyModule, "SNRSeries");

    if (complex8_snr_series && complex8_snr_series->data->length > 0) {
        Complex8TimeSeriesWrapper *wrapped_snr_series =
          (Complex8TimeSeriesWrapper *)PyType_GenericNew(
            (PyTypeObject *)wrapped_snr_series_class, NULL, NULL);
        if (!wrapped_snr_series) {
            PyErr_SetString(
              PyExc_ValueError,
              "Failed to create wrapped snr series: constructor error");
            return NULL;
        }

        wrapped_snr_series->complex8_snr_series = complex8_snr_series;
        return wrapped_snr_series;
    } else {
        PyErr_SetString(PyExc_ValueError,
                        "Failed to create wrapped snr series: "
                        "COMPLEX8TimeSeries must not be null or empty.");
        return NULL;
    }
}

static PyObject *
  new_wrapped_snr_series_list(const PostcohInspiralTable *buffer_postcohtable,
                              bool is_heartbeat) {

    Py_ssize_t num_trigger_ifos = strlen(buffer_postcohtable->ifos) / IFO_LEN;
    PyObject *wrapped_snr_series_list = PyList_New(num_trigger_ifos);
    if (!wrapped_snr_series_list) {
        PyErr_SetString(PyExc_ValueError,
                        "Failed to create wrapped snr series list");
        return NULL;
    }

    if (is_heartbeat) { return wrapped_snr_series_list; }

    for (Py_ssize_t trigger_ifo_id = 0; trigger_ifo_id < num_trigger_ifos;
         trigger_ifo_id++) {
        int ifo_id = -1;
        char ifo_string[IFO_LEN + 1];
        strncpy(ifo_string,
                buffer_postcohtable->ifos + trigger_ifo_id * IFO_LEN, IFO_LEN);
        ifo_string[IFO_LEN] = '\0';
        if (try_get_ifo_id(ifo_string, &ifo_id)) {
            Complex8TimeSeriesWrapper *wrapped_snr_series =
              new_wrapped_snr_series(
                buffer_postcohtable->snr_series_list[ifo_id]);
            if (!wrapped_snr_series) {
                // PyErr set in new_wrapped_snr_series
                return NULL;
            }

            PyList_SetItem(wrapped_snr_series_list, trigger_ifo_id,
                           (PyObject *)wrapped_snr_series);
            if (PyErr_Occurred()) { return NULL; }
        } else {
            PyErr_SetString(
              PyExc_ValueError,
              "Failed to set wrapped snr series list: Invalid ifos string.");
            return NULL;
        }
    }

    return wrapped_snr_series_list;
}

typedef struct {
    PyObject_HEAD
    PostcohInspiralTable postcohtable;
    PyObject *ifos;
    PyObject *pivotal_ifo;
    PyObject *skymap_fname;
    PyObject *end_time_sngl;
    PyObject *snglsnr;
    PyObject *coaphase;
    PyObject *chisq;
    PyObject *far_sngl;
    PyObject *far_1w_sngl;
    PyObject *far_1d_sngl;
    PyObject *far_2h_sngl;
    PyObject *deff;
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
    { "ifos", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, ifos), READONLY,
      "ifos" },
    { "pivotal_ifo", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, pivotal_ifo),
      READONLY, "pivotal_ifo" },
    { "skymap_fname", T_OBJECT_EX,
      offsetof(PostcohInspiralWrapper, skymap_fname), READONLY,
      "skymap_fname" },
    // Not dependent on the number of detectors
    { "end_time", T_INT,
      offsetof(PostcohInspiralWrapper, postcohtable.end_time.gpsSeconds), 0,
      "end_time" },
    { "end_time_ns", T_INT,
      offsetof(PostcohInspiralWrapper, postcohtable.end_time.gpsNanoSeconds), 0,
      "end_time_ns" },
    { "is_background", T_INT,
      offsetof(PostcohInspiralWrapper, postcohtable.is_background), 0,
      "is_background" },
    { "livetime", T_INT,
      offsetof(PostcohInspiralWrapper, postcohtable.livetime), 0, "livetime" },
    { "tmplt_idx", T_INT,
      offsetof(PostcohInspiralWrapper, postcohtable.tmplt_idx), 0,
      "tmplt_idx" },
    { "bankid", T_INT, offsetof(PostcohInspiralWrapper, postcohtable.bankid), 0,
      "bankid" },
    { "pix_idx", T_INT, offsetof(PostcohInspiralWrapper, postcohtable.pix_idx),
      0, "pix_idx" },
    { "cohsnr", T_FLOAT, offsetof(PostcohInspiralWrapper, postcohtable.cohsnr),
      0, "cohsnr" },
    { "nullsnr", T_FLOAT,
      offsetof(PostcohInspiralWrapper, postcohtable.nullsnr), 0, "nullsnr" },
    { "cmbchisq", T_FLOAT,
      offsetof(PostcohInspiralWrapper, postcohtable.cmbchisq), 0, "cmbchisq" },
    { "spearman_pval", T_FLOAT,
      offsetof(PostcohInspiralWrapper, postcohtable.spearman_pval), 0,
      "spearman_pval" },
    { "fap", T_FLOAT, offsetof(PostcohInspiralWrapper, postcohtable.fap), 0,
      "fap" },
    { "far_2h", T_FLOAT, offsetof(PostcohInspiralWrapper, postcohtable.far_2h),
      0, "far_2h" },
    { "far_1d", T_FLOAT, offsetof(PostcohInspiralWrapper, postcohtable.far_1d),
      0, "far_1d" },
    { "far_1w", T_FLOAT, offsetof(PostcohInspiralWrapper, postcohtable.far_1w),
      0, "far_1w" },
    { "far", T_FLOAT, offsetof(PostcohInspiralWrapper, postcohtable.far), 0,
      "far" },
    { "rank", T_DOUBLE, offsetof(PostcohInspiralWrapper, postcohtable.rank), 0,
      "rank" },
    { "template_duration", T_DOUBLE,
      offsetof(PostcohInspiralWrapper, postcohtable.template_duration), 0,
      "template_duration" },
    { "mass1", T_FLOAT, offsetof(PostcohInspiralWrapper, postcohtable.mass1), 0,
      "mass1" },
    { "mass2", T_FLOAT, offsetof(PostcohInspiralWrapper, postcohtable.mass2), 0,
      "mass2" },
    { "mchirp", T_FLOAT, offsetof(PostcohInspiralWrapper, postcohtable.mchirp),
      0, "mchirp" },
    { "mtotal", T_FLOAT, offsetof(PostcohInspiralWrapper, postcohtable.mtotal),
      0, "mtotal" },
    { "eta", T_FLOAT, offsetof(PostcohInspiralWrapper, postcohtable.eta), 0,
      "eta" },
    { "spin1x", T_FLOAT, offsetof(PostcohInspiralWrapper, postcohtable.spin1x),
      0, "spin1x" },
    { "spin1y", T_FLOAT, offsetof(PostcohInspiralWrapper, postcohtable.spin1y),
      0, "spin1y" },
    { "spin1z", T_FLOAT, offsetof(PostcohInspiralWrapper, postcohtable.spin1z),
      0, "spin1z" },
    { "spin2x", T_FLOAT, offsetof(PostcohInspiralWrapper, postcohtable.spin2x),
      0, "spin2x" },
    { "spin2y", T_FLOAT, offsetof(PostcohInspiralWrapper, postcohtable.spin2y),
      0, "spin2y" },
    { "spin2z", T_FLOAT, offsetof(PostcohInspiralWrapper, postcohtable.spin2z),
      0, "spin2z" },
    { "ra", T_DOUBLE, offsetof(PostcohInspiralWrapper, postcohtable.ra), 0,
      "ra" },
    { "dec", T_DOUBLE, offsetof(PostcohInspiralWrapper, postcohtable.dec), 0,
      "dec" },
    { "f_final", T_FLOAT,
      offsetof(PostcohInspiralWrapper, postcohtable.f_final), 0, "f_final" },
    { "process_id", T_LONG,
      offsetof(PostcohInspiralWrapper, postcohtable.process_id), 0,
      "process_id" },
    { "event_id", T_LONG,
      offsetof(PostcohInspiralWrapper, postcohtable.event_id), 0, "event_id" },

    // Things that are done single detector are ndarrays
    { "_end_time_sngl", T_OBJECT_EX,
      offsetof(PostcohInspiralWrapper, end_time_sngl), READONLY,
      "_end_time_sngl" },
    { "snglsnr", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, snglsnr),
      READONLY, "snglsnr" },
    { "coaphase", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, coaphase),
      READONLY, "coaphase" },
    { "chisq", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, chisq), READONLY,
      "chisq" },
    { "far_sngl", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, far_sngl),
      READONLY, "far_sngl" },
    { "far_1w_sngl", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, far_1w_sngl),
      READONLY, "far_1w_sngl" },
    { "far_1d_sngl", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, far_1d_sngl),
      READONLY, "far_1d_sngl" },
    { "far_2h_sngl", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, far_2h_sngl),
      READONLY, "far_2h_sngl" },
    { "deff", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, deff), READONLY,
      "deff" },
    { NULL },
};

static PyTypeObject postcoh_inspiral_wrapper_type = {
    // clang-format off
  PyVarObject_HEAD_INIT(NULL, 0) // PyVarObject_HEAD_INIT includes a trailing comma
  .tp_basicsize = sizeof(PostcohInspiralWrapper), // clang-format on
    .tp_doc = "LAL's PostcohInspiral structure",
    .tp_flags =
      Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_CHECKTYPES,
    .tp_members = members_postcohinspiral,
    .tp_name    = MODULE_NAME ".PostcohInspiral",
    .tp_dealloc = __del_postcohinspiral__,
};

static PostcohInspiralWrapper *
  new_wrapped_postcohtable(const PostcohInspiralTable *buffer_postcohtable) {
    PyObject *pyModule = PyImport_ImportModule(
      "gstlal_spiir.pipemodules.postcohtable.postcohtable");
    PyObject *wrapped_postcohtable_class =
      PyObject_GetAttrString(pyModule, "PostcohInspiral");

    PostcohInspiralWrapper *self = (PostcohInspiralWrapper *)PyType_GenericNew(
      (PyTypeObject *)wrapped_postcohtable_class, NULL, NULL);
    if (!self) {
        PyErr_SetString(PyExc_ValueError, "new wrapped_postcohtable error");
        return NULL;
    }

    self->postcohtable = *buffer_postcohtable;

    self->ifos         = PyStr_FromString(self->postcohtable.ifos);
    self->pivotal_ifo  = PyStr_FromString(self->postcohtable.pivotal_ifo);
    self->skymap_fname = PyStr_FromString(self->postcohtable.skymap_fname);

    npy_intp end_time_dims[2] = { MAX_NIFO, 2 };
    self->end_time_sngl       = PyArray_SimpleNewFromData(
      2, end_time_dims, NPY_INT, self->postcohtable.end_time_sngl);

    npy_intp dims[1] = { MAX_NIFO };
    self->snglsnr =
      PyArray_SimpleNewFromData(1, dims, NPY_FLOAT, self->postcohtable.snglsnr);
    self->coaphase = PyArray_SimpleNewFromData(1, dims, NPY_FLOAT,
                                               self->postcohtable.coaphase);
    self->chisq =
      PyArray_SimpleNewFromData(1, dims, NPY_FLOAT, self->postcohtable.chisq);
    self->far_sngl    = PyArray_SimpleNewFromData(1, dims, NPY_FLOAT,
                                                  self->postcohtable.far_sngl);
    self->far_1w_sngl = PyArray_SimpleNewFromData(
      1, dims, NPY_FLOAT, self->postcohtable.far_1w_sngl);
    self->far_1d_sngl = PyArray_SimpleNewFromData(
      1, dims, NPY_FLOAT, self->postcohtable.far_1d_sngl);
    self->far_2h_sngl = PyArray_SimpleNewFromData(
      1, dims, NPY_FLOAT, self->postcohtable.far_2h_sngl);
    self->deff =
      PyArray_SimpleNewFromData(1, dims, NPY_DOUBLE, self->postcohtable.deff);

    if (PyErr_Occurred()) { return NULL; }

    return self;
}

typedef struct {
    PyObject_HEAD
    PostcohInspiralWrapper *wrapped_postcohtable;
    PyObject *wrapped_snr_series_list;
} PostcohTrigger;

static void __del_postcohtrigger__(PyObject *self) {
    PostcohTrigger *self_typed = (PostcohTrigger *)self;

    Py_XDECREF(self_typed->wrapped_postcohtable);
    Py_XDECREF(self_typed->wrapped_snr_series_list);

    Py_TYPE(self)->tp_free(self);
}

static PyMemberDef members_postcohtrigger[] = {
    { "postcoh_inspiral", T_OBJECT_EX,
      offsetof(PostcohTrigger, wrapped_postcohtable), READONLY,
      "postcoh_inspiral" },
    { "snr_series_list", T_OBJECT_EX,
      offsetof(PostcohTrigger, wrapped_snr_series_list), READONLY,
      "snr_series_list" },
    { NULL },
};

static PyTypeObject postcohtrigger_type = {
    // clang-format off
  PyVarObject_HEAD_INIT(NULL, 0) // PyVarObject_HEAD_INIT includes a trailing comma
  .tp_basicsize = sizeof(PostcohTrigger), // clang-format on
    .tp_doc = "Postcoh Trigger structure",
    .tp_flags =
      Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_CHECKTYPES,
    .tp_members = members_postcohtrigger,
    .tp_name    = MODULE_NAME ".PostcohTrigger",
    .tp_dealloc = __del_postcohtrigger__,
};

static PyObject *
  new_postcohtrigger(const PostcohInspiralTable *buffer_postcohtable,
                     bool is_heartbeat) {
    PyObject *pyModule = PyImport_ImportModule(
      "gstlal_spiir.pipemodules.postcohtable.postcohtable");
    PyObject *postcohtrigger_class =
      PyObject_GetAttrString(pyModule, "PostcohTrigger");

    PostcohTrigger *self = (PostcohTrigger *)PyType_GenericNew(
      (PyTypeObject *)postcohtrigger_class, NULL, NULL);
    if (!self) {
        PyErr_SetString(PyExc_ValueError, "new postcoh trigger error");
        return NULL;
    }

    PostcohInspiralWrapper *wrapped_postcohtable =
      new_wrapped_postcohtable(buffer_postcohtable);
    if (!wrapped_postcohtable) {
        Py_DECREF(self);
        return NULL;
    }

    self->wrapped_postcohtable = wrapped_postcohtable;

    PyObject *wrapped_snr_series_list =
      new_wrapped_snr_series_list(buffer_postcohtable, is_heartbeat);
    if (!wrapped_snr_series_list) {
        Py_DECREF(self);
        return NULL;
    }

    self->wrapped_snr_series_list = wrapped_snr_series_list;
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

    if (PyErr_Occurred()) { return NULL; }

    if (!PyArg_ParseTuple(args, "y#", &data, &length)) { return NULL; }

    Py_ssize_t num_triggers = length / sizeof(PostcohInspiralTable);
    if ((Py_ssize_t)(num_triggers * sizeof(PostcohInspiralTable)) != length) {
        PyErr_SetString(PyExc_ValueError, "Buffer does not contain an integer "
                                          "quantity of PostcohInspiralTable");
        return NULL;
    }

    PyObject *trigger_list = PyList_New(num_triggers);
    if (!trigger_list) {
        PyErr_SetString(PyExc_ValueError, "Failed to create trigger list");
        return NULL;
    }

    for (Py_ssize_t i = 0; i < num_triggers; i++) {
        /* memcpy postcoh postcohtable */
        const PostcohInspiralTable *buffer_postcohtable =
          (const PostcohInspiralTable *)data + i;

        // The first trigger is a heartbeat that uses a limitted set of fields
        PyObject *postcohtrigger =
          new_postcohtrigger(buffer_postcohtable, i == 0);

        if (!postcohtrigger) {
            Py_DECREF(trigger_list);
            return NULL;
        }

        PyList_SetItem(trigger_list, i, postcohtrigger);
        if (PyErr_Occurred()) { return NULL; }
    }

    if (PyErr_Occurred()) { return NULL; }

    return trigger_list;
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

PyMODINIT_FUNC PyInit__postcohtable(void) {
    static struct PyModuleDef moduledef = {
        PyModuleDef_HEAD_INIT,
        .m_name    = MODULE_NAME,
        .m_doc     = "Wrapper for LAL's PostcohInspiralTable type.",
        .m_methods = methods,
    };

    PyObject *module = PyModule_Create(&moduledef);
    assert(!PyErr_Occurred());

    import_array();
    if (PyErr_Occurred()) { return NULL; }

    if (PyType_Ready(&postcoh_inspiral_wrapper_type) < 0) return NULL;
    Py_INCREF(&postcoh_inspiral_wrapper_type);

    PyModule_AddObject(module, "PostcohInspiral",
                       (PyObject *)&postcoh_inspiral_wrapper_type);

    if (PyType_Ready(&snr_series_wrapper_type) < 0) return NULL;
    Py_INCREF(&snr_series_wrapper_type);

    PyModule_AddObject(module, "SNRSeries",
                       (PyObject *)&snr_series_wrapper_type);

    if (PyType_Ready(&postcohtrigger_type) < 0) return NULL;
    Py_INCREF(&postcohtrigger_type);

    PyModule_AddObject(module, "PostcohTrigger",
                       (PyObject *)&postcohtrigger_type);

    return module;
}
