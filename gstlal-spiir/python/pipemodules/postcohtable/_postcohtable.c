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
    PyObject_HEAD
    PostcohInspiralTable postcohtable;
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
} PostcohInspiralWrapper;

// static PyObject *row_event_id_type = NULL;
// static PyObject *process_id_type = NULL;

/*
 * Member access
 */

static PyMemberDef members[] = {
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
    { "_process_id", T_LONG,
      offsetof(PostcohInspiralWrapper, postcohtable.process_id), 0,
      "process_id (long)" },
    { "_event_id", T_LONG,
      offsetof(PostcohInspiralWrapper, postcohtable.event_id), 0,
      "event_id (long)" },

    // Things that are done single detector are ndarrays
    { "end_time_sngl", T_OBJECT_EX,
      offsetof(PostcohInspiralWrapper, end_time_sngl), READONLY,
      "end_time_sngl" },
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

typedef struct {
    size_t offset;
    size_t capacity;
} StringField;

static PyObject *read_string_from_field(PyObject *obj, void *closure) {
    assert(obj);
    const StringField *string_field = (StringField *)closure;

    char *field = (char *)((void *)obj + string_field->offset);
    assert(strnlen(field, string_field->capacity) < string_field->capacity);

    return PyString_FromString(field);
}

static int
  write_string_to_field(PyObject *obj, PyObject *value, void *closure) {
    assert(obj);
    assert(value);
    const StringField *string_field = (StringField *)closure;
    char *value_as_string           = PyString_AsString(value);
    if (PyErr_Occurred()) return -1;
    if (strnlen(value_as_string, string_field->capacity)
        >= string_field->capacity) {
        PyErr_Format(PyExc_ValueError, "string too long \'%s\'",
                     value_as_string);
        return -1;
    }

    char *field = (char *)((void *)obj + string_field->offset);

    strcpy(field, value_as_string);
    return 0;
}

// FIXME: This should follow the same format as our other get functions.
static PyObject *get_snr_series(PyObject *obj, void *closure) {
    assert(obj);
    COMPLEX8TimeSeries *snr = ((PostcohInspiralWrapper *)obj)->snr;
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

#define NUM_SINGLE_GETSETS     11
#define NUM_GETSETS_PER_IFO    10
#define MAX_GETSET_NAME_LENGTH 40
#define NUM_GETSETS            (NUM_SINGLE_GETSETS + NUM_GETSETS_PER_IFO * MAX_NIFO)
static struct PyGetSetDef getset[NUM_GETSETS + 1] = { { NULL } };

static PyObject *read_double_from_field(PyObject *obj, void *closure) {
    assert(obj);
    const size_t offset = *(size_t *)closure;

    double *field = (double *)((void *)obj + offset);
    return PyFloat_FromDouble(*field);
}

static int
  write_double_to_field(PyObject *obj, PyObject *value, void *closure) {
    assert(obj);
    assert(value);
    const size_t offset    = *(size_t *)closure;
    double value_as_double = PyFloat_AsDouble(value);
    if (PyErr_Occurred()) return -1;

    double *field = (double *)((void *)obj + offset);
    *field        = value_as_double;
    return 0;
}

static PyObject *read_float_from_field(PyObject *obj, void *closure) {
    assert(obj);
    const size_t offset = *(size_t *)closure;

    float *field = (float *)((void *)obj + offset);
    return PyFloat_FromDouble((double)*field);
}

static int write_float_to_field(PyObject *obj, PyObject *value, void *closure) {
    assert(obj);
    assert(value);
    const size_t offset    = *(size_t *)closure;
    double value_as_double = PyFloat_AsDouble(value);
    if (PyErr_Occurred()) return -1;

    float *field = (float *)((void *)obj + offset);
    *field       = (float)value_as_double;
    return 0;
}

static PyObject *read_int_from_field(PyObject *obj, void *closure) {
    assert(obj);
    const size_t offset = *(size_t *)closure;

    int *field = (int *)((void *)obj + offset);
    return PyInt_FromLong((long)*field);
}

static int write_int_to_field(PyObject *obj, PyObject *value, void *closure) {
    assert(obj);
    assert(value);
    const size_t offset = *(size_t *)closure;
    int value_as_long   = (int)PyInt_AsLong(value);
    if (PyErr_Occurred()) return -1;

    int *field = (int *)((void *)obj + offset);
    *field     = (int)value_as_long;
    return 0;
}

static void declare_getset(char *name, void *closure, getter get, setter set) {
    static char getset_names[NUM_GETSETS][MAX_GETSET_NAME_LENGTH] = { 0 };
    static int getset_idx                                        = 0;
    assert(strlen(name) > 0 && strlen(name) < MAX_GETSET_NAME_LENGTH);

    strcpy(getset_names[getset_idx], name);
    getset[getset_idx] = (PyGetSetDef) { getset_names[getset_idx], get, set,
                                        getset_names[getset_idx], closure };

    getset_idx++;
}

static void declare_ifo_getset(
  char *base_name, void *closure, getter get, setter set, int ifo_id) {
    assert(strlen(base_name) + 1 + strlen(IFOMap[ifo_id])
           < MAX_GETSET_NAME_LENGTH);
    char *name = malloc(strlen(base_name) + 1 + strlen(IFOMap[ifo_id]));

    strcpy(name, base_name);
    strcat(name, "_");
    strcat(name, IFOMap[ifo_id]);

    declare_getset(name, closure, get, set);
    free(name);
}

#define NUM_STRING_GETSETS 3
static StringField *get_static_string_closure(StringField closure) {
    static StringField attr_string_closures[NUM_STRING_GETSETS];
    static int closure_idx = 0;

    attr_string_closures[closure_idx] = closure;
    return &attr_string_closures[closure_idx++];
}

static size_t *get_static_offset_closure(size_t offset) {
    static size_t attr_offsets[NUM_GETSETS_PER_IFO * MAX_NIFO];
    static int closure_idx = 0;

    attr_offsets[closure_idx] = offset;
    return &attr_offsets[closure_idx++];
}

#define NUM_NAME_GETSETS 8
static char *get_static_name_closure(char *name) {
    static char attr_name_closures[NUM_NAME_GETSETS][MAX_GETSET_NAME_LENGTH];
    static int closure_idx = 0;
    assert(strlen(name) < MAX_GETSET_NAME_LENGTH);

    strcpy(attr_name_closures[closure_idx], name);

    return attr_name_closures[closure_idx++];
}

static void prepare_getset() {
    StringField string_closure = { 0, 0 };

    string_closure =
      (StringField) { offsetof(PostcohInspiralWrapper, postcohtable.ifos),
                      MAX_ALLIFO_LEN };
    declare_getset("ifos", get_static_string_closure(string_closure),
                   read_string_from_field, write_string_to_field);
    string_closure = (StringField) { offsetof(PostcohInspiralWrapper,
                                              postcohtable.pivotal_ifo),
                                     MAX_ALLIFO_LEN };
    declare_getset("pivotal_ifo", get_static_string_closure(string_closure),
                   read_string_from_field, write_string_to_field);
    string_closure = (StringField) { offsetof(PostcohInspiralWrapper,
                                              postcohtable.skymap_fname),
                                     MAX_ALLIFO_LEN };
    declare_getset("skymap_fname", get_static_string_closure(string_closure),
                   read_string_from_field, write_string_to_field);

    declare_getset("_snr_name", get_static_name_closure("_snr_name"),
                   get_snr_series, NULL);
    declare_getset("_snr_epoch_gpsSeconds",
                   get_static_name_closure("_snr_epoch_gpsSeconds"),
                   get_snr_series, NULL);
    declare_getset("_snr_epoch_gpsNanoSeconds",
                   get_static_name_closure("_snr_epoch_gpsNanoSeconds"),
                   get_snr_series, NULL);
    declare_getset("_snr_f0", get_static_name_closure("_snr_f0"),
                   get_snr_series, NULL);
    declare_getset("_snr_deltaT", get_static_name_closure("_snr_deltaT"),
                   get_snr_series, NULL);
    declare_getset("_snr_sampleUnits",
                   get_static_name_closure("_snr_sampleUnits"), get_snr_series,
                   NULL);
    declare_getset("_snr_data_length",
                   get_static_name_closure("_snr_data_length"), get_snr_series,
                   NULL);
    declare_getset("_snr_data", get_static_name_closure("_snr_data"),
                   get_snr_series, NULL);

    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        declare_ifo_getset(
          "chisq",
          get_static_offset_closure(
            offsetof(PostcohInspiralWrapper, postcohtable.chisq[ifo_id])),
          read_float_from_field, write_float_to_field, ifo_id);

        declare_ifo_getset(
          "snglsnr",
          get_static_offset_closure(
            offsetof(PostcohInspiralWrapper, postcohtable.snglsnr[ifo_id])),
          read_float_from_field, write_float_to_field, ifo_id);

        declare_ifo_getset(
          "coaphase",
          get_static_offset_closure(
            offsetof(PostcohInspiralWrapper, postcohtable.coaphase[ifo_id])),
          read_float_from_field, write_float_to_field, ifo_id);

        declare_ifo_getset(
          "far_sngl",
          get_static_offset_closure(
            offsetof(PostcohInspiralWrapper, postcohtable.far_sngl[ifo_id])),
          read_float_from_field, write_float_to_field, ifo_id);

        declare_ifo_getset(
          "far_1d_sngl",
          get_static_offset_closure(
            offsetof(PostcohInspiralWrapper, postcohtable.far_1d_sngl[ifo_id])),
          read_float_from_field, write_float_to_field, ifo_id);

        declare_ifo_getset(
          "far_1w_sngl",
          get_static_offset_closure(
            offsetof(PostcohInspiralWrapper, postcohtable.far_1w_sngl[ifo_id])),
          read_float_from_field, write_float_to_field, ifo_id);

        declare_ifo_getset(
          "far_2h_sngl",
          get_static_offset_closure(
            offsetof(PostcohInspiralWrapper, postcohtable.far_2h_sngl[ifo_id])),
          read_float_from_field, write_float_to_field, ifo_id);

        declare_ifo_getset(
          "deff",
          get_static_offset_closure(
            offsetof(PostcohInspiralWrapper, postcohtable.deff[ifo_id])),
          read_double_from_field, write_double_to_field, ifo_id);

        declare_ifo_getset(
          "end_time_sngl",
          get_static_offset_closure(offsetof(
            PostcohInspiralWrapper, postcohtable.end_time_sngl[ifo_id])),
          read_int_from_field, write_int_to_field, ifo_id);

        declare_ifo_getset(
          "end_time_ns_sngl",
          get_static_offset_closure(
            offsetof(PostcohInspiralWrapper, postcohtable.end_time_sngl[ifo_id])
            + offsetof(LIGOTimeGPS, gpsNanoSeconds)),
          read_int_from_field, write_int_to_field, ifo_id);
    }
    getset[NUM_GETSETS] = (PyGetSetDef) { NULL };
}

// static Py_ssize_t getreadbuffer(PyObject *self, Py_ssize_t segment, void
// **ptrptr)
//{
//	if(segment) {
//		PyErr_SetString(PyExc_SystemError, "bad segment");
//		return -1;
//	}
//	*ptrptr = &((PostcohInspiralWrapper*)self)->postcohtable;
//	return sizeof(((PostcohInspiralWrapper*)self)->postcohtable);
//}
//
//
// static Py_ssize_t getsegcount(PyObject *self, Py_ssize_t *lenp)
//{
//	if(lenp)
//		*lenp = sizeof(((PostcohInspiralWrapper*)self)->postcohtable);
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
    PostcohInspiralWrapper *instance =
      (PostcohInspiralWrapper *)PyType_GenericNew(type, args, kwds);

    if (!instance) return NULL;

    /* link the event_id pointer in the postcohtable table structure
     * to the event_id structure */
    // new->postcohtable->event_id = new->event_id_i;

    // new->process_id_i = 0;
    // new->event_id_i = 0;

    /* done */
    return (PyObject *)instance;
}

static void __del__(PyObject *self) {
    PostcohInspiralWrapper *self_typed = (PostcohInspiralWrapper *)self;
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
        PostcohInspiralWrapper *wrapped_postcohtable =
          (PostcohInspiralWrapper *)PyType_GenericNew((PyTypeObject *)cls, NULL,
                                                      NULL);
        if (!wrapped_postcohtable) {
            Py_DECREF(result);
            return NULL;
        }
        /* memcpy postcoh postcohtable */
        const PostcohInspiralTable *buffer_postcohtable =
          (const PostcohInspiralTable *)data;
        data += sizeof(PostcohInspiralTable);
        /* if the data read in is less then expected amount */
        if (data > end) {
            Py_DECREF((PyObject *)wrapped_postcohtable);
            Py_DECREF(result);
            PyErr_SetString(PyExc_ValueError,
                            "overran end of buffer while deserializing a "
                            "PostcohInspiralTable");
            return NULL;
        }

        wrapped_postcohtable->postcohtable = *buffer_postcohtable;

        wrapped_postcohtable->end_time_sngl = PyArray_SimpleNewFromData(
          2, end_time_dims, NPY_INT,
          wrapped_postcohtable->postcohtable.end_time_sngl);
        wrapped_postcohtable->snglsnr = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, wrapped_postcohtable->postcohtable.snglsnr);
        wrapped_postcohtable->coaphase = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, wrapped_postcohtable->postcohtable.coaphase);
        wrapped_postcohtable->chisq = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, wrapped_postcohtable->postcohtable.chisq);
        wrapped_postcohtable->far_sngl = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, wrapped_postcohtable->postcohtable.far_sngl);
        wrapped_postcohtable->far_1w_sngl = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, wrapped_postcohtable->postcohtable.far_1w_sngl);
        wrapped_postcohtable->far_1d_sngl = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, wrapped_postcohtable->postcohtable.far_1d_sngl);
        wrapped_postcohtable->far_2h_sngl = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, wrapped_postcohtable->postcohtable.far_2h_sngl);
        wrapped_postcohtable->deff = PyArray_SimpleNewFromData(
          1, dims, NPY_DOUBLE, wrapped_postcohtable->postcohtable.deff);

        /* duplicate the SNR time series if we have length? */
        if (wrapped_postcohtable->postcohtable.snr_length) {
            const size_t nbytes =
              sizeof(wrapped_postcohtable->postcohtable.snr[0])
              * wrapped_postcohtable->postcohtable.snr_length;
            if (data + nbytes > end) {
                Py_DECREF((PyObject *)wrapped_postcohtable);
                Py_DECREF(result);
                PyErr_SetString(PyExc_ValueError,
                                "buffer overrun while copying SNR time series");
                return NULL;
            }
            COMPLEX8TimeSeries *series = XLALCreateCOMPLEX8TimeSeries(
              "snr", &wrapped_postcohtable->postcohtable.epoch, 0.,
              wrapped_postcohtable->postcohtable.deltaT, &lalDimensionlessUnit,
              wrapped_postcohtable->postcohtable.snr_length);
            if (!series) {
                Py_DECREF((PyObject *)wrapped_postcohtable);
                Py_DECREF(result);
                PyErr_SetString(PyExc_MemoryError, "out of memory");
                return NULL;
            }
            memcpy(series->data->data, wrapped_postcohtable->postcohtable.snr,
                   nbytes);
            data += nbytes;
            wrapped_postcohtable->snr = series;
        } else
            wrapped_postcohtable->snr = NULL;

        if (PyList_Append(result, (PyObject *)wrapped_postcohtable))
            printf("append failure");
        Py_DECREF((PyObject *)wrapped_postcohtable);
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

static PyTypeObject PostcohInspiralWrapper_Type = {
    // clang-format off
    PyObject_HEAD_INIT(NULL) // PyObject_HEAD_INIT includes a trailing comma
    .tp_basicsize = sizeof(PostcohInspiralWrapper), // clang-format on
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
    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        PyObject *str =
          PyString_FromStringAndSize(IFOMap[ifo_id], strlen(IFOMap[ifo_id]));
        assert(str);
        Py_INCREF(str);
        PyList_SetItem(ifo_map, ifo_id, str);
    }
    PyModule_AddObject(module, "ifo_map", ifo_map);

    /* Cached ID types */
    // process_id_type = postcohtable_py__get_ilwdchar_class("process",
    // "process_id"); row_event_id_type =
    // postcohtable_py__get_ilwdchar_class("postcoh", "event_id");

    /* PostcohInspiralTable */
    //_PostcohInspiralWrapper_Type =
    //&postcohtable_py__postcohinspiraltable_type;
    if (PyType_Ready(&PostcohInspiralWrapper_Type) < 0) return;
    Py_INCREF(&PostcohInspiralWrapper_Type);
    PyModule_AddObject(module, "GSTLALPostcohInspiral",
                       (PyObject *)&PostcohInspiralWrapper_Type);
}
