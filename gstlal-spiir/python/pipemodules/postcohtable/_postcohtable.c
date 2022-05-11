/*
 * Copyright (C) 2010  Kipp Cannon
 *
 * This program is free software; you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the
 * Free Software Foundation; either version 2 of the License, or (at your
 * option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
 */

/*
 * ============================================================================
 *
 *                                  Preamble
 *
 * ============================================================================
 */

#include <string.h>
#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION

#define PY_SSIZE_T_CLEAN
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
 *                                    Type
 *
 * ============================================================================
 */

/*
 * Cached ID types
 */

typedef struct {
    PyObject_HEAD;
    PostcohInspiralTable row;
    COMPLEX8TimeSeries *snr;
    PyObject *end_time_sngl;
    PyObject *snglsnr;
    PyObject *coaphase;
    PyObject *chisq;
    PyObject *far_sngl;
    PyObject *far_1w_sngl;
    PyObject *far_1d_sngl;
    PyObject *far_2h_sngl;
    PyObject *deff;
} gstlal_GSTLALPostcohInspiral;

// static PyObject *row_event_id_type = NULL;
// static PyObject *process_id_type = NULL;

/*
 * Member access
 */

static PyMemberDef members[] = {
    // Not dependent on the number of detectors
    { "end_time", T_INT,
      offsetof(gstlal_GSTLALPostcohInspiral, row.end_time.gpsSeconds), 0,
      "end_time" },
    { "end_time_ns", T_INT,
      offsetof(gstlal_GSTLALPostcohInspiral, row.end_time.gpsNanoSeconds), 0,
      "end_time_ns" },
    { "is_background", T_INT,
      offsetof(gstlal_GSTLALPostcohInspiral, row.is_background), 0,
      "is_background" },
    { "livetime", T_INT, offsetof(gstlal_GSTLALPostcohInspiral, row.livetime),
      0, "livetime" },
    { "tmplt_idx", T_INT, offsetof(gstlal_GSTLALPostcohInspiral, row.tmplt_idx),
      0, "tmplt_idx" },
    { "bankid", T_INT, offsetof(gstlal_GSTLALPostcohInspiral, row.bankid), 0,
      "bankid" },
    { "pix_idx", T_INT, offsetof(gstlal_GSTLALPostcohInspiral, row.pix_idx), 0,
      "pix_idx" },
    { "cohsnr", T_FLOAT, offsetof(gstlal_GSTLALPostcohInspiral, row.cohsnr), 0,
      "cohsnr" },
    { "nullsnr", T_FLOAT, offsetof(gstlal_GSTLALPostcohInspiral, row.nullsnr),
      0, "nullsnr" },
    { "cmbchisq", T_FLOAT, offsetof(gstlal_GSTLALPostcohInspiral, row.cmbchisq),
      0, "cmbchisq" },
    { "spearman_pval", T_FLOAT,
      offsetof(gstlal_GSTLALPostcohInspiral, row.spearman_pval), 0,
      "spearman_pval" },
    { "fap", T_FLOAT, offsetof(gstlal_GSTLALPostcohInspiral, row.fap), 0,
      "fap" },
    { "far_2h", T_FLOAT, offsetof(gstlal_GSTLALPostcohInspiral, row.far_2h), 0,
      "far_2h" },
    { "far_1d", T_FLOAT, offsetof(gstlal_GSTLALPostcohInspiral, row.far_1d), 0,
      "far_1d" },
    { "far_1w", T_FLOAT, offsetof(gstlal_GSTLALPostcohInspiral, row.far_1w), 0,
      "far_1w" },
    { "far", T_FLOAT, offsetof(gstlal_GSTLALPostcohInspiral, row.far), 0,
      "far" },
    { "rank", T_DOUBLE, offsetof(gstlal_GSTLALPostcohInspiral, row.rank), 0,
      "rank" },
    { "template_duration", T_DOUBLE,
      offsetof(gstlal_GSTLALPostcohInspiral, row.template_duration), 0,
      "template_duration" },
    { "mass1", T_FLOAT, offsetof(gstlal_GSTLALPostcohInspiral, row.mass1), 0,
      "mass1" },
    { "mass2", T_FLOAT, offsetof(gstlal_GSTLALPostcohInspiral, row.mass2), 0,
      "mass2" },
    { "mchirp", T_FLOAT, offsetof(gstlal_GSTLALPostcohInspiral, row.mchirp), 0,
      "mchirp" },
    { "mtotal", T_FLOAT, offsetof(gstlal_GSTLALPostcohInspiral, row.mtotal), 0,
      "mtotal" },
    { "eta", T_FLOAT, offsetof(gstlal_GSTLALPostcohInspiral, row.eta), 0,
      "eta" },
    { "spin1x", T_FLOAT, offsetof(gstlal_GSTLALPostcohInspiral, row.spin1x), 0,
      "spin1x" },
    { "spin1y", T_FLOAT, offsetof(gstlal_GSTLALPostcohInspiral, row.spin1y), 0,
      "spin1y" },
    { "spin1z", T_FLOAT, offsetof(gstlal_GSTLALPostcohInspiral, row.spin1z), 0,
      "spin1z" },
    { "spin2x", T_FLOAT, offsetof(gstlal_GSTLALPostcohInspiral, row.spin2x), 0,
      "spin2x" },
    { "spin2y", T_FLOAT, offsetof(gstlal_GSTLALPostcohInspiral, row.spin2y), 0,
      "spin2y" },
    { "spin2z", T_FLOAT, offsetof(gstlal_GSTLALPostcohInspiral, row.spin2z), 0,
      "spin2z" },
    { "ra", T_DOUBLE, offsetof(gstlal_GSTLALPostcohInspiral, row.ra), 0, "ra" },
    { "dec", T_DOUBLE, offsetof(gstlal_GSTLALPostcohInspiral, row.dec), 0,
      "dec" },
    { "f_final", T_FLOAT, offsetof(gstlal_GSTLALPostcohInspiral, row.f_final),
      0, "f_final" },
    { "_process_id", T_LONG,
      offsetof(gstlal_GSTLALPostcohInspiral, row.process_id), 0,
      "process_id (long)" },
    { "_event_id", T_LONG, offsetof(gstlal_GSTLALPostcohInspiral, row.event_id),
      0, "event_id (long)" },

    // Things that are done single detector are ndarrays
    { "end_time_sngl", T_OBJECT_EX,
      offsetof(gstlal_GSTLALPostcohInspiral, end_time_sngl), READONLY,
      "end_time_sngl" },
    { "snglsnr", T_OBJECT_EX, offsetof(gstlal_GSTLALPostcohInspiral, snglsnr),
      READONLY, "snglsnr" },
    { "coaphase", T_OBJECT_EX, offsetof(gstlal_GSTLALPostcohInspiral, coaphase),
      READONLY, "coaphase" },
    { "chisq", T_OBJECT_EX, offsetof(gstlal_GSTLALPostcohInspiral, chisq), READONLY,
      "chisq" },
    { "far_sngl", T_OBJECT_EX, offsetof(gstlal_GSTLALPostcohInspiral, far_sngl),
      READONLY, "far_sngl" },
    { "far_1w_sngl", T_OBJECT_EX,
      offsetof(gstlal_GSTLALPostcohInspiral, far_1w_sngl), READONLY, "far_1w_sngl" },
    { "far_1d_sngl", T_OBJECT_EX,
      offsetof(gstlal_GSTLALPostcohInspiral, far_1d_sngl), READONLY, "far_1d_sngl" },
    { "far_2h_sngl", T_OBJECT_EX,
      offsetof(gstlal_GSTLALPostcohInspiral, far_2h_sngl), READONLY, "far_2h_sngl" },
    { "deff", T_OBJECT_EX, offsetof(gstlal_GSTLALPostcohInspiral, deff), READONLY,
      "deff" },
    { NULL },
};

struct py_interop__string_closure {
    Py_ssize_t offset;
    Py_ssize_t length;
};

static PyObject *py_interop__string_get(PyObject *obj, void *closure) {
    assert(obj);
    const struct py_interop__string_closure *closure_typed = closure;

    char *field = (char*)((void *)obj + closure_typed->offset);
    assert(memchr(field, '\0', closure_typed->length));

    return PyString_FromString(field);
}

static int py_interop__string_set(PyObject *obj, PyObject *value, void *closure) {
    assert(obj);
    assert(value); // TODO: Could set an error instead, this isn't supposed to be unreachable
    const struct py_interop__string_closure *closure_typed = closure;
    char *value_as_string = PyString_AsString(value);
    if (PyErr_Occurred()) return -1;
    if ((Py_ssize_t)memchr(value_as_string, '\0', closure_typed->length) >= closure_typed->length) {
        PyErr_Format(PyExc_ValueError, "string too long \'%s\'", value_as_string);
        return -1;
    }

    char *field = (char *)((void *)obj + closure_typed->offset);
    assert(memchr(field, '\0', closure_typed->length));

    // TODO: replace strcpy with strncpy
    strcpy(field, value_as_string);
    return 0;
}

static PyObject *py_interop__snr_series_get(PyObject *obj, void *closure) {
    assert(obj);
    COMPLEX8TimeSeries *snr = ((gstlal_GSTLALPostcohInspiral *)obj)->snr;
    const char *name        = closure;

    if (!snr) {
        PyErr_SetString(PyExc_ValueError, "no snr time series available");
        return NULL;
    }
    if (!strcmp(name, "_snr_name")) {
        return PyString_FromString(snr->name);
    } else if (!strcmp(name, "_snr_epoch_gpsSeconds")) {
        return PyInt_FromLong(snr->epoch.gpsSeconds);
    } else if (!strcmp(name, "_snr_epoch_gpsNanoSeconds")) {
        return PyInt_FromLong(snr->epoch.gpsNanoSeconds);
    } else if (!strcmp(name, "_snr_f0")) {
        return PyFloat_FromDouble(snr->f0);
    } else if (!strcmp(name, "_snr_deltaT")) {
        return PyFloat_FromDouble(snr->deltaT);
    } else if (!strcmp(name, "_snr_sampleUnits")) {
        char *s          = XLALUnitToString(&snr->sampleUnits);
        PyObject *result = PyString_FromString(s);
        XLALFree(s);
        return result;
    } else if (!strcmp(name, "_snr_data_length")) {
        return PyInt_FromLong(snr->data->length);
    } else if (!strcmp(name, "_snr_data")) {
        npy_intp dims[] = { snr->data->length };
        PyObject *array =
          PyArray_SimpleNewFromData(1, dims, NPY_CFLOAT, snr->data->data);
        if (!array) return NULL;
        Py_INCREF(obj);
        PyArray_SetBaseObject((PyArrayObject *)array, obj);
        return array;
    }
    PyErr_BadArgument();
    return NULL;
}

#define NUM_SINGLE_FIELDS 11
#define NUM_FIELDS_PER_IFO 10
// TODO: Move into prepare_getset
static struct PyGetSetDef getset[NUM_SINGLE_FIELDS + NUM_FIELDS_PER_IFO * MAX_NIFO + 1] = {
    { "ifos", py_interop__string_get, py_interop__string_set, "ifos",
      &(struct py_interop__string_closure) {
        offsetof(gstlal_GSTLALPostcohInspiral, row.ifos), MAX_ALLIFO_LEN } },
    { "pivotal_ifo", py_interop__string_get, py_interop__string_set,
      "pivotal_ifo",
      &(struct py_interop__string_closure) {
        offsetof(gstlal_GSTLALPostcohInspiral, row.pivotal_ifo),
        MAX_IFO_LEN } },
    { "skymap_fname", py_interop__string_get, py_interop__string_set,
      "skymap_fname",
      &(struct py_interop__string_closure) {
        offsetof(gstlal_GSTLALPostcohInspiral, row.skymap_fname),
        MAX_SKYMAP_FNAME_LEN } },
    { "_snr_name", py_interop__snr_series_get, NULL, ".snr.name", "_snr_name" },
    { "_snr_epoch_gpsSeconds", py_interop__snr_series_get, NULL, ".snr.epoch.gpsSeconds",
      "_snr_epoch_gpsSeconds" },
    { "_snr_epoch_gpsNanoSeconds", py_interop__snr_series_get, NULL,
      ".snr.epoch.gpsNanoSeconds", "_snr_epoch_gpsNanoSeconds" },
    { "_snr_f0", py_interop__snr_series_get, NULL, ".snr.f0", "_snr_f0" },
    { "_snr_deltaT", py_interop__snr_series_get, NULL, ".snr.deltaT", "_snr_deltaT" },
    { "_snr_sampleUnits", py_interop__snr_series_get, NULL, ".snr.sampleUnits",
      "_snr_sampleUnits" },
    { "_snr_data_length", py_interop__snr_series_get, NULL, ".snr.data.length",
      "_snr_data_length" },
    { "_snr_data", py_interop__snr_series_get, NULL, ".snr.data", "_snr_data" },

    { NULL }
};

static Py_ssize_t closures[NUM_SINGLE_FIELDS + NUM_FIELDS_PER_IFO * MAX_NIFO];

static PyObject *py_interop__double_array_get(PyObject *obj, void *closure) {
    assert(obj);
    const Py_ssize_t *offset = closure;

    double *field = (double *)((void *)obj + *offset);
    return PyFloat_FromDouble(*field);
}

static int py_interop__double_array_set(PyObject *obj, PyObject *value, void *closure) {
    assert(obj);
    assert(value);
    const Py_ssize_t *offset = closure;
    double value_as_double  = PyFloat_AsDouble(value);
    if (PyErr_Occurred()) return -1;

    double *field = (double *)((void *)obj + *offset);
    *field = value_as_double;
    return 0;
}

static PyObject *py_interop__float_array_get(PyObject *obj, void *closure) {
    assert(obj);
    const Py_ssize_t *offset = closure;

    float *field = (float *)((void *)obj + *offset);
    return PyFloat_FromDouble((double)*field);
}

static int py_interop__float_array_set(PyObject *obj, PyObject *value, void *closure) {
    assert(obj);
    assert(value);
    const Py_ssize_t *offset = closure;
    double value_as_double  = PyFloat_AsDouble(value);
    if (PyErr_Occurred()) return -1;

    float *field = (float *)((void *)obj + *offset);
    *field = (float)value_as_double;
    return 0;
}

static PyObject *py_interop__int_array_get(PyObject *obj, void *closure) {
    assert(obj);
    const Py_ssize_t *offset = closure;
    
    int *field = (int *)((void *)obj + *offset);
    return PyInt_FromLong((long)*field);
}

static int py_interop__int_array_set(PyObject *obj, PyObject *value, void *closure) {
    assert(obj);
    assert(value);
    const Py_ssize_t *offset = closure;
    int value_as_long       = (int)PyInt_AsLong(value);
    if (PyErr_Occurred()) return -1;

    int *field = (int *)((void *)obj + *offset);
    *field = (int)value_as_long;
    return 0;
}

// Allocate a 2D array of max size for names
//#define MAX_NAME_LENGTH 15
//char[][] names = char[SINGLE + 10 * MAX_NIFO][MAX_POSTCOHTABLE_NAME_LENGTH]
// TODO: Replace all mallocs with static memory.
void prepare_getset() {
    int cur_ifo_field = 0;
    for (int i = 0; i < MAX_NIFO; ++i) {
        // These names aer unclear (move to function before renaming)
        // var, name, IFOMap[i] (could move that to a function)
        char *var  = "chisq_";
        // Rearrange IFOMap and var here for clarity
        char *name = (char *)malloc(strlen(IFOMap[i]) + strlen(var) + 1);
        strcpy(name, var);
        strcat(name, IFOMap[i]);
        closures[cur_ifo_field] = offsetof(gstlal_GSTLALPostcohInspiral, row.chisq[i]);
        PyGetSetDef def  = { name, py_interop__float_array_get, py_interop__float_array_set,
                            name, &closures[cur_ifo_field] };
        getset[NUM_SINGLE_FIELDS + cur_ifo_field++] = def;

        var          = "snglsnr_";
        name         = (char *)malloc(strlen(IFOMap[i]) + strlen(var) + 1);
        strcpy(name, var);
        strcat(name, IFOMap[i]);
        def.name         = name;
        def.doc          = name;
        closures[cur_ifo_field] = offsetof(gstlal_GSTLALPostcohInspiral, row.snglsnr[i]);
        def.closure      = &closures[cur_ifo_field];
        getset[NUM_SINGLE_FIELDS + cur_ifo_field++] = def;

        var          = "coaphase_";
        name         = (char *)malloc(strlen(IFOMap[i]) + strlen(var) + 1);
        strcpy(name, var);
        strcat(name, IFOMap[i]);
        def.name         = name;
        def.doc          = name;
        closures[cur_ifo_field] = offsetof(gstlal_GSTLALPostcohInspiral, row.coaphase[i]);
        def.closure      = &closures[cur_ifo_field];
        getset[NUM_SINGLE_FIELDS + cur_ifo_field++] = def;

        var          = "far_sngl_";
        name         = (char *)malloc(strlen(IFOMap[i]) + strlen(var) + 1);
        strcpy(name, var);
        strcat(name, IFOMap[i]);
        def.name         = name;
        def.doc          = name;
        closures[cur_ifo_field] = offsetof(gstlal_GSTLALPostcohInspiral, row.far_sngl[i]);
        def.closure      = &closures[cur_ifo_field];
        getset[NUM_SINGLE_FIELDS + cur_ifo_field++] = def;

        var          = "far_1d_sngl_";
        name         = (char *)malloc(strlen(IFOMap[i]) + strlen(var) + 1);
        strcpy(name, var);
        strcat(name, IFOMap[i]);
        def.name         = name;
        def.doc          = name;
        closures[cur_ifo_field] = offsetof(gstlal_GSTLALPostcohInspiral, row.far_1d_sngl[i]);
        def.closure      = &closures[cur_ifo_field];
        getset[NUM_SINGLE_FIELDS + cur_ifo_field++] = def;

        var          = "far_1w_sngl_";
        name         = (char *)malloc(strlen(IFOMap[i]) + strlen(var) + 1);
        strcpy(name, var);
        strcat(name, IFOMap[i]);
        def.name         = name;
        def.doc          = name;
        closures[cur_ifo_field] = offsetof(gstlal_GSTLALPostcohInspiral, row.far_1w_sngl[i]);
        def.closure      = &closures[cur_ifo_field];
        getset[NUM_SINGLE_FIELDS + cur_ifo_field++] = def;

        var          = "far_2h_sngl_";
        name         = (char *)malloc(strlen(IFOMap[i]) + strlen(var) + 1);
        strcpy(name, var);
        strcat(name, IFOMap[i]);
        def.name         = name;
        def.doc          = name;
        closures[cur_ifo_field] = offsetof(gstlal_GSTLALPostcohInspiral, row.far_2h_sngl[i]);
        def.closure      = &closures[cur_ifo_field];
        getset[NUM_SINGLE_FIELDS + cur_ifo_field++] = def;

        var          = "deff_";
        name         = (char *)malloc(strlen(IFOMap[i]) + strlen(var) + 1);
        strcpy(name, var);
        strcat(name, IFOMap[i]);
        def.name         = name;
        def.get          = py_interop__double_array_get;
        def.set          = py_interop__double_array_set;
        def.doc          = name;
        closures[cur_ifo_field] = offsetof(gstlal_GSTLALPostcohInspiral, row.deff[i]);
        def.closure      = &closures[cur_ifo_field];
        getset[NUM_SINGLE_FIELDS + cur_ifo_field++] = def;

        var  = "end_time_sngl_";
        name = (char *)malloc(strlen(IFOMap[i]) + strlen(var) + 1);
        strcpy(name, var);
        strcat(name, IFOMap[i]);
        def.name         = name;
        def.get          = py_interop__int_array_get;
        def.set          = py_interop__int_array_set;
        def.doc          = name;
        closures[cur_ifo_field] = offsetof(gstlal_GSTLALPostcohInspiral, row.end_time_sngl[i]);
        def.closure      = &closures[cur_ifo_field];
        getset[NUM_SINGLE_FIELDS + cur_ifo_field++] = def;

        var  = "end_time_ns_sngl_";
        name = (char *)malloc(strlen(IFOMap[i]) + strlen(var) + 1);
        strcpy(name, var);
        strcat(name, IFOMap[i]);
        def.name         = name;
        def.get          = py_interop__int_array_get;
        def.set          = py_interop__int_array_set;
        def.doc          = name;
        closures[cur_ifo_field] = offsetof(gstlal_GSTLALPostcohInspiral, row.end_time_sngl[i]) + offsetof(LIGOTimeGPS, gpsNanoSeconds);
        def.closure      = &closures[cur_ifo_field];
        getset[NUM_SINGLE_FIELDS + cur_ifo_field++] = def;
    }
    PyGetSetDef def = { NULL };
    getset[NUM_SINGLE_FIELDS + cur_ifo_field]  = def;
}

// static Py_ssize_t getreadbuffer(PyObject *self, Py_ssize_t segment, void
// **ptrptr)
//{
//	if(segment) {
//		PyErr_SetString(PyExc_SystemError, "bad segment");
//		return -1;
//	}
//	*ptrptr = &((gstlal_GSTLALPostcohInspiral*)self)->row;
//	return sizeof(((gstlal_GSTLALPostcohInspiral*)self)->row);
//}
//
//
// static Py_ssize_t getsegcount(PyObject *self, Py_ssize_t *lenp)
//{
//	if(lenp)
//		*lenp = sizeof(((gstlal_GSTLALPostcohInspiral*)self)->row);
//	return 1;
//}
//
//
// static PyBufferProcs as_buffer = {
//	.bf_getreadbuffer = getreadbuffer,
//	.bf_getsegcount = getsegcount,
//	.bf_getwritebuffer = NULL,
//	.bf_getcharbuffer = NULL
//};
//

/*
 * Methods
 */

static PyObject *__new__(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    gstlal_GSTLALPostcohInspiral *instance =
      (gstlal_GSTLALPostcohInspiral *)PyType_GenericNew(type, args, kwds);

    if (!instance) return NULL;

    /* link the event_id pointer in the row table structure
     * to the event_id structure */
    // new->row->event_id = new->event_id_i;

    // new->process_id_i = 0;
    // new->event_id_i = 0;

    /* done */
    return (PyObject *)instance;
}

static void __del__(PyObject *self) {
    gstlal_GSTLALPostcohInspiral *self_typed =
      (gstlal_GSTLALPostcohInspiral *)self;
    if (self_typed->snr) XLALDestroyCOMPLEX8TimeSeries(self_typed->snr);
    Py_DECREF(self_typed->end_time_sngl);
    Py_DECREF(self_typed->snglsnr);
    Py_DECREF(self_typed->coaphase);
    Py_DECREF(self_typed->chisq);
    Py_DECREF(self_typed->far_sngl);
    Py_DECREF(self_typed->far_1w_sngl);
    Py_DECREF(self_typed->far_1d_sngl);
    Py_DECREF(self_typed->far_2h_sngl);
    Py_DECREF(self_typed->deff);
    Py_TYPE(self)->tp_free(self);
}

static PyObject *from_buffer(PyObject *cls, PyObject *args) {
    const char *data;
    Py_ssize_t length;
    PyObject *result;
    npy_intp dims[1]          = { MAX_NIFO };
    npy_intp end_time_dims[2] = { 2, MAX_NIFO };

    if (!PyArg_ParseTuple(args, "s#", (const char **)&data, &length))
        return NULL;
    const char *const end = data + length;

    result = PyList_New(0);

    if (!result) return NULL;

    while (data < end) {
        PyObject *item = PyType_GenericNew((PyTypeObject *)cls, NULL, NULL);
        if (!item) {
            Py_DECREF(result);
            return NULL;
        }
        /* memcpy postcoh row */
        const PostcohInspiralTable *gstlal_postcohinspiral =
          (const PostcohInspiralTable *)data;
        data += sizeof(*gstlal_postcohinspiral);
        /* if the data read in is less then expected amount */
        if (data > end) {
            Py_DECREF(item);
            Py_DECREF(result);
            PyErr_SetString(PyExc_ValueError,
                            "buffer overrun while copying postcoh row");
            return NULL;
        }

        gstlal_GSTLALPostcohInspiral *item_typed =
          (gstlal_GSTLALPostcohInspiral *)item;

        item_typed->row = *gstlal_postcohinspiral;

        item_typed->end_time_sngl = PyArray_SimpleNewFromData(
          2, end_time_dims, NPY_INT, item_typed->row.end_time_sngl);
        item_typed->snglsnr = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, item_typed->row.snglsnr);
        item_typed->coaphase = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, item_typed->row.coaphase);
        item_typed->chisq =
          PyArray_SimpleNewFromData(1, dims, NPY_FLOAT, item_typed->row.chisq);
        item_typed->far_sngl = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, item_typed->row.far_sngl);
        item_typed->far_1w_sngl = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, item_typed->row.far_1w_sngl);
        item_typed->far_1d_sngl = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, item_typed->row.far_1d_sngl);
        item_typed->far_2h_sngl = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, item_typed->row.far_2h_sngl);
        item_typed->deff =
          PyArray_SimpleNewFromData(1, dims, NPY_DOUBLE, item_typed->row.deff);

        /* duplicate the SNR time series if we have length? */
        if (item_typed->row.snr_length) {
            const size_t nbytes = sizeof(item_typed->row.snr[0])
                                  * item_typed->row.snr_length;
            if (data + nbytes > end) {
                Py_DECREF(item);
                Py_DECREF(result);
                PyErr_SetString(PyExc_ValueError,
                                "buffer overrun while copying SNR time series");
                return NULL;
            }
            COMPLEX8TimeSeries *series = XLALCreateCOMPLEX8TimeSeries(
              "snr", &item_typed->row.epoch, 0.,
              item_typed->row.deltaT, &lalDimensionlessUnit,
              item_typed->row.snr_length);
            if (!series) {
                Py_DECREF(item);
                Py_DECREF(result);
                PyErr_SetString(PyExc_MemoryError, "out of memory");
                return NULL;
            }
            memcpy(series->data->data, item_typed->row.snr, nbytes);
            data += nbytes;
            item_typed->snr = series;
        } else
            item_typed->snr = NULL;

        if (PyList_Append(result, item)) printf("append failure");
        Py_DECREF(item);
    }

    if (data != end) {
        Py_DECREF(result);
        PyErr_SetString(PyExc_ValueError, "did not consume entire buffer");
        return NULL;
    }

    PyObject *tuple = PyList_AsTuple(result);
    Py_DECREF(result);
    return tuple;
}

static struct PyMethodDef methods[] = {
    { "from_buffer", from_buffer, METH_VARARGS | METH_CLASS,
      "Construct a tuple of PostcohInspiralTable objects from a buffer object. "
      " The buffer is interpreted as a C array of PostcohInspiralTable "
      "structures." },
    {
      NULL,
    }
};

/*
 * Type
 */

static PyTypeObject gstlal_GSTLALPostcohInspiral_Type = {
    PyObject_HEAD_INIT(NULL).tp_basicsize =
      sizeof(gstlal_GSTLALPostcohInspiral),
    .tp_doc = "LAL's PostcohInspiral structure",
    .tp_flags =
      Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_CHECKTYPES,
    .tp_members = members,
    .tp_methods = methods,
    .tp_getset  = getset,
    .tp_name    = MODULE_NAME ".GSTLALPostcohInspiral",
    .tp_new     = __new__,
    .tp_dealloc = __del__,
};

/*
 * ============================================================================
 *
 *                            Module Registration
 *
 * ============================================================================
 */

PyMODINIT_FUNC init_postcohtable(void) {
    PyObject *module = Py_InitModule3(
      MODULE_NAME, NULL, "Wrapper for LAL's PostcohInspiralTable type.");

    prepare_getset();
    import_array();

    PyObject *ifo_map = PyList_New(MAX_NIFO);
    Py_INCREF(ifo_map);
    for (int i = 0; i < MAX_NIFO; ++i) {
        PyObject *str =
          PyString_FromStringAndSize(IFOMap[i], strlen(IFOMap[i]));
        assert(str);
        Py_INCREF(str);
        PyList_SetItem(ifo_map, i, str);
    }
    // TODO: The return value should be checked in case it failed
    // It only decrements the ref count on success
    // If it fails we should exit the program
    PyModule_AddObject(module, "ifo_map", ifo_map);

    /* Cached ID types */
    // process_id_type = py_interop__get_ilwdchar_class("process", "process_id");
    // row_event_id_type = py_interop__get_ilwdchar_class("postcoh", "event_id");

    /* PostcohInspiralTable */
    //_gstlal_GSTLALPostcohInspiral_Type = &py_interop__postcohinspiraltable_type;
    if (PyType_Ready(&gstlal_GSTLALPostcohInspiral_Type) < 0) return;
    Py_INCREF(&gstlal_GSTLALPostcohInspiral_Type);
    PyModule_AddObject(module, "GSTLALPostcohInspiral",
                       (PyObject *)&gstlal_GSTLALPostcohInspiral_Type);
}
