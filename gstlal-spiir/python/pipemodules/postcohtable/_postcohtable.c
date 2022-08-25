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
    PyObject snr_series[MAX_NIFO];
} Complex8TimeSeriesWrapper;

typedef struct {
    PyObject_HEAD
    PostcohInspiralTable postcohtable;
    PyObject *end_time_sngl;
    PyObject *snglsnr;
    PyObject *coaphase;
    PyObject *chisq;
    PyObject *far_sngl;
    PyObject *far_1w_sngl;
    PyObject *far_1d_sngl;
    PyObject *far_2h_sngl;
    PyObject *deff;
    Complex8TimeSeriesWrapper wrapped_snr_series;
} PostcohInspiralWrapper;

static void PyArray_SimpleNewFromComplex8TimeSeries(Complex8TimeSeriesWrapper * wrapped_snr_series, PostcohInspiralTable * buffer_postcoh) {
  // Allocate a separate numpy array for each snr_series
  if (buffer_postcoh->snr_series) {
      for (int ifo_id = 0; ifo_id < MAX_NIFO; ifo_id++) {
          if (buffer_postcoh->snr_series[ifo_id] && buffer_postcoh->snr_series[ifo_id]->data->length > 0) {
              npy_intp snr_series_dims[1] = { buffer_postcoh->snr_series[ifo_id]->data->length };
              wrapped_snr_series->snr_series[ifo_id] = *PyArray_SimpleNewFromData(1, snr_series_dims, NPY_CFLOAT, buffer_postcoh->snr_series[ifo_id]->data->data);
              Py_INCREF(&wrapped_snr_series->snr_series[ifo_id]);
          } 
          // else {
          //     wrapped_snr_series->snr_series[ifo_id] = NULL;
          // }
      }
  } 
  // else {
  //     for (int ifo_id = 0; ifo_id < MAX_NIFO; ifo_id++) {
  //         wrapped_snr_series->snr_series[ifo_id] = NULL;
  //     }
  // }
}

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

// These are upper bounds for memory storage and do not need to be exact.
#define NUM_STRING_GETSETS  3
#define NUM_OFFSET_KEY_GETSETS 7 * MAX_NIFO
#define NUM_OFFSET_GETSETS 11 * MAX_NIFO
#define NUM_GETSETS                                                            \
    (NUM_STRING_GETSETS + NUM_OFFSET_KEY_GETSETS + NUM_OFFSET_GETSETS)
#define MAX_GETSET_NAME_LENGTH 40

typedef struct {
    size_t offset;
    size_t capacity;
} StringField;

typedef struct {
    size_t offset;
    char key[MAX_GETSET_NAME_LENGTH];
} OffsetKey;

// NOTE: This structure includes pointers to its own fields, so it requires a
// deep copy to duplicate. See !35
typedef struct {
    char names[NUM_GETSETS][MAX_GETSET_NAME_LENGTH];
    StringField string_fields[NUM_STRING_GETSETS];
    OffsetKey offset_keys[NUM_OFFSET_KEY_GETSETS];
    size_t offsets[NUM_OFFSET_GETSETS];
    struct PyGetSetDef getsets[NUM_GETSETS + 1];
} PostcohtableGetSets;

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

static PyObject *get_snr_series(PyObject *obj, void *closure) {
    assert(obj);
    const OffsetKey *offset_key = (OffsetKey *)closure;
    const size_t offset = offset_key->offset;
    COMPLEX8TimeSeries *snr_series = 
        *(COMPLEX8TimeSeries **)((void *)obj + offset);

    const char *key = offset_key->key;
    if (!strcmp(key, "snr_series_name")) {
        return PyString_FromString(snr_series->name);
    } else if (!strcmp(key, "snr_series_epoch_gpsSeconds")) {
        return PyInt_FromLong(snr_series->epoch.gpsSeconds);
    } else if (!strcmp(key, "snr_series_epoch_gpsNanoSeconds")) {
        return PyInt_FromLong(snr_series->epoch.gpsNanoSeconds);
    } else if (!strcmp(key, "snr_series_f0")) {
        return PyFloat_FromDouble(snr_series->f0);
    } else if (!strcmp(key, "snr_series_deltaT")) {
        return PyFloat_FromDouble(snr_series->deltaT);
    } else if (!strcmp(key, "snr_series_sampleUnits")) {
        char *s          = XLALUnitToString(&snr_series->sampleUnits);
        PyObject *result = PyString_FromString(s);
        XLALFree(s);
        return result;
    } else if (!strcmp(key, "snr_series_data_length")) {
        return PyInt_FromLong(snr_series->data->length);
    }
    PyErr_BadArgument();
    return NULL;
}

static PyObject *get_py_object(PyObject *obj, void *closure) {
    assert(obj);
    const size_t offset = *(size_t *)closure;

    return *(PyObject **)((void *)obj + offset);
}

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

typedef struct {
    int getset_idx;
    int string_field_idx;
    int offset_idx;
    int offset_key_idx;
    PostcohtableGetSets *postcohtable_getsets;
} GetSetBuilder;

static GetSetBuilder *
  construct_getset_builder(PostcohtableGetSets *postcohtable_getsets) {
    static GetSetBuilder builder = { 0 };
    builder.getset_idx           = 0;
    builder.string_field_idx     = 0;
    builder.offset_idx           = 0;
    builder.offset_key_idx       = 0;
    builder.postcohtable_getsets = postcohtable_getsets;
    return &builder;
}

static void _declare_getset(
  GetSetBuilder *builder, char *name, void *closure, getter get, setter set) {
    assert(strlen(name) > 0 && strlen(name) < MAX_GETSET_NAME_LENGTH);
    char *getset_name =
      builder->postcohtable_getsets->names[builder->getset_idx];

    strcpy(getset_name, name);
    builder->postcohtable_getsets->getsets[builder->getset_idx++] =
      (PyGetSetDef) { getset_name, get, set, getset_name, closure };
    builder->postcohtable_getsets->getsets[builder->getset_idx] =
      (PyGetSetDef) { NULL };
}

static void declare_string_getset(GetSetBuilder *builder,
                                  char *name,
                                  StringField string_field,
                                  getter get,
                                  setter set) {
    StringField *getset_string_field =
      &builder->postcohtable_getsets
         ->string_fields[builder->string_field_idx++];

    *getset_string_field = string_field;
    _declare_getset(builder, name, getset_string_field, get, set);
}

static void declare_offset_getset(
  GetSetBuilder *builder, char *name, size_t offset, getter get, setter set) {
    size_t *getset_offset =
      &builder->postcohtable_getsets->offsets[builder->offset_idx++];

    *getset_offset = offset;
    _declare_getset(builder, name, getset_offset, get, set);
}

static void declare_offset_key_getset(
  GetSetBuilder *builder, char *name, OffsetKey offset_key, getter get, setter set) {
    OffsetKey *getset_offset_key = &builder->postcohtable_getsets->offset_keys[builder->offset_key_idx++];

    assert(strlen(offset_key.key) < MAX_GETSET_NAME_LENGTH);
    *getset_offset_key = offset_key;

    _declare_getset(builder, name, getset_offset_key, get, set);
}

static void format_name(char *output_name, char *base_name, int ifo_id) {
    assert(strlen(base_name) + 1 + strlen(IFOMap[ifo_id])
           < MAX_GETSET_NAME_LENGTH);
    sprintf(output_name, "%s_%s", base_name, IFOMap[ifo_id]);
}

static void prepare_getset(PostcohtableGetSets *postcohtable_getsets) {
    GetSetBuilder *builder   = construct_getset_builder(postcohtable_getsets);
    StringField string_field = { 0 };

    string_field.offset   = offsetof(PostcohInspiralWrapper, postcohtable.ifos);
    string_field.capacity = MAX_ALLIFO_LEN;
    declare_string_getset(builder, "ifos", string_field, read_string_from_field,
                          write_string_to_field);
    string_field.offset =
      offsetof(PostcohInspiralWrapper, postcohtable.pivotal_ifo);
    string_field.capacity = MAX_IFO_LEN;
    declare_string_getset(builder, "pivotal_ifo", string_field,
                          read_string_from_field, write_string_to_field);
    string_field.offset =
      offsetof(PostcohInspiralWrapper, postcohtable.skymap_fname);
    string_field.capacity = MAX_SKYMAP_FNAME_LEN;
    declare_string_getset(builder, "skymap_fname", string_field,
                          read_string_from_field, write_string_to_field);

    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        char name[MAX_GETSET_NAME_LENGTH];
        OffsetKey offset_key = {};
        offset_key.offset   = offsetof(PostcohInspiralWrapper, postcohtable.snr_series[ifo_id]);

        format_name(name, "snr_series_name", ifo_id);
        strcpy(offset_key.key, "snr_series_name");
        declare_offset_key_getset(
          builder, name,
          offset_key,
          get_snr_series, NULL);

        format_name(name, "snr_series_epoch_gpsSeconds", ifo_id);
        strcpy(offset_key.key, "snr_series_epoch_gpsSeconds");
        declare_offset_key_getset(
          builder, name,
          offset_key,
          get_snr_series, NULL);

        format_name(name, "snr_series_epoch_gpsNanoSeconds", ifo_id);
        strcpy(offset_key.key, "snr_series_epoch_gpsNanoSeconds");
        declare_offset_key_getset(
          builder, name,
          offset_key,
          get_snr_series, NULL);

        format_name(name, "snr_series_f0", ifo_id);
        strcpy(offset_key.key, "snr_series_f0");
        declare_offset_key_getset(
          builder, name,
          offset_key,
          get_snr_series, NULL);

        format_name(name, "snr_series_deltaT", ifo_id);
        strcpy(offset_key.key, "snr_series_deltaT");
        declare_offset_key_getset(
          builder, name,
          offset_key,
          get_snr_series, NULL);

        format_name(name, "snr_series_sampleUnits", ifo_id);
        strcpy(offset_key.key, "snr_series_sampleUnits");
        declare_offset_key_getset(
          builder, name,
          offset_key,
          get_snr_series, NULL);

        format_name(name, "snr_series_data_length", ifo_id);
        strcpy(offset_key.key, "snr_series_data_length");
        declare_offset_key_getset(
          builder, name,
          offset_key,
          get_snr_series, NULL);

        format_name(name, "snr_series_data", ifo_id);
        declare_offset_getset(
          builder, name,
          offsetof(PostcohInspiralWrapper, wrapped_snr_series.snr_series[ifo_id]),
          get_py_object, NULL);

        format_name(name, "chisq", ifo_id);
        declare_offset_getset(
          builder, name,
          offsetof(PostcohInspiralWrapper, postcohtable.chisq[ifo_id]),
          read_float_from_field, write_float_to_field);

        format_name(name, "snglsnr", ifo_id);
        declare_offset_getset(
          builder, name,
          offsetof(PostcohInspiralWrapper, postcohtable.snglsnr[ifo_id]),
          read_float_from_field, write_float_to_field);

        format_name(name, "coaphase", ifo_id);
        declare_offset_getset(
          builder, name,
          offsetof(PostcohInspiralWrapper, postcohtable.coaphase[ifo_id]),
          read_float_from_field, write_float_to_field);

        format_name(name, "far_sngl", ifo_id);
        declare_offset_getset(
          builder, name,
          offsetof(PostcohInspiralWrapper, postcohtable.far_sngl[ifo_id]),
          read_float_from_field, write_float_to_field);

        format_name(name, "far_1d_sngl", ifo_id);
        declare_offset_getset(
          builder, name,
          offsetof(PostcohInspiralWrapper, postcohtable.far_1d_sngl[ifo_id]),
          read_float_from_field, write_float_to_field);

        format_name(name, "far_1w_sngl", ifo_id);
        declare_offset_getset(
          builder, name,
          offsetof(PostcohInspiralWrapper, postcohtable.far_1w_sngl[ifo_id]),
          read_float_from_field, write_float_to_field);

        format_name(name, "far_2h_sngl", ifo_id);
        declare_offset_getset(
          builder, name,
          offsetof(PostcohInspiralWrapper, postcohtable.far_2h_sngl[ifo_id]),
          read_float_from_field, write_float_to_field);

        format_name(name, "deff", ifo_id);
        declare_offset_getset(
          builder, name,
          offsetof(PostcohInspiralWrapper, postcohtable.deff[ifo_id]),
          read_double_from_field, write_double_to_field);

        format_name(name, "end_time_sngl", ifo_id);
        declare_offset_getset(
          builder, name,
          offsetof(PostcohInspiralWrapper, postcohtable.end_time_sngl[ifo_id]),
          read_int_from_field, write_int_to_field);

        format_name(name, "end_time_ns_sngl", ifo_id);
        declare_offset_getset(
          builder, name,
          offsetof(PostcohInspiralWrapper, postcohtable.end_time_sngl[ifo_id])
            + offsetof(LIGOTimeGPS, gpsNanoSeconds),
          read_int_from_field, write_int_to_field);
    }
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

static void free_snr_series(PostcohInspiralWrapper *postcoh_inspiral) {
    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        if (&postcoh_inspiral->wrapped_snr_series.snr_series[ifo_id]) {
            Py_DECREF(&postcoh_inspiral->wrapped_snr_series.snr_series[ifo_id]);
        }
    }
}

static void __del__(PyObject *self) {
    PostcohInspiralWrapper *self_typed = (PostcohInspiralWrapper *)self;
    Py_DECREF(self_typed->end_time_sngl);
    Py_DECREF(self_typed->snglsnr);
    Py_DECREF(self_typed->coaphase);
    Py_DECREF(self_typed->chisq);
    Py_DECREF(self_typed->far_sngl);
    Py_DECREF(self_typed->far_1w_sngl);
    Py_DECREF(self_typed->far_1d_sngl);
    Py_DECREF(self_typed->far_2h_sngl);
    Py_DECREF(self_typed->deff);

    free_snr_series(self_typed);

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

        Complex8TimeSeriesWrapper *wrapped_snr_series =
          (Complex8TimeSeriesWrapper *)PyType_GenericNew((PyTypeObject *)cls, NULL,
                                                      NULL);
        if (!wrapped_snr_series) {
            Py_DECREF(result);
            return NULL;
        }

        PyArray_SimpleNewFromComplex8TimeSeries(
          wrapped_snr_series, buffer_postcohtable);

        wrapped_postcohtable->wrapped_snr_series = *wrapped_snr_series;
        wrapped_postcohtable->end_time_sngl = PyArray_SimpleNewFromData(
          2, end_time_dims, NPY_INT,
          buffer_postcohtable->end_time_sngl);
        wrapped_postcohtable->snglsnr = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, buffer_postcohtable->snglsnr);
        wrapped_postcohtable->coaphase = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, buffer_postcohtable->coaphase);
        wrapped_postcohtable->chisq = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, buffer_postcohtable->chisq);
        wrapped_postcohtable->far_sngl = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, buffer_postcohtable->far_sngl);
        wrapped_postcohtable->far_1w_sngl = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, buffer_postcohtable->far_1w_sngl);
        wrapped_postcohtable->far_1d_sngl = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, buffer_postcohtable->far_1d_sngl);
        wrapped_postcohtable->far_2h_sngl = PyArray_SimpleNewFromData(
          1, dims, NPY_FLOAT, buffer_postcohtable->far_2h_sngl);
        wrapped_postcohtable->deff = PyArray_SimpleNewFromData(
          1, dims, NPY_DOUBLE, buffer_postcohtable->deff);

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

static PyObject *delete_all_snr_series(PyObject *self, PyObject *args) {
    PostcohInspiralWrapper *self_typed = (PostcohInspiralWrapper *)self;
    free_snr_series(self_typed);

    Py_INCREF(Py_None);
    return Py_None;
}

static struct PyMethodDef methods[] = {
    { "from_buffer", from_buffer, METH_VARARGS | METH_CLASS,
      "Construct a tuple of PostcohInspiralTable objects from a buffer object. "
      " The buffer is interpreted as a C array of PostcohInspiralTable "
      "structures." },
    { "delete_all_snr_series", delete_all_snr_series, METH_NOARGS,
      "Release all SNR time series attached to the GSTLALSnglInspiral "
      "object." },
    {
      NULL,
    }
};


/*
 * ============================================================================
 *
 *                            Module Registration
 *
 * ============================================================================
 */

PyMODINIT_FUNC init_postcohtable(void) {
    static PostcohtableGetSets postcohtable_getsets   = { 0 };
    static PyTypeObject postcoh_inspiral_wrapper_type = {
        // clang-format off
        PyObject_HEAD_INIT(NULL) // PyObject_HEAD_INIT includes a trailing comma
        .tp_basicsize = sizeof(PostcohInspiralWrapper), // clang-format on
        .tp_doc = "LAL's PostcohInspiral structure",
        .tp_flags =
          Py_TPFLAGS_DEFAULT | Py_TPFLAGS_BASETYPE | Py_TPFLAGS_CHECKTYPES,
        .tp_members = members,
        .tp_methods = methods,
        .tp_getset  = postcohtable_getsets.getsets,
        .tp_name    = MODULE_NAME ".GSTLALPostcohInspiral",
        .tp_new     = __new__,
        .tp_dealloc = __del__,
    };

    PyObject *module = Py_InitModule3(
      MODULE_NAME, NULL, "Wrapper for LAL's PostcohInspiralTable type.");

    prepare_getset(&postcohtable_getsets);
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
    if (PyType_Ready(&postcoh_inspiral_wrapper_type) < 0) return;
    Py_INCREF(&postcoh_inspiral_wrapper_type);
    PyModule_AddObject(module, "GSTLALPostcohInspiral",
                       (PyObject *)&postcoh_inspiral_wrapper_type);
}
