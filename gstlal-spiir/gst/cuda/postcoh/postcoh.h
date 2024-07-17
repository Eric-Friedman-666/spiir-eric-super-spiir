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

#ifndef __CUDA_POSTCOH_H__
#define __CUDA_POSTCOH_H__

// Standard and 3rd party includes
#include <cuda_runtime.h>
#include <glib.h>
#include <gst/base/gstadapter.h>
#include <gst/base/gstcollectpads.h>
#include <gst/gst.h>

// Our includes
#include <lal/LIGOMetadataTables.h>
#include <pipe_macro.h>
#include <postcoh/postcoh_state.h>

G_BEGIN_DECLS

#define CUDA_TYPE_POSTCOH (cuda_postcoh_get_type())
#define CUDA_POSTCOH(obj)                                                      \
    (G_TYPE_CHECK_INSTANCE_CAST((obj), CUDA_TYPE_POSTCOH, CudaPostcoh))
#define CUDA_POSTCOH_CLASS(klass)                                              \
    (G_TYPE_CHECK_CLASS_CAST((klass), CUDA_TYPE_POSTCOH, CudaPostcohClass))
#define GST_IS_CUDA_POSTCOH(obj)                                               \
    (G_TYPE_CHECK_INSTANCE_TYPE((obj), CUDA_TYPE_POSTCOH))
#define GST_IS_CUDA_POSTCOH_CLASS(klass)                                       \
    (G_TYPE_CHECK_CLASS_TYPE((klass), CUDA_TYPE_POSTCOH))

typedef struct _CudaPostcoh CudaPostcoh;
typedef struct _CudaPostcohClass CudaPostcohClass;

typedef struct _GstPostcohCollectData GstPostcohCollectData;
typedef void (*CudaPostcohPeakfinder)(gpointer d_snglsnr, gint size);

struct _GstPostcohCollectData {
    GstCollectData data;
    gchar *ifo_name;
    GstAdapter *adapter;
    double offset_per_nanosecond;
    gint channels;
    gboolean is_aligned;
    guint64 aligned_offset0;
    guint64 next_offset;
    GstCollectDataDestroyNotify destroy_notify;
    GArray *flag_segments;
};

/**
 * CudaPostcoh:
 *
 * Opaque data structure.
 */
struct _CudaPostcoh {
    GstElement element;

    /* <private> */
    GstPad *srcpad;
    GstCollectPads *collect;

    gint rate;
    gint channels;
    gint width;
    guint bps;

    char *detrsp_fname;
    char *spiir_bank_fname;
    gint exe_len;
    gsize exe_size;
    gint one_take_len;
    gsize one_take_size;
    gint snglsnr_cpy_len;
    gsize snglsnr_cpy_size;
    gint preserved_len;
    gint head_len;
    float max_dt;
    gboolean set_starttime;
    gboolean is_all_aligned;
    double offset_per_nanosecond;

    GstClockTime t0;
    GstClockTime next_exe_t;
    guint64 offset0;
    guint64 samples_in;
    guint64 samples_out;

    PostcohState *state;
    float snglsnr_thresh;
    float cohsnr_thresh;
    GMutex prop_lock;
    GCond prop_avail;
    gint hist_trials;
    float trial_interval;
    gint trial_interval_in_samples;
    gint output_skymap;

    char *sngl_tmplt_fname;
    SnglInspiralTable *sngl_table;

    gboolean enable_signal_removal_bg;
    float signal_removal_bg_threshold;

    COMPLEX_F *one_take_snr[MAX_NIFO];

    gint stream_id;
    gint device_id;
    /* book-keeping */
    long process_id;
    long cur_event_id;
    cudaStream_t stream;
    GstClockTime t_roll_start;
    int refresh_interval;
};

struct _CudaPostcohClass {
    GstElementClass cuda_postcoh_parent_class;
};

GType cuda_postcoh_get_type(void);

G_END_DECLS

#endif /* __CUDA_POSTCOH_H__ */
