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
    PyObject_HEAD PostcohInspiralTable row;
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
      offsetof(gstlal_GSTLALPostcohInspiral, end_time_sngl), 0,
      "end_time_sngl" },
    { "snglsnr", T_OBJECT_EX, offsetof(gstlal_GSTLALPostcohInspiral, snglsnr),
      0, "snglsnr" },
    { "coaphase", T_OBJECT_EX, offsetof(gstlal_GSTLALPostcohInspiral, coaphase),
      0, "coaphase" },
    { "chisq", T_OBJECT_EX, offsetof(gstlal_GSTLALPostcohInspiral, chisq), 0,
      "chisq" },
    { "far_sngl", T_OBJECT_EX, offsetof(gstlal_GSTLALPostcohInspiral, far_sngl),
      0, "far_sngl" },
    { "far_1w_sngl", T_OBJECT_EX,
      offsetof(gstlal_GSTLALPostcohInspiral, far_1w_sngl), 0, "far_1w_sngl" },
    { "far_1d_sngl", T_OBJECT_EX,
      offsetof(gstlal_GSTLALPostcohInspiral, far_1d_sngl), 0, "far_1d_sngl" },
    { "far_2h_sngl", T_OBJECT_EX,
      offsetof(gstlal_GSTLALPostcohInspiral, far_2h_sngl), 0, "far_2h_sngl" },
    { "deff", T_OBJECT_EX, offsetof(gstlal_GSTLALPostcohInspiral, deff), 0,
      "deff" },
    { NULL },
};

struct pylal_inline_string_description {
    Py_ssize_t offset;
    Py_ssize_t length;
};

static PyObject *pylal_inline_string_get(PyObject *obj, void *data) {
    const struct pylal_inline_string_description *desc = data;
    char *s = (char *)obj + desc->offset;

    if ((ssize_t)strlen(s) >= desc->length) {
        /* something's wrong, obj probably isn't a valid address */
    }

    return PyString_FromString(s);
}

static int pylal_inline_string_set(PyObject *obj, PyObject *val, void *data) {
    const struct pylal_inline_string_description *desc = data;
    char *v                                            = PyString_AsString(val);
    char *s = (char *)obj + desc->offset;

    if (!v) return -1;
    if ((ssize_t)strlen(v) >= desc->length) {
        PyErr_Format(PyExc_ValueError, "string too long \'%s\'", v);
        return -1;
    }

    strncpy(s, v, desc->length - 1);
    s[desc->length - 1] = '\0';

    return 0;
}

static PyObject *snr_component_get(PyObject *obj, void *data) {
    COMPLEX8TimeSeries *snr = ((gstlal_GSTLALPostcohInspiral *)obj)->snr;
    const char *name        = (const char *)data;

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

#define SINGLE 11
static struct PyGetSetDef getset[SINGLE + 10 * MAX_NIFO + 1] = {
    { "ifos", pylal_inline_string_get, pylal_inline_string_set, "ifos",
      &(struct pylal_inline_string_description) {
        offsetof(gstlal_GSTLALPostcohInspiral, row.ifos), MAX_ALLIFO_LEN } },
    { "pivotal_ifo", pylal_inline_string_get, pylal_inline_string_set,
      "pivotal_ifo",
      &(struct pylal_inline_string_description) {
        offsetof(gstlal_GSTLALPostcohInspiral, row.pivotal_ifo),
        MAX_IFO_LEN } },
    { "skymap_fname", pylal_inline_string_get, pylal_inline_string_set,
      "skymap_fname",
      &(struct pylal_inline_string_description) {
        offsetof(gstlal_GSTLALPostcohInspiral, row.skymap_fname),
        MAX_SKYMAP_FNAME_LEN } },
    { "_snr_name", snr_component_get, NULL, ".snr.name", "_snr_name" },
    { "_snr_epoch_gpsSeconds", snr_component_get, NULL, ".snr.epoch.gpsSeconds",
      "_snr_epoch_gpsSeconds" },
    { "_snr_epoch_gpsNanoSeconds", snr_component_get, NULL,
      ".snr.epoch.gpsNanoSeconds", "_snr_epoch_gpsNanoSeconds" },
    { "_snr_f0", snr_component_get, NULL, ".snr.f0", "_snr_f0" },
    { "_snr_deltaT", snr_component_get, NULL, ".snr.deltaT", "_snr_deltaT" },
    { "_snr_sampleUnits", snr_component_get, NULL, ".snr.sampleUnits",
      "_snr_sampleUnits" },
    { "_snr_data_length", snr_component_get, NULL, ".snr.data.length",
      "_snr_data_length" },
    { "_snr_data", snr_component_get, NULL, ".snr.data", "_snr_data" },

    { NULL }
};

struct lal_array {
    Py_ssize_t offset;
    Py_ssize_t index;
};

static PyObject *pylal_double_array_get(PyObject *obj, void *data) {
    const struct lal_array *desc = data;
    double *d = (double *)((char *)obj + desc->offset) + desc->index;
    if (!d) {
        PyErr_Format(PyExc_ValueError, "float doesn't exist!");
        return NULL;
    }
    return PyFloat_FromDouble(*d);
}

static int pylal_double_array_set(PyObject *obj, PyObject *val, void *data) {
    const struct lal_array *desc = data;
    double v                     = PyFloat_AsDouble(val);
    double *d = (double *)((char *)obj + desc->offset) + desc->index;
    if (!d) {
        PyErr_Format(PyExc_ValueError, "float doesn't exist!");
        return -1;
    }
    *d = v;
    return 0;
}

static PyObject *pylal_float_array_get(PyObject *obj, void *data) {
    const struct lal_array *desc = data;
    float *f = (float *)((char *)obj + desc->offset) + desc->index;
    if (!f) {
        PyErr_Format(PyExc_ValueError, "float doesn't exist!");
        return NULL;
    }
    return PyFloat_FromDouble((double)*f);
}

static int pylal_float_array_set(PyObject *obj, PyObject *val, void *data) {
    const struct lal_array *desc = data;
    double v                     = PyFloat_AsDouble(val);
    float *f = (float *)((char *)obj + desc->offset) + desc->index;
    if (!f) {
        PyErr_Format(PyExc_ValueError, "float doesn't exist!");
        return -1;
    }
    *f = (float)v;
    return 0;
}

static PyObject *pylal_int_array_get(PyObject *obj, void *data) {
    const struct lal_array *desc = data;
    int *i = (int *)((char *)obj + desc->offset) + desc->index;
    if (!i) {
        PyErr_Format(PyExc_ValueError, "int doesn't exist!");
        return NULL;
    }
    return PyInt_FromLong((long)*i);
}

static int pylal_int_array_set(PyObject *obj, PyObject *val, void *data) {
    const struct lal_array *desc = data;
    int v                        = (int)PyInt_AsLong(val);
    int *i = (int *)((char *)obj + desc->offset) + desc->index;
    if (!i) {
        PyErr_Format(PyExc_ValueError, "float doesn't exist!");
        return -1;
    }
    *i = (int)v;
    return 0;
}

void prepare_getset() {
    int offset = SINGLE;
    for (int i = 0; i < MAX_NIFO; ++i) {
        char *var  = "chisq_";
        char *name = (char *)malloc(strlen(IFOMap[i].name) + strlen(var) + 1);
        struct lal_array *data =
          (struct lal_array *)malloc(sizeof(struct lal_array));
        data->offset = offsetof(gstlal_GSTLALPostcohInspiral, row.chisq);
        data->index  = i;
        strcpy(name, var);
        strcat(name, IFOMap[i].name);
        PyGetSetDef def  = { name, pylal_float_array_get, pylal_float_array_set,
                            name, data };
        getset[offset++] = def;

        var          = "snglsnr_";
        name         = (char *)malloc(strlen(IFOMap[i].name) + strlen(var) + 1);
        data         = (struct lal_array *)malloc(sizeof(struct lal_array));
        data->offset = offsetof(gstlal_GSTLALPostcohInspiral, row.snglsnr);
        data->index  = i;
        strcpy(name, var);
        strcat(name, IFOMap[i].name);
        def.name         = name;
        def.doc          = name;
        def.closure      = data;
        getset[offset++] = def;

        var          = "coaphase_";
        name         = (char *)malloc(strlen(IFOMap[i].name) + strlen(var) + 1);
        data         = (struct lal_array *)malloc(sizeof(struct lal_array));
        data->offset = offsetof(gstlal_GSTLALPostcohInspiral, row.coaphase);
        data->index  = i;
        strcpy(name, var);
        strcat(name, IFOMap[i].name);
        def.name         = name;
        def.doc          = name;
        def.closure      = data;
        getset[offset++] = def;

        var          = "far_sngl_";
        name         = (char *)malloc(strlen(IFOMap[i].name) + strlen(var) + 1);
        data         = (struct lal_array *)malloc(sizeof(struct lal_array));
        data->offset = offsetof(gstlal_GSTLALPostcohInspiral, row.far_sngl);
        data->index  = i;
        strcpy(name, var);
        strcat(name, IFOMap[i].name);
        def.name         = name;
        def.doc          = name;
        def.closure      = data;
        getset[offset++] = def;

        var          = "far_1d_sngl_";
        name         = (char *)malloc(strlen(IFOMap[i].name) + strlen(var) + 1);
        data         = (struct lal_array *)malloc(sizeof(struct lal_array));
        data->offset = offsetof(gstlal_GSTLALPostcohInspiral, row.far_1d_sngl);
        data->index  = i;
        strcpy(name, var);
        strcat(name, IFOMap[i].name);
        def.name         = name;
        def.doc          = name;
        def.closure      = data;
        getset[offset++] = def;

        var          = "far_1w_sngl_";
        name         = (char *)malloc(strlen(IFOMap[i].name) + strlen(var) + 1);
        data         = (struct lal_array *)malloc(sizeof(struct lal_array));
        data->offset = offsetof(gstlal_GSTLALPostcohInspiral, row.far_1w_sngl);
        data->index  = i;
        strcpy(name, var);
        strcat(name, IFOMap[i].name);
        def.name         = name;
        def.doc          = name;
        def.closure      = data;
        getset[offset++] = def;

        var          = "far_2h_sngl_";
        name         = (char *)malloc(strlen(IFOMap[i].name) + strlen(var) + 1);
        data         = (struct lal_array *)malloc(sizeof(struct lal_array));
        data->offset = offsetof(gstlal_GSTLALPostcohInspiral, row.far_2h_sngl);
        data->index  = i;
        strcpy(name, var);
        strcat(name, IFOMap[i].name);
        def.name         = name;
        def.doc          = name;
        def.closure      = data;
        getset[offset++] = def;

        var          = "deff_";
        name         = (char *)malloc(strlen(IFOMap[i].name) + strlen(var) + 1);
        data         = (struct lal_array *)malloc(sizeof(struct lal_array));
        data->offset = offsetof(gstlal_GSTLALPostcohInspiral, row.deff);
        data->index  = i;
        strcpy(name, var);
        strcat(name, IFOMap[i].name);
        def.name         = name;
        def.get          = pylal_double_array_get;
        def.set          = pylal_double_array_set;
        def.doc          = name;
        def.closure      = data;
        getset[offset++] = def;

        var  = "end_time_sngl_";
        name = (char *)malloc(strlen(IFOMap[i].name) + strlen(var) + 1);
        data = (struct lal_array *)malloc(sizeof(struct lal_array));
        data->offset =
          offsetof(gstlal_GSTLALPostcohInspiral, row.end_time_sngl);
        data->index = i * 2;
        strcpy(name, var);
        strcat(name, IFOMap[i].name);
        def.name         = name;
        def.get          = pylal_int_array_get;
        def.set          = pylal_int_array_set;
        def.doc          = name;
        def.closure      = data;
        getset[offset++] = def;

        var  = "end_time_ns_sngl_";
        name = (char *)malloc(strlen(IFOMap[i].name) + strlen(var) + 1);
        data = (struct lal_array *)malloc(sizeof(struct lal_array));
        data->offset =
          offsetof(gstlal_GSTLALPostcohInspiral, row.end_time_sngl);
        data->index = i * 2 + 1;
        strcpy(name, var);
        strcat(name, IFOMap[i].name);
        def.name         = name;
        def.get          = pylal_int_array_get;
        def.set          = pylal_int_array_set;
        def.doc          = name;
        def.closure      = data;
        getset[offset++] = def;
    }
    PyGetSetDef def = { NULL };
    getset[offset]  = def;
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
    gstlal_GSTLALPostcohInspiral *ret =
      (gstlal_GSTLALPostcohInspiral *)PyType_GenericNew(type, args, kwds);

    if (!ret) return NULL;

    /* link the event_id pointer in the row table structure
     * to the event_id structure */
    // new->row->event_id = new->event_id_i;

    // new->process_id_i = 0;
    // new->event_id_i = 0;

    /* done */
    return (PyObject *)ret;
}

static void __del__(PyObject *self) {
    gstlal_GSTLALPostcohInspiral *typed_self =
      (gstlal_GSTLALPostcohInspiral *)self;
    if (typed_self->snr) XLALDestroyCOMPLEX8TimeSeries(typed_self->snr);
    Py_DECREF(typed_self->end_time_sngl);
    Py_DECREF(typed_self->snglsnr);
    Py_DECREF(typed_self->coaphase);
    Py_DECREF(typed_self->chisq);
    Py_DECREF(typed_self->far_sngl);
    Py_DECREF(typed_self->far_1w_sngl);
    Py_DECREF(typed_self->far_1d_sngl);
    Py_DECREF(typed_self->far_2h_sngl);
    Py_DECREF(typed_self->deff);
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
        /* sorb the PostcohInspiralTable entry from the pipeline to the
         * gstlal_GSTLALPostcohInspiral item*/
        ((gstlal_GSTLALPostcohInspiral *)item)->row =
          (PostcohInspiralTable)*gstlal_postcohinspiral;

        // Set the single-detector arrays
        ((gstlal_GSTLALPostcohInspiral *)item)->end_time_sngl =
          PyArray_SimpleNewFromData(1, end_time_dims, NPY_INT,
                                    gstlal_postcohinspiral->end_time_sngl);
        ((gstlal_GSTLALPostcohInspiral *)item)->snglsnr =
          PyArray_SimpleNewFromData(1, dims, NPY_FLOAT,
                                    gstlal_postcohinspiral->snglsnr);
        ((gstlal_GSTLALPostcohInspiral *)item)->coaphase =
          PyArray_SimpleNewFromData(1, dims, NPY_FLOAT,
                                    gstlal_postcohinspiral->coaphase);
        ((gstlal_GSTLALPostcohInspiral *)item)->chisq =
          PyArray_SimpleNewFromData(1, dims, NPY_FLOAT,
                                    gstlal_postcohinspiral->chisq);
        ((gstlal_GSTLALPostcohInspiral *)item)->far_sngl =
          PyArray_SimpleNewFromData(1, dims, NPY_FLOAT,
                                    gstlal_postcohinspiral->far_sngl);
        ((gstlal_GSTLALPostcohInspiral *)item)->far_1w_sngl =
          PyArray_SimpleNewFromData(1, dims, NPY_FLOAT,
                                    gstlal_postcohinspiral->far_1w_sngl);
        ((gstlal_GSTLALPostcohInspiral *)item)->far_1d_sngl =
          PyArray_SimpleNewFromData(1, dims, NPY_FLOAT,
                                    gstlal_postcohinspiral->far_1d_sngl);
        ((gstlal_GSTLALPostcohInspiral *)item)->far_2h_sngl =
          PyArray_SimpleNewFromData(1, dims, NPY_FLOAT,
                                    gstlal_postcohinspiral->far_2h_sngl);
        ((gstlal_GSTLALPostcohInspiral *)item)->deff =
          PyArray_SimpleNewFromData(1, dims, NPY_DOUBLE,
                                    gstlal_postcohinspiral->deff);

        /* duplicate the SNR time series if we have length? */
        if (gstlal_postcohinspiral->snr_length) {
            const size_t nbytes = sizeof(gstlal_postcohinspiral->snr[0])
                                  * gstlal_postcohinspiral->snr_length;
            if (data + nbytes > end) {
                Py_DECREF(item);
                Py_DECREF(result);
                PyErr_SetString(PyExc_ValueError,
                                "buffer overrun while copying SNR time series");
                return NULL;
            }
            COMPLEX8TimeSeries *series = XLALCreateCOMPLEX8TimeSeries(
              "snr", &gstlal_postcohinspiral->epoch, 0.,
              gstlal_postcohinspiral->deltaT, &lalDimensionlessUnit,
              gstlal_postcohinspiral->snr_length);
            if (!series) {
                Py_DECREF(item);
                Py_DECREF(result);
                PyErr_SetString(PyExc_MemoryError, "out of memory");
                return NULL;
            }
            memcpy(series->data->data, gstlal_postcohinspiral->snr, nbytes);
            data += nbytes;
            ((gstlal_GSTLALPostcohInspiral *)item)->snr = series;
        } else
            ((gstlal_GSTLALPostcohInspiral *)item)->snr = NULL;

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
          PyString_FromStringAndSize(IFOMap[i].name, strlen(IFOMap[i].name));
        assert(str);
        Py_INCREF(str);
        PyList_SetItem(ifo_map, i, str);
    }
    PyModule_AddObject(module, "ifo_map", ifo_map);

    /* Cached ID types */
    // process_id_type = pylal_get_ilwdchar_class("process", "process_id");
    // row_event_id_type = pylal_get_ilwdchar_class("postcoh", "event_id");

    /* PostcohInspiralTable */
    //_gstlal_GSTLALPostcohInspiral_Type = &pylal_postcohinspiraltable_type;
    if (PyType_Ready(&gstlal_GSTLALPostcohInspiral_Type) < 0) return;
    Py_INCREF(&gstlal_GSTLALPostcohInspiral_Type);
    PyModule_AddObject(module, "GSTLALPostcohInspiral",
                       (PyObject *)&gstlal_GSTLALPostcohInspiral_Type);
}
