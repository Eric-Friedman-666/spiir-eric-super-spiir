/*
 * Copyright (C) 2014 Qi Chu <qi.chu@ligo.org>
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Library General Public
 * License as published by the Free Software Foundation; either
 * version 2 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Library General Public License for more details.
 *
 * You should have received a copy of the GNU Library General Public
 * License along with this library; if not, write to the
 * Free Software Foundation, Inc., 59 Temple Place - Suite 330,
 * Boston, MA 02111-1307, USA.
 */

#ifndef __CUDA_MULTIRATESPIIR_H__
#define __CUDA_MULTIRATESPIIR_H__

#include <cuda_runtime.h>
#include <glib.h>
#include <gst/base/gstadapter.h>
#include <gst/base/gstbasetransform.h>
#include <gst/gst.h>
#include <multiratespiir/multiratespiir_state.h>

G_BEGIN_DECLS

#define CUDA_TYPE_MULTIRATESPIIR (cuda_multiratespiir_get_type())
#define CUDA_MULTIRATESPIIR(obj)                                               \
    (G_TYPE_CHECK_INSTANCE_CAST((obj), CUDA_TYPE_MULTIRATESPIIR,               \
                                CudaMultirateSPIIR))
#define CUDA_MULTIRATESPIIR_CLASS(klass)                                       \
    (G_TYPE_CHECK_CLASS_CAST((klass), CUDA_TYPE_MULTIRATESPIIR,                \
                             CudaMultirateSPIIRClass))
#define GST_IS_CUDA_MULTIRATESPIIR(obj)                                        \
    (G_TYPE_CHECK_INSTANCE_TYPE((obj), CUDA_TYPE_MULTIRATESPIIR))
#define GST_IS_CUDA_MULTIRATESPIIR_CLASS(klass)                                \
    (G_TYPE_CHECK_CLASS_TYPE((klass), CUDA_TYPE_MULTIRATESPIIR))

typedef struct _CudaMultirateSPIIR CudaMultirateSPIIR;
typedef struct _CudaMultirateSPIIRClass CudaMultirateSPIIRClass;

/* single-precision bank */
typedef struct _SpiirBank_s {
    float *a1_s;
    float *b0_s;
    int *d_s;

    unsigned int num_templates;
    unsigned int num_filters;
    unsigned int rate;
    unsigned int depth;
} SpiirBank_s;

/**
 * CudaMultirateSPIIR:
 *
 * Opaque data structure.
 */
struct _CudaMultirateSPIIR {
    GstBaseTransform element;

    /* <private> */

    GstPad *srcpad;
    GstAdapter *adapter;
    GArray *flag_segments; /* book keeping the flag details, inspired by
                              control_segments in gstlal_gate.c */

    gboolean need_discont;
    guint num_depths;
    guint num_head_cover_samples; /* number of samples needed to produce the
                                     first buffer */
    guint num_tail_cover_samples; /* number of samples needed to produce the
                                     last buffer */
    guint num_exe_samples; /* number of samples executed every time after first
                              buffer */

    GstClockTime t0;
    guint64 offset0;
    guint64 samples_in;
    guint64 samples_out;
    guint64 next_in_offset;
    guint bps;

    guint64 num_gap_samples;
    gboolean need_tail_drain;

    gint outchannels; /* = number of templates */
    gint rate;
    gint width;
    gchar *bank_fname;
    GMutex iir_bank_lock;
    GCond iir_bank_available;
    SpiirState **spstate;
    gboolean spstate_initialised;

    gint stream_id;
    gint deviceID;
    cudaStream_t stream;

    gint gap_handle;

    float *h_snglsnr_buffer;
    gsize len_snglsnr_buffer;
    double offset_per_nanosecond;
};

struct _CudaMultirateSPIIRClass {
    GstBaseTransformClass cuda_multiratespiir_parent_class;
};

GType cuda_multiratespiir_get_type(void);

G_END_DECLS

#endif /* __CUDA_MULTIRATESPIIR_H__ */
