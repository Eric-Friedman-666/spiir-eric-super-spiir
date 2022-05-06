/*
 * Copyright (C) 2020 Qi Chu <qi.chu@ligo.org>
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

#ifndef __TRIGGER_JOINTER_H__
#define __TRIGGER_JOINTER_H__

#include <glib.h>
#include <gst/base/gstadapter.h>
#include <gst/base/gstcollectpads.h>
#include <gst/gst.h>
#include <lal/LIGOMetadataTables.h>
#include <pipe_macro.h>
#include <postcohtable.h>

G_BEGIN_DECLS

#define GSTLAL_TYPE_TRIGGER_JOINTER (trigger_jointer_get_type())
#define TRIGGER_JOINTER(obj)                                                   \
    (G_TYPE_CHECK_INSTANCE_CAST((obj), GSTLAL_TYPE_TRIGGER_JOINTER,            \
                                TriggerJointer))
#define TRIGGER_JOINTER_CLASS(klass)                                           \
    (G_TYPE_CHECK_CLASS_CAST((klass), GSTLAL_TYPE_TRIGGER_JOINTER,             \
                             TriggerJointerClass))
#define GST_IS_TRIGGER_JOINTER(obj)                                            \
    (G_TYPE_CHECK_INSTANCE_TYPE((obj), GSTLAL_TYPE_TRIGGER_JOINTER))
#define GST_IS_TRIGGER_JOINTER_CLASS(klass)                                    \
    (G_TYPE_CHECK_CLASS_TYPE((klass), GSTLAL_TYPE_TRIGGER_JOINTER))

typedef struct _TriggerJointer TriggerJointer;
typedef struct _TriggerJointerClass TriggerJointerClass;

#ifndef DEFINED_COMPLEX_F
#define DEFINED_COMPLEX_F

typedef struct _Complex_F {
    float re;
    float im;
} COMPLEX_F;

#else
#endif

#define JOINTER_PARAMS_NOT_INIT -1
#define JOINTER_PARAMS_INIT     1

typedef struct _TriggerJointerCollectData TriggerJointerCollectData;
typedef void (*TriggerJointerPeakfinder)(gpointer d_snglsnr, gint size);

struct _TriggerJointerCollectData {
    GstCollectData data;
    gboolean is_snr;
    gboolean is_aligned;
    GstCollectDataDestroyNotify destroy_notify;
    /* the following structures are only used for snr pads */
    gchar *ifo_name;
    gint ifo_mapping;
    gint rate;
    gint channels;
    gint width;
    gint bps;
    gint ntmplt;
    GstClockTime timelag; // IFO-dependent timelag in nano seconds
    gint ntimelag; // IFO-dependent timelag in offset (number of samples)
    gint preserved_len;
    GstClockTime next_tstart; // expected next buffer time
    GstAdapter *adapter;
    GArray *flag_segments;
};

/**
 * TriggerJointer:
 *
 * Opaque data structure.
 */
struct _TriggerJointer {
    GstElement element;

    /* <private> */
    GstPad *srcpad;
    /* sink pads composed of a number of SNR pads and one postcoh pad */
    GSList *collect_snrdata;
    TriggerJointerCollectData *collect_postcohdata;
    /* boilder plate collect pads
     * pad added to this strucuture
     * will invoke collected function
     * if buf comes in */
    GstCollectPads *collect;

    gboolean is_t0_set;
    gboolean is_snr_info_set;
    gboolean is_all_aligned;
    gboolean is_next_tstart_set;
    double offset_per_nanosecond;

    GstClockTime t0;
    gint output_skymap;

    gint autochisq_len;
};

struct _TriggerJointerClass {
    GstElementClass parent_class;
};

GType trigger_jointer_get_type(void);

G_END_DECLS

#endif /* __TRIGGER_JOINTER_H__ */
