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
    PyObject_HEAD // PyObject_HEAD includes a trailing semicolon
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
      offsetof(PostcohInspiralWrapper, row.is_background), 0, "is_background" },
    { "livetime", T_INT, offsetof(PostcohInspiralWrapper, row.livetime), 0,
      "livetime" },
    { "tmplt_idx", T_INT, offsetof(PostcohInspiralWrapper, row.tmplt_idx), 0,
      "tmplt_idx" },
    { "bankid", T_INT, offsetof(PostcohInspiralWrapper, row.bankid), 0,
      "bankid" },
    { "pix_idx", T_INT, offsetof(PostcohInspiralWrapper, row.pix_idx), 0,
      "pix_idx" },
    { "cohsnr", T_FLOAT, offsetof(PostcohInspiralWrapper, row.cohsnr), 0,
      "cohsnr" },
    { "nullsnr", T_FLOAT, offsetof(PostcohInspiralWrapper, row.nullsnr), 0,
      "nullsnr" },
    { "cmbchisq", T_FLOAT, offsetof(PostcohInspiralWrapper, row.cmbchisq), 0,
      "cmbchisq" },
    { "spearman_pval", T_FLOAT,
      offsetof(PostcohInspiralWrapper, row.spearman_pval), 0, "spearman_pval" },
    { "fap", T_FLOAT, offsetof(PostcohInspiralWrapper, row.fap), 0, "fap" },
    { "far_2h", T_FLOAT, offsetof(PostcohInspiralWrapper, row.far_2h), 0,
      "far_2h" },
    { "far_1d", T_FLOAT, offsetof(PostcohInspiralWrapper, row.far_1d), 0,
      "far_1d" },
    { "far_1w", T_FLOAT, offsetof(PostcohInspiralWrapper, row.far_1w), 0,
      "far_1w" },
    { "far", T_FLOAT, offsetof(PostcohInspiralWrapper, row.far), 0, "far" },
    { "rank", T_DOUBLE, offsetof(PostcohInspiralWrapper, row.rank), 0, "rank" },
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
    { "eta", T_FLOAT, offsetof(PostcohInspiralWrapper, row.eta), 0, "eta" },
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
    { "dec", T_DOUBLE, offsetof(PostcohInspiralWrapper, row.dec), 0, "dec" },
    { "f_final", T_FLOAT, offsetof(PostcohInspiralWrapper, row.f_final), 0,
      "f_final" },
    { "_process_id", T_LONG, offsetof(PostcohInspiralWrapper, row.process_id),
      0, "process_id (long)" },
    { "_event_id", T_LONG, offsetof(PostcohInspiralWrapper, row.event_id), 0,
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

typedef struct postcohtable_py__string_closure {
    Py_ssize_t offset;
    Py_ssize_t length;
} PostcohPyStringClosure;

static PyObject *postcohtable_py__get_string(PyObject *obj, void *closure) {
    assert(obj);
    const PostcohPyStringClosure *closure_typed = closure;

    char *field = (char *)((void *)obj + closure_typed->offset);
    assert((Py_ssize_t)strlen(field) < closure_typed->length);

    return PyString_FromString(field);
}

static int
  postcohtable_py__set_string(PyObject *obj, PyObject *value, void *closure) {
    assert(obj);
    assert(value);
    const PostcohPyStringClosure *closure_typed = closure;
    char *value_as_string                       = PyString_AsString(value);
    if (PyErr_Occurred()) return -1;
    if ((Py_ssize_t)strlen(value_as_string) >= closure_typed->length) {
        PyErr_Format(PyExc_ValueError, "string too long \'%s\'",
                     value_as_string);
        return -1;
    }

    char *field = (char *)((void *)obj + closure_typed->offset);
    assert((Py_ssize_t)strlen(field) < closure_typed->length);

    strcpy(field, value_as_string);
    return 0;
}

// FIXME: This should follow the same format as our other get functions.
static PyObject *postcohtable_py__snr_series_get(PyObject *obj, void *closure) {
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

#define NUM_SINGLE_FIELDS     11
#define NUM_FIELDS_PER_IFO    10
#define MAX_FIELD_NAME_LENGTH 40
static struct PyGetSetDef
  getset[NUM_SINGLE_FIELDS + NUM_FIELDS_PER_IFO * MAX_NIFO + 1] = { { NULL } };
static char field_names[NUM_SINGLE_FIELDS + NUM_FIELDS_PER_IFO * MAX_NIFO]
                       [MAX_FIELD_NAME_LENGTH] = { 0 };

#define NUM_STRING_FIELDS 3
static PostcohPyStringClosure attr_string_closures[NUM_STRING_FIELDS];
static Py_ssize_t attr_offsets[NUM_FIELDS_PER_IFO * MAX_NIFO];

static PyObject *postcohtable_py__get_double(PyObject *obj, void *closure) {
    assert(obj);
    const Py_ssize_t *offset = closure;

    double *field = (double *)((void *)obj + *offset);
    return PyFloat_FromDouble(*field);
}

static int
  postcohtable_py__set_double(PyObject *obj, PyObject *value, void *closure) {
    assert(obj);
    assert(value);
    const Py_ssize_t *offset = closure;
    double value_as_double   = PyFloat_AsDouble(value);
    if (PyErr_Occurred()) return -1;

    double *field = (double *)((void *)obj + *offset);
    *field        = value_as_double;
    return 0;
}

static PyObject *postcohtable_py__get_float(PyObject *obj, void *closure) {
    assert(obj);
    const Py_ssize_t *offset = closure;

    float *field = (float *)((void *)obj + *offset);
    return PyFloat_FromDouble((double)*field);
}

static int
  postcohtable_py__set_float(PyObject *obj, PyObject *value, void *closure) {
    assert(obj);
    assert(value);
    const Py_ssize_t *offset = closure;
    double value_as_double   = PyFloat_AsDouble(value);
    if (PyErr_Occurred()) return -1;

    float *field = (float *)((void *)obj + *offset);
    *field       = (float)value_as_double;
    return 0;
}

static PyObject *postcohtable_py__get_int(PyObject *obj, void *closure) {
    assert(obj);
    const Py_ssize_t *offset = closure;

    int *field = (int *)((void *)obj + *offset);
    return PyInt_FromLong((long)*field);
}

static int
  postcohtable_py__set_int(PyObject *obj, PyObject *value, void *closure) {
    assert(obj);
    assert(value);
    const Py_ssize_t *offset = closure;
    int value_as_long        = (int)PyInt_AsLong(value);
    if (PyErr_Occurred()) return -1;

    int *field = (int *)((void *)obj + *offset);
    *field     = (int)value_as_long;
    return 0;
}

static __inline__ void set_field_name(char *name, int field_idx) {
    assert(strlen(name) <= MAX_FIELD_NAME_LENGTH);
    strcpy(field_names[field_idx], name);
}

static __inline__ void
  set_ifo_field_name(char *name, int ifo_id, int field_idx) {
    assert(strlen(name) + 1 + MAX_IFO_LEN <= MAX_FIELD_NAME_LENGTH);
    set_field_name(name, field_idx);
    strncat(field_names[field_idx], "_", 1);
    strncat(field_names[field_idx], IFOMap[ifo_id], MAX_IFO_LEN);
}

static __inline__ void
  declare_getset(getter get, setter set, void *closure, int field_idx) {
    assert(strlen(field_names[field_idx]) > 0
           && strlen(field_names[field_idx]) <= MAX_FIELD_NAME_LENGTH);
    getset[field_idx] = (PyGetSetDef) { field_names[field_idx], get, set,
                                        field_names[field_idx], closure };
}

static __inline__ void declare_string_getset(PostcohPyStringClosure closure,
                                             getter get,
                                             setter set,
                                             int closure_idx,
                                             int field_idx) {
    attr_string_closures[closure_idx] = closure;
    declare_getset(get, set, &attr_string_closures[closure_idx], field_idx);
}

static __inline__ void declare_offset_getset(
  Py_ssize_t offset, getter get, setter set, int closure_idx, int field_idx) {
    attr_offsets[closure_idx] = offset;
    declare_getset(get, set, &attr_offsets[closure_idx], field_idx);
}

static __inline__ void
  declare_name_getset(getter get, setter set, int field_idx) {
    declare_getset(get, set, field_names[field_idx], field_idx);
}

void prepare_getset() {
    int field_idx          = 0;
    int string_closure_idx = 0;
    int offset_closure_idx = 0;

    set_field_name("ifos", field_idx);
    declare_string_getset(
      (PostcohPyStringClosure) { offsetof(PostcohInspiralWrapper, row.ifos),
                                 MAX_ALLIFO_LEN },
      postcohtable_py__get_string, postcohtable_py__set_string,
      string_closure_idx++, field_idx++);

    set_field_name("pivotal_ifo", field_idx);
    declare_string_getset(
      (PostcohPyStringClosure) {
        offsetof(PostcohInspiralWrapper, row.pivotal_ifo), MAX_IFO_LEN },
      postcohtable_py__get_string, postcohtable_py__set_string,
      string_closure_idx++, field_idx++);

    set_field_name("skymap_fname", field_idx);
    declare_string_getset(
      (PostcohPyStringClosure) {
        offsetof(PostcohInspiralWrapper, row.skymap_fname),
        MAX_SKYMAP_FNAME_LEN },
      postcohtable_py__get_string, postcohtable_py__set_string,
      string_closure_idx++, field_idx++);

    set_field_name("_snr_name", field_idx);
    declare_name_getset(postcohtable_py__snr_series_get, NULL, field_idx++);
    set_field_name("_snr_epoch_gpsSeconds", field_idx);
    declare_name_getset(postcohtable_py__snr_series_get, NULL, field_idx++);
    set_field_name("_snr_epoch_gpsNanoSeconds", field_idx);
    declare_name_getset(postcohtable_py__snr_series_get, NULL, field_idx++);
    set_field_name("_snr_f0", field_idx);
    declare_name_getset(postcohtable_py__snr_series_get, NULL, field_idx++);
    set_field_name("_snr_deltaT", field_idx);
    declare_name_getset(postcohtable_py__snr_series_get, NULL, field_idx++);
    set_field_name("_snr_sampleUnits", field_idx);
    declare_name_getset(postcohtable_py__snr_series_get, NULL, field_idx++);
    set_field_name("_snr_data_length", field_idx);
    declare_name_getset(postcohtable_py__snr_series_get, NULL, field_idx++);
    set_field_name("_snr_data", field_idx);
    declare_name_getset(postcohtable_py__snr_series_get, NULL, field_idx++);

    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        set_ifo_field_name("chisq", ifo_id, field_idx);
        declare_offset_getset(
          offsetof(PostcohInspiralWrapper, row.chisq[ifo_id]),
          postcohtable_py__get_float, postcohtable_py__set_float,
          offset_closure_idx++, field_idx++);

        set_ifo_field_name("snglsnr", ifo_id, field_idx);
        declare_offset_getset(
          offsetof(PostcohInspiralWrapper, row.snglsnr[ifo_id]),
          postcohtable_py__get_float, postcohtable_py__set_float,
          offset_closure_idx++, field_idx++);

        set_ifo_field_name("coaphase", ifo_id, field_idx);
        declare_offset_getset(
          offsetof(PostcohInspiralWrapper, row.coaphase[ifo_id]),
          postcohtable_py__get_float, postcohtable_py__set_float,
          offset_closure_idx++, field_idx++);

        set_ifo_field_name("far_sngl", ifo_id, field_idx);
        declare_offset_getset(
          offsetof(PostcohInspiralWrapper, row.far_sngl[ifo_id]),
          postcohtable_py__get_float, postcohtable_py__set_float,
          offset_closure_idx++, field_idx++);

        set_ifo_field_name("far_1d_sngl", ifo_id, field_idx);
        declare_offset_getset(
          offsetof(PostcohInspiralWrapper, row.far_1d_sngl[ifo_id]),
          postcohtable_py__get_float, postcohtable_py__set_float,
          offset_closure_idx++, field_idx++);

        set_ifo_field_name("far_1w_sngl", ifo_id, field_idx);
        declare_offset_getset(
          offsetof(PostcohInspiralWrapper, row.far_1w_sngl[ifo_id]),
          postcohtable_py__get_float, postcohtable_py__set_float,
          offset_closure_idx++, field_idx++);

        set_ifo_field_name("far_2h_sngl", ifo_id, field_idx);
        declare_offset_getset(
          offsetof(PostcohInspiralWrapper, row.far_2h_sngl[ifo_id]),
          postcohtable_py__get_float, postcohtable_py__set_float,
          offset_closure_idx++, field_idx++);

        set_ifo_field_name("deff", ifo_id, field_idx);
        declare_offset_getset(
          offsetof(PostcohInspiralWrapper, row.deff[ifo_id]),
          postcohtable_py__get_double, postcohtable_py__set_double,
          offset_closure_idx++, field_idx++);

        set_ifo_field_name("end_time_sngl", ifo_id, field_idx);
        declare_offset_getset(
          offsetof(PostcohInspiralWrapper, row.end_time_sngl[ifo_id]),
          postcohtable_py__get_int, postcohtable_py__set_int,
          offset_closure_idx++, field_idx++);

        set_ifo_field_name("end_time_ns_sngl", ifo_id, field_idx);
        declare_offset_getset(
          offsetof(PostcohInspiralWrapper, row.end_time_sngl[ifo_id])
            + offsetof(LIGOTimeGPS, gpsNanoSeconds),
          postcohtable_py__get_int, postcohtable_py__set_int,
          offset_closure_idx++, field_idx++);
    }
    getset[field_idx] = (PyGetSetDef) { NULL };
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

/*
 * Methods
 */

static PyObject *__new__(PyTypeObject *type, PyObject *args, PyObject *kwds) {
    PostcohInspiralWrapper *instance =
      (PostcohInspiralWrapper *)PyType_GenericNew(type, args, kwds);

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
        PyObject *py_trigger =
          PyType_GenericNew((PyTypeObject *)cls, NULL, NULL);
        if (!py_trigger) {
            Py_DECREF(result);
            return NULL;
        }
        /* memcpy postcoh row */
        const PostcohInspiralTable *postcohtable_trigger =
          (const PostcohInspiralTable *)data;
        data += sizeof(PostcohInspiralTable);
        /* if the data read in is less then expected amount */
        if (data > end) {
            Py_DECREF(py_trigger);
            Py_DECREF(result);
            PyErr_SetString(PyExc_ValueError,
                            "overran end of buffer while deserializing a "
                            "PostcohInspiralTable");
            return NULL;
        }

        PostcohInspiralWrapper *py_trigger_typed =
          (PostcohInspiralWrapper *)py_trigger;

        py_trigger_typed->row = *postcohtable_trigger;

        py_trigger_typed->end_time_sngl = PyArray_SimpleNewFromData(
          2, end_time_dims, NPY_INT, py_trigger_typed->row.end_time_sngl);
        py_trigger_typed->snglsnr = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, py_trigger_typed->row.snglsnr);
        py_trigger_typed->coaphase = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, py_trigger_typed->row.coaphase);
        py_trigger_typed->chisq = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, py_trigger_typed->row.chisq);
        py_trigger_typed->far_sngl = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, py_trigger_typed->row.far_sngl);
        py_trigger_typed->far_1w_sngl = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, py_trigger_typed->row.far_1w_sngl);
        py_trigger_typed->far_1d_sngl = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, py_trigger_typed->row.far_1d_sngl);
        py_trigger_typed->far_2h_sngl = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, py_trigger_typed->row.far_2h_sngl);
        py_trigger_typed->deff = PyArray_SimpleNewFromData(
          1, dims, NPY_DOUBLE, py_trigger_typed->row.deff);

        /* duplicate the SNR time series if we have length? */
        if (py_trigger_typed->row.snr_length) {
            const size_t nbytes = sizeof(py_trigger_typed->row.snr[0])
                                  * py_trigger_typed->row.snr_length;
            if (data + nbytes > end) {
                Py_DECREF(py_trigger);
                Py_DECREF(result);
                PyErr_SetString(PyExc_ValueError,
                                "buffer overrun while copying SNR time series");
                return NULL;
            }
            COMPLEX8TimeSeries *series = XLALCreateCOMPLEX8TimeSeries(
              "snr", &py_trigger_typed->row.epoch, 0.,
              py_trigger_typed->row.deltaT, &lalDimensionlessUnit,
              py_trigger_typed->row.snr_length);
            if (!series) {
                Py_DECREF(py_trigger);
                Py_DECREF(result);
                PyErr_SetString(PyExc_MemoryError, "out of memory");
                return NULL;
            }
            memcpy(series->data->data, py_trigger_typed->row.snr, nbytes);
            data += nbytes;
            py_trigger_typed->snr = series;
        } else
            py_trigger_typed->snr = NULL;

        if (PyList_Append(result, py_trigger)) printf("append failure");
        Py_DECREF(py_trigger);
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
    PyObject_HEAD_INIT(NULL) // PyObject_HEAD_INIT includes a trailing comma
      .tp_basicsize = sizeof(PostcohInspiralWrapper),
    .tp_doc         = "LAL's PostcohInspiral structure",
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
