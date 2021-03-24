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
    PyObject *end_time_sngl;
    PyObject *snglsnr;
    PyObject *coaphase;
    PyObject *chisq;
    PyObject *far_sngl;
    PyObject *far_1w_sngl;
    PyObject *far_1d_sngl;
    PyObject *far_2h_sngl;
    PyObject *deff;
    COMPLEX8TimeSeries **snr_series;
} PostcohInspiralWrapper;

// static PyObject *row_event_id_type = NULL;
// static PyObject *process_id_type = NULL;

/*
 * Member access
 */

static PyMemberDef members[] = {
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
      offsetof(PostcohInspiralWrapper, end_time_sngl), 0,
      "end_time_sngl" },
    { "snglsnr", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, snglsnr),
      0, "snglsnr" },
    { "coaphase", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, coaphase),
      0, "coaphase" },
    { "chisq", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, chisq), 0,
      "chisq" },
    { "far_sngl", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, far_sngl),
      0, "far_sngl" },
    { "far_1w_sngl", T_OBJECT_EX,
      offsetof(PostcohInspiralWrapper, far_1w_sngl), 0, "far_1w_sngl" },
    { "far_1d_sngl", T_OBJECT_EX,
      offsetof(PostcohInspiralWrapper, far_1d_sngl), 0, "far_1d_sngl" },
    { "far_2h_sngl", T_OBJECT_EX,
      offsetof(PostcohInspiralWrapper, far_2h_sngl), 0, "far_2h_sngl" },
    { "deff", T_OBJECT_EX, offsetof(PostcohInspiralWrapper, deff), 0,
      "deff" },
    { NULL },
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
// static Py_ssize_t getreadbuffer(PyObject *self, Py_ssize_t segment, void
// **ptrptr)
//{
//	if(segment) {
//		PyErr_SetString(PyExc_SystemError, "bad segment");
//		return -1;
//	}
//	*ptrptr = &((PostcohInspiralWrapper*)self)->row;
//	return sizeof(((PostcohInspiralWrapper*)self)->row);
//}
//
//
// static Py_ssize_t getsegcount(PyObject *self, Py_ssize_t *lenp)
//{
//	if(lenp)
//		*lenp = sizeof(((PostcohInspiralWrapper*)self)->row);
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

static PyObject *snr_component_get_helper(COMPLEX8TimeSeries *snr,
                                          const char *name) {
    // A helper function that extract and return an attribute from snr, whose
    // type is COMPLEX8TimeSeries, by name.

    if (!strcmp(name, "name_")) {
        return PyString_FromString(snr->name);
    } else if (!strcmp(name, "epoch_gpsSeconds_")) {
        return PyInt_FromLong(snr->epoch.gpsSeconds);
    } else if (!strcmp(name, "epoch_gpsNanoSeconds_")) {
        return PyInt_FromLong(snr->epoch.gpsNanoSeconds);
    } else if (!strcmp(name, "f0_")) {
        return PyFloat_FromDouble(snr->f0);
    } else if (!strcmp(name, "deltaT_")) {
        return PyFloat_FromDouble(snr->deltaT);
    } else if (!strcmp(name, "sampleUnits_")) {
        char *s          = XLALUnitToString(&snr->sampleUnits);
        PyObject *result = PyString_FromString(s);
        XLALFree(s);
        return result;
    } else if (!strcmp(name, "data_length_")) {
        return PyInt_FromLong(snr->data->length);
    } else if (!strcmp(name, "data_")) {
        npy_intp dims[] = { snr->data->length };
        PyObject *array =
          PyArray_SimpleNewFromData(1, dims, NPY_CFLOAT, snr->data->data);
        if (!array) return NULL;
        return array;
    }
    PyErr_BadArgument();
    return NULL;
}

struct Closure_for_snr_series{ // a struct for snr_series get function closure
  Py_ssize_t index;
  char *closure_str;
};

static PyObject *snr_series_get(PyObject *obj, void *_closure) {
    if (!((PostcohInspiralWrapper *)obj)->snr_series) {
        PyErr_SetString(PyExc_ValueError, "no snr time series available");
        return NULL;
    }
    struct Closure_for_snr_series *closure =
      (struct Closure_for_snr_series *)_closure;
    int index        = closure->index;
    const char *name = closure->closure_str;
    COMPLEX8TimeSeries *snr =
      ((PostcohInspiralWrapper *)obj)->snr_series[index];
    if (!snr) {
        PyErr_SetString(PyExc_ValueError,
                        "no snr time series for this detector is available");
        return NULL;
    }
    return snr_component_get_helper(snr, name);
}

#define VAR 18 // the number of attributes in each IFO
#define SINGLE 3 // number of single attributes independent on IFOs

// IFO-independent attributes defined first as follows

static struct PyGetSetDef getset[SINGLE + VAR * MAX_NIFO + 1] = {
    { "ifos", pylal_inline_string_get, pylal_inline_string_set, "ifos",
      &(struct pylal_inline_string_description) {
        offsetof(PostcohInspiralWrapper, row.ifos), MAX_ALLIFO_LEN } },
    { "pivotal_ifo", pylal_inline_string_get, pylal_inline_string_set,
      "pivotal_ifo",
      &(struct pylal_inline_string_description) {
        offsetof(PostcohInspiralWrapper, row.pivotal_ifo),
        MAX_IFO_LEN } },
    { "skymap_fname", pylal_inline_string_get, pylal_inline_string_set,
      "skymap_fname",
      &(struct pylal_inline_string_description) {
        offsetof(PostcohInspiralWrapper, row.skymap_fname),
        MAX_SKYMAP_FNAME_LEN } },
    { NULL }
};

// IFO-dependent attributes defined here

void prepare_getset() {
    int offset = SINGLE;
    for (int i = 0; i < MAX_NIFO; ++i) {
        char *var  = "chisq_";
        char *name = (char *)malloc(strlen(IFOMap[i].name) + strlen(var) + 1);
        struct lal_array *data =
          (struct lal_array *)malloc(sizeof(struct lal_array));
        data->offset = offsetof(PostcohInspiralWrapper, row.chisq);
        data->index  = i;
        strcpy(name, var);
        strcat(name, IFOMap[i].name);
        PyGetSetDef def  = { name, pylal_float_array_get, pylal_float_array_set,
                            name, data };
        getset[offset++] = def;

        var          = "snglsnr_";
        name         = (char *)malloc(strlen(IFOMap[i].name) + strlen(var) + 1);
        data         = (struct lal_array *)malloc(sizeof(struct lal_array));
        data->offset = offsetof(PostcohInspiralWrapper, row.snglsnr);
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
        data->offset = offsetof(PostcohInspiralWrapper, row.coaphase);
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
        data->offset = offsetof(PostcohInspiralWrapper, row.far_sngl);
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
        data->offset = offsetof(PostcohInspiralWrapper, row.far_1d_sngl);
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
        data->offset = offsetof(PostcohInspiralWrapper, row.far_1w_sngl);
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
        data->offset = offsetof(PostcohInspiralWrapper, row.far_2h_sngl);
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
        data->offset = offsetof(PostcohInspiralWrapper, row.deff);
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
          offsetof(PostcohInspiralWrapper, row.end_time_sngl);
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
          offsetof(PostcohInspiralWrapper, row.end_time_sngl);
        data->index = i * 2 + 1;
        strcpy(name, var);
        strcat(name, IFOMap[i].name);
        def.name         = name;
        def.get          = pylal_int_array_get;
        def.set          = pylal_int_array_set;
        def.doc          = name;
        def.closure      = data;
        getset[offset++] = def;

        // ****************************
        // Set def for snr_series data
        // ****************************
        
        struct Closure_for_snr_series *closure;
        char *head="snr_series_";


        var  = "name_";
        name = (char *)malloc(strlen(head) + strlen(IFOMap[i].name) + strlen(var) + 1);
        closure = (struct Closure_for_snr_series *)malloc(sizeof(struct Closure_for_snr_series));
        strcpy(name, head);
        strcat(name, var);
        strcat(name, IFOMap[i].name);
        closure->index = i;
        closure->closure_str = var;
        def.name         = name;
        def.get          = snr_series_get;
        def.set          = NULL;
        def.doc          = name;
        def.closure      = closure;
        getset[offset++] = def;

        var  = "epoch_gpsSeconds_";
        name = (char *)malloc(strlen(head) + strlen(IFOMap[i].name) + strlen(var) + 1);
        closure = (struct Closure_for_snr_series *)malloc(sizeof(struct Closure_for_snr_series));
        strcpy(name, head);
        strcat(name, var);
        strcat(name, IFOMap[i].name);
        closure->index = i;
        closure->closure_str = var;
        def.name         = name;
        def.get          = snr_series_get;
        def.set          = NULL;
        def.doc          = name;
        def.closure      = closure;
        getset[offset++] = def;

        var  = "epoch_gpsNanoSeconds";
        name = (char *)malloc(strlen(head) + strlen(IFOMap[i].name) + strlen(var) + 1);
        closure = (struct Closure_for_snr_series *)malloc(sizeof(struct Closure_for_snr_series));
        strcpy(name, head);
        strcat(name, var);
        strcat(name, IFOMap[i].name);
        closure->index = i;
        closure->closure_str = var;
        def.name         = name;
        def.get          = snr_series_get;
        def.set          = NULL;
        def.doc          = name;
        def.closure      = closure;
        getset[offset++] = def;

        var  = "f0_";
        name = (char *)malloc(strlen(head) + strlen(IFOMap[i].name) + strlen(var) + 1);
        closure = (struct Closure_for_snr_series *)malloc(sizeof(struct Closure_for_snr_series));
        strcpy(name, head);
        strcat(name, var);
        strcat(name, IFOMap[i].name);
        closure->index = i;
        closure->closure_str = var;
        def.name         = name;
        def.get          = snr_series_get;
        def.set          = NULL;
        def.doc          = name;
        def.closure      = closure;
        getset[offset++] = def;

        var  = "deltaT_";
        name = (char *)malloc(strlen(head) + strlen(IFOMap[i].name) + strlen(var) + 1);
        closure = (struct Closure_for_snr_series *)malloc(sizeof(struct Closure_for_snr_series));
        strcpy(name, head);
        strcat(name, var);
        strcat(name, IFOMap[i].name);
        closure->index = i;
        closure->closure_str = var;
        def.name         = name;
        def.get          = snr_series_get;
        def.set          = NULL;
        def.doc          = name;
        def.closure      = closure;
        getset[offset++] = def;

        var  = "sampleUnits_";
        name = (char *)malloc(strlen(head) + strlen(IFOMap[i].name) + strlen(var) + 1);
        closure = (struct Closure_for_snr_series *)malloc(sizeof(struct Closure_for_snr_series));
        strcpy(name, head);
        strcat(name, var);
        strcat(name, IFOMap[i].name);
        closure->index = i;
        closure->closure_str = var;
        def.name         = name;
        def.get          = snr_series_get;
        def.set          = NULL;
        def.doc          = name;
        def.closure      = closure;
        getset[offset++] = def;

        var  = "data_length_";
        name = (char *)malloc(strlen(head) + strlen(IFOMap[i].name) + strlen(var) + 1);
        closure = (struct Closure_for_snr_series *)malloc(sizeof(struct Closure_for_snr_series));
        strcpy(name, head);
        strcat(name, var);
        strcat(name, IFOMap[i].name);
        closure->index = i;
        closure->closure_str = var;
        def.name         = name;
        def.get          = snr_series_get;
        def.set          = NULL;
        def.doc          = name;
        def.closure      = closure;
        getset[offset++] = def;

        var  = "data_";
        name = (char *)malloc(strlen(head) + strlen(IFOMap[i].name) + strlen(var) + 1);
        closure = (struct Closure_for_snr_series *)malloc(sizeof(struct Closure_for_snr_series));
        strcpy(name, head);
        strcat(name, var);
        strcat(name, IFOMap[i].name);
        closure->index = i;
        closure->closure_str = var;
        def.name         = name;
        def.get          = snr_series_get;
        def.set          = NULL;
        def.doc          = name;
        def.closure      = closure;
        getset[offset++] = def;

        // ***************************************
        // End of setting def for snr_series data
        // ***************************************
    }
    PyGetSetDef def = { NULL };
    getset[offset]  = def;
}

/*
 * Methods
 */

static PyObject *__new__(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    PostcohInspiralWrapper *ret =
      (PostcohInspiralWrapper *)PyType_GenericNew(type, args, kwds);

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
    PostcohInspiralWrapper *typed_self =
      (PostcohInspiralWrapper *)self;
    Py_DECREF(typed_self->end_time_sngl);
    Py_DECREF(typed_self->snglsnr);
    Py_DECREF(typed_self->coaphase);
    Py_DECREF(typed_self->chisq);
    Py_DECREF(typed_self->far_sngl);
    Py_DECREF(typed_self->far_1w_sngl);
    Py_DECREF(typed_self->far_1d_sngl);
    Py_DECREF(typed_self->far_2h_sngl);
    Py_DECREF(typed_self->deff);

    // Free snr_series related memory
    if (typed_self->snr_series) {
        // Destroy COMPLEX8TimeSeries objects
        for (int i = 0; i < MAX_NIFO; i++) {
            if (typed_self->snr_series[i]) {
                XLALDestroyCOMPLEX8TimeSeries(typed_self->snr_series[i]);
            }
        }

        // free snr_series array
        free(typed_self->snr_series);
		typed_self->snr_series = NULL;
    }

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
        const PostcohInspiralTable *trigger =
          (const PostcohInspiralTable *)data;
        data += sizeof(*trigger);
        /* if the data read in is less then expected amount */
        if (data > end) {
            Py_DECREF(item);
            Py_DECREF(result);
            PyErr_SetString(PyExc_ValueError,
                            "buffer overrun while copying postcoh row");
            return NULL;
        }
        /* sorb the PostcohInspiralTable entry from the pipeline to the
         * PostcohInspiralWrapper item*/
        ((PostcohInspiralWrapper *)item)->row =
          (PostcohInspiralTable)*trigger;

        // Set the single-detector arrays
        ((PostcohInspiralWrapper *)item)->end_time_sngl =
          PyArray_SimpleNewFromData(1, end_time_dims, NPY_INT,
                                    trigger->end_time_sngl);
        ((PostcohInspiralWrapper *)item)->snglsnr =
          PyArray_SimpleNewFromData(1, dims, NPY_FLOAT,
                                    trigger->snglsnr);
        ((PostcohInspiralWrapper *)item)->coaphase =
          PyArray_SimpleNewFromData(1, dims, NPY_FLOAT,
                                    trigger->coaphase);
        ((PostcohInspiralWrapper *)item)->chisq =
          PyArray_SimpleNewFromData(1, dims, NPY_FLOAT,
                                    trigger->chisq);
        ((PostcohInspiralWrapper *)item)->far_sngl =
          PyArray_SimpleNewFromData(1, dims, NPY_FLOAT,
                                    trigger->far_sngl);
        ((PostcohInspiralWrapper *)item)->far_1w_sngl =
          PyArray_SimpleNewFromData(1, dims, NPY_FLOAT,
                                    trigger->far_1w_sngl);
        ((PostcohInspiralWrapper *)item)->far_1d_sngl =
          PyArray_SimpleNewFromData(1, dims, NPY_FLOAT,
                                    trigger->far_1d_sngl);
        ((PostcohInspiralWrapper *)item)->far_2h_sngl =
          PyArray_SimpleNewFromData(1, dims, NPY_FLOAT,
                                    trigger->far_2h_sngl);
        ((PostcohInspiralWrapper *)item)->deff =
          PyArray_SimpleNewFromData(1, dims, NPY_DOUBLE,
                                    trigger->deff);

        // Shallow copy snr_series data to avoid memory leaking
        if (trigger->snr_series) {
            ((PostcohInspiralWrapper *)item)->snr_series =
              malloc(sizeof(COMPLEX8TimeSeries *) * MAX_NIFO);
            if (!((PostcohInspiralWrapper *)item)->snr_series) {
                Py_DECREF(item);
                Py_DECREF(result);
                PyErr_SetString(PyExc_MemoryError, "out of memory");
                return NULL;
            }
            for (int i = 0; i < MAX_NIFO; i++) {
                if (trigger->snr_series[i]
                    && trigger->snr_series[i]->data->length
                         > 0) {
                    ((PostcohInspiralWrapper *)item)->snr_series[i] =
                     trigger->snr_series[i]; 
                } else {
                  ((PostcohInspiralWrapper *)item)->snr_series[i] = NULL;
                }
            }
        } else {
            ((PostcohInspiralWrapper *)item)->snr_series = NULL;
        }

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

static PyObject *delete_all_snr_series(PyObject *self, PyObject *args) {
   COMPLEX8TimeSeries **snr_series = ((PostcohInspiralWrapper *)self)->snr_series;
    if (snr_series) {
        for (int i = 0; i < MAX_NIFO; i++) {
            if (snr_series[i]) {
                XLALDestroyCOMPLEX8TimeSeries(snr_series[i]);
                snr_series[i] = NULL;
            }
        }
    }
    free(((PostcohInspiralWrapper *)self)->snr_series);
    ((PostcohInspiralWrapper *)self)->snr_series = NULL;
    Py_INCREF(Py_None);
    return Py_None;
}
static struct PyMethodDef methods[] = {
    { "from_buffer", from_buffer, METH_VARARGS | METH_CLASS,
      "Construct a tuple of PostcohInspiralTable objects from a buffer object. "
      " The buffer is interpreted as a C array of PostcohInspiralTable "
      "structures." },
    { "delete_all_snr_series", delete_all_snr_series, METH_NOARGS,
      "Release all SNR time series attached to the GSTLALSnglInspiral object."
    },
    {
      NULL,
    }
};

/*
 * Type
 */

static PyTypeObject PostcohInspiralWrapper_Type = {
    PyObject_HEAD_INIT(NULL).tp_basicsize =
      sizeof(PostcohInspiralWrapper),
    .tp_doc = "LAL's PostcohInspiral structure with SNR series",
    .tp_flags =
      Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_CHECKTYPES,
    .tp_members = members,
    .tp_methods = methods,
    .tp_getset  = getset,
    .tp_name    = MODULE_NAME ".PostcohInspiralWrapper",
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
    //_PostcohInspiralWrapper_Type = &pylal_postcohinspiraltable_type;
    if (PyType_Ready(&PostcohInspiralWrapper_Type) < 0) return;
    Py_INCREF(&PostcohInspiralWrapper_Type);
    PyModule_AddObject(module, "PostcohInspiralWrapper",
                       (PyObject *)&PostcohInspiralWrapper_Type);
}
