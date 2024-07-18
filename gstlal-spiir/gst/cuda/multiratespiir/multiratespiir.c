/*
 * Copyright (C) 2014 Qi Chu
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Library General Public
 * License as published by the Free Software Foundation; either
 * version 2 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Library General Public License for more deroll-offss.
 *
 * You should have received a copy of the GNU Library General Public
 * License along with this library; if not, write to the
 * Free Software Foundation, Inc., 59 Temple Place - Suite 330,
 * Boston, MA 02111-1307, USA.
 */

/**
 * SECTION:element-MultirateSPIIR
 *
 * gst-launch -v
 */

/* TODO:
 *  - no update of SpiirState at run time. should support streaming format
 *  changes such as width/ rate/ quality change at run time. Should
 *  support IIR bank changes at run time.
 */
#include <cuda_debug.h>
#include <cuda_runtime.h>
#include <flag_segment.h>
#include <glib.h>
#include <gst/base/gstadapter.h>
#include <gst/base/gstbasetransform.h>
#include <gst/gst.h>
#include <gstlal/gstlal.h>
#include <math.h>
#include <multiratespiir/multiratespiir.h>
#include <multiratespiir/multiratespiir_kernel.h>
#include <multiratespiir/multiratespiir_utils.h>
#include <stdio.h>
#include <string.h>

#define GST_CAT_DEFAULT cuda_multiratespiir_debug
GST_DEBUG_CATEGORY_STATIC(GST_CAT_DEFAULT);

/*
 * add a segment to the control segment array.  note they are appended, and
 * the code assumes they are in order and do not overlap, see gstlal_gate.c
 * control_segment
 */

static GstFlowReturn push_with_flag_segments(CudaMultirateSPIIR *element,
                                             GstBuffer *outbuf) {
    GstClockTime start    = GST_BUFFER_PTS(outbuf),
                 stop     = start + GST_BUFFER_DURATION(outbuf);
    GArray *flag_segments = element->flag_segments;

    /* The final segment must end after outbuf */
    g_assert(flag_segments->len > 0);
    FlagSegment *final_segment =
      &g_array_index(flag_segments, FlagSegment, flag_segments->len - 1);
    g_assert(final_segment->stop >= stop);

    guint pushed_len       = 0;
    gboolean is_buf_intact = TRUE;
    GstFlowReturn ret;
    guint flush_len = 0;
    for (guint i = 0; i < flag_segments->len && stop > start; i++) {
        // Push a subbuffer for each segment's overlap with the buffer
        FlagSegment *this_segment =
          &g_array_index(flag_segments, FlagSegment, i);

        if (this_segment->start > stop) break;

        if (this_segment->stop < start) {
            flush_len = i + 1;
            continue;
        }

        GstClockTime sub_start = MAX(this_segment->start, start);
        GstClockTime sub_stop  = MIN(this_segment->stop, stop);
        guint sub_len          = round((double)(sub_stop - sub_start)
                              * element->offset_per_nanosecond);
        gsize sub_size         = (gsize)sub_len * element->bps;

        GST_DEBUG_OBJECT(
          element,
          "number of segments %d, processing %d, start %" GST_TIME_FORMAT
          ", stop %" GST_TIME_FORMAT " segment start %" GST_TIME_FORMAT
          ", segment stop %" GST_TIME_FORMAT " sub_len %" G_GUINT32_FORMAT
          ", sub_size %" G_GSIZE_FORMAT,
          flag_segments->len, i, GST_TIME_ARGS(start), GST_TIME_ARGS(stop),
          GST_TIME_ARGS(this_segment->start), GST_TIME_ARGS(this_segment->stop),
          sub_len, sub_size);

        if (sub_len > 0) {
            gsize sub_offset     = element->offset0 + element->samples_out;
            gsize sub_offset_end = sub_offset + sub_len;
            gsize sub_offset_from_outbuf =
              (sub_offset - GST_BUFFER_OFFSET(outbuf)) * element->bps;
            /* note that the buf->data is gunit8 *, so need to calculate the
             * offset for subbuf */
            GstBuffer *subbuf = gst_buffer_copy_region(
              outbuf,
              GST_BUFFER_COPY_FLAGS | GST_BUFFER_COPY_TIMESTAMPS
                | GST_BUFFER_COPY_META | GST_BUFFER_COPY_MEMORY,
              sub_offset_from_outbuf, sub_size);
            if (!subbuf) {
                GST_ERROR_OBJECT(element, "failing creating sub-buffer");
                return GST_FLOW_ERROR;
            }

            if (sub_offset_from_outbuf > 0
                || sub_size != gst_buffer_get_size(outbuf)) {
                subbuf                      = gst_buffer_make_writable(subbuf);
                GST_BUFFER_DURATION(subbuf) = sub_stop - sub_start;
                GST_BUFFER_OFFSET_END(subbuf) = sub_offset_end;
                if (sub_offset_from_outbuf > 0) {
                    GST_BUFFER_PTS(subbuf)    = sub_start;
                    GST_BUFFER_OFFSET(subbuf) = sub_offset;
                }
            }

            if (this_segment->is_gap) {
                GST_BUFFER_FLAG_SET(subbuf, GST_BUFFER_FLAG_GAP);
            }

            GST_LOG_OBJECT(
              element,
              "Creating sub buffer (EXPECTED, ACTUAL):\n"
              "size (%" G_GSIZE_FORMAT ", %" G_GSIZE_FORMAT "),\n"
              "timestamp (%" GST_TIME_FORMAT ", %" GST_TIME_FORMAT "),\n"
              "duration (%" GST_TIME_FORMAT ", %" GST_TIME_FORMAT "),\n"
              "offset (%" G_GUINT64_FORMAT ", %" G_GUINT64_FORMAT "),\n"
              "offset_end (%" G_GUINT64_FORMAT ", %" G_GUINT64_FORMAT ")",
              sub_size, gst_buffer_get_size(subbuf), GST_TIME_ARGS(sub_start),
              GST_TIME_ARGS(GST_BUFFER_PTS(subbuf)),
              GST_TIME_ARGS(sub_stop - sub_start),
              GST_TIME_ARGS(GST_BUFFER_DURATION(subbuf)), sub_offset,
              GST_BUFFER_OFFSET(subbuf), sub_offset_end,
              GST_BUFFER_OFFSET_END(subbuf));

            ret = gst_pad_push(element->srcpad, subbuf);
            GST_LOG_OBJECT(element, "pushed sub buffer, result = %s",
                           gst_flow_get_name(ret));
            is_buf_intact = FALSE;
            element->samples_out += sub_len;
            start = sub_stop;
            pushed_len += sub_len;
        }
    }
    g_assert(pushed_len
             == GST_BUFFER_OFFSET_END(outbuf) - GST_BUFFER_OFFSET(outbuf));

    if (flush_len > 0) g_array_remove_range(flag_segments, 0, flush_len);

    if (is_buf_intact) {
        gst_buffer_ref(outbuf); /* need the transform to free it */
        ret = gst_pad_push(element->srcpad, outbuf);
        GST_LOG_OBJECT(element, "pushed original buffer, result = %s",
                       gst_flow_get_name(ret));
    }
    return GST_FLOW_OK;
}

G_DEFINE_TYPE_WITH_CODE(CudaMultirateSPIIR,
                        cuda_multiratespiir,
                        GST_TYPE_BASE_TRANSFORM,
                        GST_DEBUG_CATEGORY_INIT(GST_CAT_DEFAULT,
                                                "cuda_multiratespiir",
                                                0,
                                                "cuda_multiratespiir element"))

enum { PROP_0, PROP_IIRBANK_FNAME, PROP_GAP_HANDLE, PROP_STREAM_ID };

// FIXME: not support width=64 yet
static GstStaticPadTemplate cuda_multiratespiir_sink_template =
  GST_STATIC_PAD_TEMPLATE("sink",
                          GST_PAD_SINK,
                          GST_PAD_ALWAYS,
                          GST_STATIC_CAPS("audio/x-raw, "
                                          "format = (string) F32LE,"
                                          "rate = (int) [1, MAX], "
                                          "channels = (int) 1, "
                                          "width = (int) 32"));

static GstStaticPadTemplate cuda_multiratespiir_src_template =
  GST_STATIC_PAD_TEMPLATE("src",
                          GST_PAD_SRC,
                          GST_PAD_ALWAYS,
                          GST_STATIC_CAPS("audio/x-raw, "
                                          "format = (string) F32LE,"
                                          "rate = (int) [1, MAX], "
                                          "channels = (int) [1, MAX], "
                                          "width = (int) 32"));

static void cuda_multiratespiir_set_property(GObject *object,
                                             guint prop_id,
                                             const GValue *value,
                                             GParamSpec *pspec);
static void cuda_multiratespiir_get_property(GObject *object,
                                             guint prop_id,
                                             GValue *value,
                                             GParamSpec *pspec);

/* vmethods */
static gboolean cuda_multiratespiir_get_unit_size(GstBaseTransform *base,
                                                  GstCaps *caps,
                                                  gsize *size);
static GstCaps *cuda_multiratespiir_transform_caps(GstBaseTransform *base,
                                                   GstPadDirection direction,
                                                   GstCaps *caps,
                                                   GstCaps *filter);
static gboolean cuda_multiratespiir_set_caps(GstBaseTransform *base,
                                             GstCaps *incaps,
                                             GstCaps *outcaps);
static GstFlowReturn cuda_multiratespiir_transform(GstBaseTransform *base,
                                                   GstBuffer *inbuf,
                                                   GstBuffer *outbuf);
static gboolean cuda_multiratespiir_transform_size(GstBaseTransform *base,
                                                   GstPadDirection direction,
                                                   GstCaps *caps,
                                                   gsize size,
                                                   GstCaps *othercaps,
                                                   gsize *othersize);
static gboolean cuda_multiratespiir_sink_event(GstBaseTransform *base,
                                               GstEvent *event);
static gboolean cuda_multiratespiir_start(GstBaseTransform *base);
static gboolean cuda_multiratespiir_stop(GstBaseTransform *base);

/*
 * class_init()
 */

static void cuda_multiratespiir_class_init(CudaMultirateSPIIRClass *klass) {
    GObjectClass *gobject_class        = (GObjectClass *)klass;
    GstElementClass *gst_element_class = GST_ELEMENT_CLASS(klass);

    gst_element_class_set_metadata(
      gst_element_class, "Multirate SPIIR",
      "multi level downsample + spiir + upsample",
      "single rate data stream -> multi template SNR streams",
      "Qi Chu <qi.chu@ligo.org>");

    gobject_class->set_property =
      GST_DEBUG_FUNCPTR(cuda_multiratespiir_set_property);
    gobject_class->get_property =
      GST_DEBUG_FUNCPTR(cuda_multiratespiir_get_property);

    gst_element_class_add_pad_template(
      gst_element_class,
      gst_static_pad_template_get(&cuda_multiratespiir_src_template));
    gst_element_class_add_pad_template(
      gst_element_class,
      gst_static_pad_template_get(&cuda_multiratespiir_sink_template));

    g_object_class_install_property(
      gobject_class, PROP_IIRBANK_FNAME,
      g_param_spec_string(
        "bank-fname", "The file of IIR bank feedback coefficients",
        "A parallel bank of first order IIR filter feedback coefficients.",
        NULL, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_GAP_HANDLE,
      g_param_spec_int("gap-handle", "gap handling",
                       "restart after gap (1), or gap is treated as 0 (0)", 0,
                       1, 0, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_STREAM_ID,
      g_param_spec_int("stream-id", "id for cuda stream", "id for cuda stream",
                       0, G_MAXINT, 0,
                       G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    GstBaseTransformClass *transform_class = GST_BASE_TRANSFORM_CLASS(klass);
    transform_class->start = GST_DEBUG_FUNCPTR(cuda_multiratespiir_start);
    transform_class->stop  = GST_DEBUG_FUNCPTR(cuda_multiratespiir_stop);
    transform_class->get_unit_size =
      GST_DEBUG_FUNCPTR(cuda_multiratespiir_get_unit_size);
    transform_class->transform_caps =
      GST_DEBUG_FUNCPTR(cuda_multiratespiir_transform_caps);
    transform_class->set_caps = GST_DEBUG_FUNCPTR(cuda_multiratespiir_set_caps);
    transform_class->transform =
      GST_DEBUG_FUNCPTR(cuda_multiratespiir_transform);
    transform_class->transform_size =
      GST_DEBUG_FUNCPTR(cuda_multiratespiir_transform_size);
    transform_class->sink_event =
      GST_DEBUG_FUNCPTR(cuda_multiratespiir_sink_event);
}

/*
 * instance init
 */

static void cuda_multiratespiir_init(CudaMultirateSPIIR *element) {
    //  GstBaseTransform *trans = GST_BASE_TRANSFORM (element);
    g_mutex_init(&element->iir_bank_lock);
    g_cond_init(&element->iir_bank_available);
    element->bank_fname          = NULL;
    element->num_depths          = 0;
    element->outchannels         = 0;
    element->spstate             = NULL;
    element->spstate_initialised = FALSE;
    element->num_exe_samples     = 4096; // assumes the rate=4096Hz
    element->num_head_cover_samples =
      13120; // assumes the rate=4096Hz, down quality = 9
    element->num_tail_cover_samples = 13104; // assumes the rate=4096Hz

    element->h_snglsnr_buffer   = NULL;
    element->len_snglsnr_buffer = 0;
    element->srcpad = gst_element_get_static_pad(GST_ELEMENT(element), "src");
}

/* vmethods */
static gboolean cuda_multiratespiir_start(GstBaseTransform *base) {
    CudaMultirateSPIIR *element = CUDA_MULTIRATESPIIR(base);

    element->adapter       = gst_adapter_new();
    element->flag_segments = g_array_new(FALSE, FALSE, sizeof(FlagSegment));

    element->need_discont    = TRUE;
    element->num_gap_samples = 0;
    element->need_tail_drain = FALSE;
    element->t0              = GST_CLOCK_TIME_NONE;
    element->offset0         = GST_BUFFER_OFFSET_NONE;
    element->next_in_offset  = GST_BUFFER_OFFSET_NONE;
    element->samples_in      = 0;
    element->samples_out     = 0;
    return TRUE;
}

static gboolean cuda_multiratespiir_stop(GstBaseTransform *base) {
    CudaMultirateSPIIR *element = CUDA_MULTIRATESPIIR(base);

    g_mutex_clear(&element->iir_bank_lock);
    g_cond_clear(&element->iir_bank_available);

    if (element->spstate) {
        spiir_state_destroy(element->spstate, element->num_depths);
    }

    g_object_unref(element->adapter);
    element->adapter = NULL;
    g_array_unref(element->flag_segments);
    element->flag_segments = NULL;

    return TRUE;
}

static gboolean cuda_multiratespiir_get_unit_size(GstBaseTransform *base,
                                                  GstCaps *caps,
                                                  gsize *size) {
    gint width, channels;
    GstStructure *structure;
    gboolean ret;

    g_return_val_if_fail(size != NULL, FALSE);

    /* this works for both float and int */
    structure = gst_caps_get_structure(caps, 0);
    ret       = gst_structure_get_int(structure, "width", &width);
    ret &= gst_structure_get_int(structure, "channels", &channels);

    if (G_UNLIKELY(!ret)) return FALSE;

    *size = (gsize)(width / 8) * channels;
    GST_DEBUG_OBJECT(base, "get unit size of caps %" G_GSIZE_FORMAT, *size);

    return TRUE;
}

static GstCaps *cuda_multiratespiir_transform_caps(GstBaseTransform *base,
                                                   GstPadDirection direction,
                                                   GstCaps *caps,
                                                   GstCaps *filter) {
    CudaMultirateSPIIR *element = CUDA_MULTIRATESPIIR(base);

    GstCaps *othercaps;

    othercaps = gst_caps_copy(caps);

    switch (direction) {
    case GST_PAD_SRC:
        /*
         * sink caps is the same with src caps, except it only has 1 channel
         */

        gst_structure_set(gst_caps_get_structure(othercaps, 0), "channels",
                          G_TYPE_INT, 1, NULL);
        GST_LOG("setting channels to 1\n");
        break;

    case GST_PAD_SINK:
        /*
         * src caps is the same with sink caps, except it only has number of
         * channels that equals to the number of templates
         */
        // if (!g_mutex_trylock(&element->iir_bank_lock))
        // printf("lock by another thread");
        g_mutex_lock(&element->iir_bank_lock);
        if (!element->spstate)
            g_cond_wait(&element->iir_bank_available, &element->iir_bank_lock);

        gst_structure_set(gst_caps_get_structure(othercaps, 0), "channels",
                          G_TYPE_INT,
                          cuda_multiratespiir_get_outchannels(element), NULL);
        g_mutex_unlock(&element->iir_bank_lock);
        break;

    case GST_PAD_UNKNOWN:
        GST_ELEMENT_ERROR(base, CORE, NEGOTIATION, (NULL),
                          ("invalid direction GST_PAD_UNKNOWN"));
        gst_caps_unref(othercaps);
        return GST_CAPS_NONE;
    }

    if (filter) {
        GstCaps *intersection = gst_caps_intersect(othercaps, filter);
        gst_caps_unref(othercaps);
        othercaps = intersection;
    }

    return othercaps;
}

// Note: sizes calculated here are uplimit sizes, not necessarily the true
// sizes.

static gboolean cuda_multiratespiir_transform_size(GstBaseTransform *base,
                                                   GstPadDirection direction,
                                                   GstCaps *caps,
                                                   gsize size,
                                                   GstCaps *othercaps,
                                                   gsize *othersize) {
    CudaMultirateSPIIR *element = CUDA_MULTIRATESPIIR(base);
    gboolean ret                = TRUE;

    gsize unit_size, other_unit_size;
    GST_LOG_OBJECT(
      base, "asked to transform size %" G_GSIZE_FORMAT " in direction %s", size,
      direction == GST_PAD_SINK ? "SINK" : "SRC");

    if (!cuda_multiratespiir_get_unit_size(base, caps, &unit_size))
        return FALSE;

    if (!cuda_multiratespiir_get_unit_size(base, othercaps, &other_unit_size))
        return FALSE;

    if (direction == GST_PAD_SINK) {
        /*
         * asked to convert size of an incoming buffer. The output size
         * is the uplimit size.
         */
        //    g_assert(element->bank_initialised == TRUE);
        GST_LOG_OBJECT(base, "available samples  %d",
                       cuda_multiratespiir_get_available_samples(element));
        *othersize = (size / unit_size
                      + cuda_multiratespiir_get_available_samples(element))
                     * other_unit_size;
    } else {
        /* asked to convert size of an outgoing buffer.
         */
        *othersize = (size / unit_size) * other_unit_size;
    }

    GST_LOG_OBJECT(base,
                   "transformed size %" G_GSIZE_FORMAT " to %" G_GSIZE_FORMAT,
                   size, *othersize);

    return ret;
}

static gboolean cuda_multiratespiir_set_caps(GstBaseTransform *base,
                                             GstCaps *incaps,
                                             GstCaps *outcaps) {
    CudaMultirateSPIIR *element = CUDA_MULTIRATESPIIR(base);
    GstStructure *s;
    gint rate;
    gint channels;
    gint width;
    gboolean success = TRUE;

    GST_LOG_OBJECT(element,
                   "incaps %" GST_PTR_FORMAT ", outcaps %" GST_PTR_FORMAT,
                   incaps, outcaps);

    s = gst_caps_get_structure(outcaps, 0);
    success &= gst_structure_get_int(s, "channels", &channels);
    success &= gst_structure_get_int(s, "width", &width);
    success &= gst_structure_get_int(s, "rate", &rate);

    g_mutex_lock(&element->iir_bank_lock);
    if (!element->spstate)
        g_cond_wait(&element->iir_bank_available, &element->iir_bank_lock);

    if (!success) {
        GST_ERROR_OBJECT(element,
                         "unable to parse and/or accept caps %" GST_PTR_FORMAT,
                         outcaps);
    }

    if (channels != (gint)cuda_multiratespiir_get_outchannels(element)) {
        /* impossible to happen */
        GST_ERROR_OBJECT(element, "channels != %d in %" GST_PTR_FORMAT,
                         cuda_multiratespiir_get_outchannels(element), outcaps);
        success = FALSE;
    }

    if (width != (gint)element->width) {
        /*
         * FIXME :do not support width change at run time
         */
        GST_ERROR_OBJECT(element, "width != %d in %" GST_PTR_FORMAT,
                         element->width, outcaps);
        success = FALSE;
    }

    if (rate != (gint)element->rate) {
        /*
         * FIXME: do not support rate change at run time
         */
        GST_ERROR_OBJECT(element, "rate != %d in %" GST_PTR_FORMAT,
                         element->rate, outcaps);
        success = FALSE;
    }
    element->bps                   = width / 8 * channels; // bytes per sample
    element->offset_per_nanosecond = element->rate / 1e9;
    /* transform_caps already done, num_depths already set */

    g_mutex_unlock(&element->iir_bank_lock);
    return success;
}

static GstFlowReturn cuda_multiratespiir_assemble_gap_buffer(
  CudaMultirateSPIIR *element, guint len, GstBuffer *gapbuf) {
    gsize outsize = (gsize)len * element->bps;
    gst_buffer_set_size(gapbuf, outsize);

    /* time */
    if (GST_CLOCK_TIME_IS_VALID(element->t0)) {
        GST_BUFFER_PTS(gapbuf) =
          element->t0
          + gst_util_uint64_scale_int_round(element->samples_out, GST_SECOND,
                                            element->rate);
        GST_BUFFER_DURATION(gapbuf) =
          element->t0
          + gst_util_uint64_scale_int_round(element->samples_out + len,
                                            GST_SECOND, element->rate)
          - GST_BUFFER_PTS(gapbuf);
    } else {
        GST_BUFFER_PTS(gapbuf)      = GST_CLOCK_TIME_NONE;
        GST_BUFFER_DURATION(gapbuf) = GST_CLOCK_TIME_NONE;
    }
    /* offset */
    if (element->offset0 != GST_BUFFER_OFFSET_NONE) {
        GST_BUFFER_OFFSET(gapbuf)     = element->offset0 + element->samples_out;
        GST_BUFFER_OFFSET_END(gapbuf) = GST_BUFFER_OFFSET(gapbuf) + len;
    } else {
        GST_BUFFER_OFFSET(gapbuf)     = GST_BUFFER_OFFSET_NONE;
        GST_BUFFER_OFFSET_END(gapbuf) = GST_BUFFER_OFFSET_NONE;
    }

    if (element->need_discont) {
        GST_BUFFER_FLAG_SET(gapbuf, GST_BUFFER_FLAG_DISCONT);
        element->need_discont = FALSE;
    }

    GST_BUFFER_FLAG_SET(gapbuf, GST_BUFFER_FLAG_GAP);

    /* move along */
    element->samples_out += len;
    element->samples_in += len;

    GST_LOG_OBJECT(
      element,
      "Assembled gap buffer of %" G_GSIZE_FORMAT
      " bytes with timestamp %" GST_TIME_FORMAT " duration %" GST_TIME_FORMAT
      " offset %" G_GUINT64_FORMAT " offset_end %" G_GUINT64_FORMAT,
      gst_buffer_get_size(gapbuf), GST_TIME_ARGS(GST_BUFFER_PTS(gapbuf)),
      GST_TIME_ARGS(GST_BUFFER_DURATION(gapbuf)), GST_BUFFER_OFFSET(gapbuf),
      GST_BUFFER_OFFSET_END(gapbuf));

    if (outsize == 0) {
        GST_DEBUG_OBJECT(element, "buffer dropped");
        return GST_BASE_TRANSFORM_FLOW_DROPPED;
    }

    return GST_FLOW_OK;
}

static GstFlowReturn cuda_multiratespiir_push_gap(CudaMultirateSPIIR *element,
                                                  guint gap_len) {
    gsize outsize     = gap_len * sizeof(float) * element->outchannels;
    GstBuffer *gapbuf = gst_buffer_new_allocate(NULL, outsize, NULL);
    if (G_UNLIKELY(!gapbuf)) {
        GST_WARNING_OBJECT(element,
                           "Failed to allocate buffer of size %" G_GSIZE_FORMAT,
                           outsize);
        return GST_FLOW_ERROR;
    }

    // FIXME: no sanity check
    GstFlowReturn res =
      cuda_multiratespiir_assemble_gap_buffer(element, gap_len, gapbuf);

    res = gst_pad_push(GST_BASE_TRANSFORM_SRC_PAD(element), gapbuf);

    if (G_UNLIKELY(res != GST_FLOW_OK))
        GST_WARNING_OBJECT(element, "Failed to push gap: %s",
                           gst_flow_get_name(res));
    return res;
}

static GstFlowReturn cuda_multiratespiir_push_drain(CudaMultirateSPIIR *element,
                                                    guint in_len) {
    /* To restore the buffer timestamp, out length must be equal to in length */
    guint out_len = 0;
    if (element->num_exe_samples == (guint)element->rate) {
        out_len = in_len;
    } else {
        out_len = in_len - element->num_tail_cover_samples;
    }

    gsize outsize     = (gsize)out_len * sizeof(float) * element->outchannels;
    GstBuffer *outbuf = gst_buffer_new_allocate(NULL, outsize, NULL);
    if (G_UNLIKELY(!outbuf)) {
        GST_WARNING_OBJECT(
          element, "failed allocating buffer of %" G_GSIZE_FORMAT " bytes",
          outsize);
        return GST_FLOW_ERROR;
    }

    GstMapInfo mapInfo;
    gst_buffer_map(outbuf, &mapInfo, GST_MAP_WRITE);
    memset(mapInfo.data, 0, outsize);
    float *outdata = (float *)mapInfo.data;

    guint num_in_multidown = MIN(in_len, element->num_exe_samples),
          old_in_len = in_len, total_num_out_spiirup = 0;
    while (num_in_multidown > 0) {
        gsize adapter_size = (gsize)(num_in_multidown * sizeof(float));
        g_assert(gst_adapter_available(element->adapter) >= adapter_size);
        const float *in_multidown =
          (const float *)gst_adapter_map(element->adapter, adapter_size);

        gint num_out_multidown =
          multi_downsample(element->spstate, in_multidown, num_in_multidown,
                           element->num_depths, element->stream);
        float *pos_out =
          outdata + total_num_out_spiirup * (element->outchannels);
        gint num_out_spiirup =
          spiirup(element->spstate, num_out_multidown, element->num_depths,
                  pos_out, element->stream);

        /* move along */
        gst_adapter_unmap(element->adapter);
        gst_adapter_flush(element->adapter, adapter_size);
        in_len -= total_num_out_spiirup;
        /* after the first filtering, update the exe_samples to the rate */
        cuda_multiratespiir_update_exe_samples(&element->num_exe_samples,
                                               element->rate);
        num_in_multidown = MIN(in_len, element->num_exe_samples);
        total_num_out_spiirup += num_out_spiirup;
    }

    gst_buffer_unmap(outbuf, &mapInfo);

    g_assert((guint)total_num_out_spiirup <= out_len);

    /* time */
    if (GST_CLOCK_TIME_IS_VALID(element->t0)) {
        GST_BUFFER_PTS(outbuf) =
          element->t0
          + gst_util_uint64_scale_int_round(element->samples_out, GST_SECOND,
                                            element->rate);
        GST_BUFFER_DURATION(outbuf) =
          element->t0
          + gst_util_uint64_scale_int_round(element->samples_out + out_len,
                                            GST_SECOND, element->rate)
          - GST_BUFFER_PTS(outbuf);
    } else {
        GST_BUFFER_PTS(outbuf)      = GST_CLOCK_TIME_NONE;
        GST_BUFFER_DURATION(outbuf) = GST_CLOCK_TIME_NONE;
    }
    /* offset */
    if (element->offset0 != GST_BUFFER_OFFSET_NONE) {
        GST_BUFFER_OFFSET(outbuf)     = element->offset0 + element->samples_out;
        GST_BUFFER_OFFSET_END(outbuf) = GST_BUFFER_OFFSET(outbuf) + out_len;
    } else {
        GST_BUFFER_OFFSET(outbuf)     = GST_BUFFER_OFFSET_NONE;
        GST_BUFFER_OFFSET_END(outbuf) = GST_BUFFER_OFFSET_NONE;
    }

    if (element->need_discont) {
        GST_BUFFER_FLAG_SET(outbuf, GST_BUFFER_FLAG_DISCONT);
        element->need_discont = FALSE;
    }

    element->samples_out += out_len;
    element->samples_in += old_in_len;

    gst_buffer_set_size(outbuf, outsize);

    GST_LOG_OBJECT(element,
                   "Push_drain: Converted to buffer of %" G_GUINT32_FORMAT
                   " samples (%" G_GSIZE_FORMAT
                   " bytes) with timestamp %" GST_TIME_FORMAT
                   ", duration %" GST_TIME_FORMAT ", offset %" G_GUINT64_FORMAT
                   ", offset_end %" G_GUINT64_FORMAT,
                   out_len, gst_buffer_get_size(outbuf),
                   GST_TIME_ARGS(GST_BUFFER_PTS(outbuf)),
                   GST_TIME_ARGS(GST_BUFFER_DURATION(outbuf)),
                   GST_BUFFER_OFFSET(outbuf), GST_BUFFER_OFFSET_END(outbuf));

    if (outsize == 0) {
        GST_DEBUG_OBJECT(element, "buffer dropped");
        gst_object_unref(outbuf);
        return GST_BASE_TRANSFORM_FLOW_DROPPED;
    }

    GstFlowReturn res =
      gst_pad_push(GST_BASE_TRANSFORM_SRC_PAD(element), outbuf);

    if (G_UNLIKELY(res != GST_FLOW_OK))
        GST_WARNING_OBJECT(element, "Failed to push drain: %s",
                           gst_flow_get_name(res));
    return res;

    return GST_FLOW_OK;
}

static GstFlowReturn cuda_multiratespiir_process(CudaMultirateSPIIR *element,
                                                 guint in_len,
                                                 GstBuffer *outbuf) {
    /* To restore the buffer timestamp, out length must be equal to in length */
    guint out_len = 0;
    if (element->num_exe_samples == (guint)element->rate) {
        out_len = in_len;
    } else {
        out_len = in_len - element->num_tail_cover_samples;
    }

    // To accelerate gpu memory copy, first gpu->cpu(pinned
    // memory)->cpu(gstbuffer) remember copy from h_snglsnr_buffer to gstbuffer.
    g_assert(element->len_snglsnr_buffer > 0
             || (element->len_snglsnr_buffer == 0
                 && element->h_snglsnr_buffer == NULL));
    // This could be allocated once based on num_exe_samples rather than
    // resizing. See #113
    gsize outsize = (gsize)out_len * element->bps;
    if (outsize > element->len_snglsnr_buffer) {
        if (element->h_snglsnr_buffer != NULL) {
            cudaFreeHost(element->h_snglsnr_buffer);
        }
        cudaMallocHost((void **)&element->h_snglsnr_buffer, outsize);
        element->len_snglsnr_buffer = outsize;
    }
    float *outdata = element->h_snglsnr_buffer;

    guint num_in_multidown = MIN(in_len, element->num_exe_samples),
          old_in_len = in_len, total_num_out_spiirup = 0;
    while (num_in_multidown > 0) {
        gsize adapter_size = (gsize)num_in_multidown * sizeof(float);
        g_assert(gst_adapter_available(element->adapter) >= adapter_size);
        const float *in_multidown =
          gst_adapter_map(element->adapter, adapter_size);

        gint num_out_multidown =
          multi_downsample(element->spstate, in_multidown, num_in_multidown,
                           element->num_depths, element->stream);
        float *pos_out =
          outdata + total_num_out_spiirup * (element->outchannels);
        gint num_out_spiirup =
          spiirup(element->spstate, num_out_multidown, element->num_depths,
                  pos_out, element->stream);

        /* move along */
        gst_adapter_unmap(element->adapter);
        gst_adapter_flush(element->adapter, adapter_size);
        in_len -= num_in_multidown;
        num_in_multidown = MIN(in_len, element->num_exe_samples);
        total_num_out_spiirup += num_out_spiirup;
    }

    g_assert(total_num_out_spiirup == out_len);

    // Copy from CPU pinned memory to the buffer
    GstMapInfo mapInfo;
    gst_buffer_map(outbuf, &mapInfo, GST_MAP_WRITE);
    memcpy(mapInfo.data, outdata, outsize);
    gst_buffer_unmap(outbuf, &mapInfo);

    /* time */
    if (GST_CLOCK_TIME_IS_VALID(element->t0)) {
        GST_BUFFER_PTS(outbuf) =
          element->t0
          + gst_util_uint64_scale_int_round(element->samples_out, GST_SECOND,
                                            element->rate);
        GST_BUFFER_DURATION(outbuf) =
          element->t0
          + gst_util_uint64_scale_int_round(element->samples_out + out_len,
                                            GST_SECOND, element->rate)
          - GST_BUFFER_PTS(outbuf);
    } else {
        GST_BUFFER_PTS(outbuf)      = GST_CLOCK_TIME_NONE;
        GST_BUFFER_DURATION(outbuf) = GST_CLOCK_TIME_NONE;
    }
    /* offset */
    if (element->offset0 != GST_BUFFER_OFFSET_NONE) {
        GST_BUFFER_OFFSET(outbuf)     = element->offset0 + element->samples_out;
        GST_BUFFER_OFFSET_END(outbuf) = GST_BUFFER_OFFSET(outbuf) + out_len;
    } else {
        GST_BUFFER_OFFSET(outbuf)     = GST_BUFFER_OFFSET_NONE;
        GST_BUFFER_OFFSET_END(outbuf) = GST_BUFFER_OFFSET_NONE;
    }

    if (element->need_discont) {
        GST_BUFFER_FLAG_SET(outbuf, GST_BUFFER_FLAG_DISCONT);
        element->need_discont = FALSE;
    }

    element->samples_in += old_in_len;

    gst_buffer_set_size(outbuf, outsize);

    GST_LOG_OBJECT(
      element,
      "Converted to buffer of %" G_GUINT32_FORMAT " samples (%" G_GSIZE_FORMAT
      " bytes) with timestamp %" GST_TIME_FORMAT ", duration %" GST_TIME_FORMAT
      ", offset %" G_GUINT64_FORMAT ", offset_end %" G_GUINT64_FORMAT,
      out_len, gst_buffer_get_size(outbuf),
      GST_TIME_ARGS(GST_BUFFER_PTS(outbuf)),
      GST_TIME_ARGS(GST_BUFFER_DURATION(outbuf)), GST_BUFFER_OFFSET(outbuf),
      GST_BUFFER_OFFSET_END(outbuf));

    if (outsize == 0) {
        GST_DEBUG_OBJECT(element, "buffer dropped");
        return GST_BASE_TRANSFORM_FLOW_DROPPED;
    }

    /* after the first filtering, update the exe_samples to the rate */
    cuda_multiratespiir_update_exe_samples(&element->num_exe_samples,
                                           element->rate);

    GstFlowReturn ret = push_with_flag_segments(element, outbuf);
    if (ret != GST_FLOW_OK) return ret;
    else
        return GST_BASE_TRANSFORM_FLOW_DROPPED;
}

/*
 * construct a buffer of zeros and push into adapter
 */

static void adapter_push_zeros(CudaMultirateSPIIR *element, unsigned samples) {
    GstBuffer *zerobuf =
      gst_buffer_new_and_alloc((gsize)samples * (element->width / 8));
    if (!zerobuf) {
        GST_DEBUG_OBJECT(element, "failure allocating zero-pad buffer");
    }
    GstMapInfo mapInfo;
    gst_buffer_map(zerobuf, &mapInfo, GST_MAP_WRITE);
    memset(mapInfo.data, 0, mapInfo.size);
    gst_buffer_unmap(zerobuf, &mapInfo);
    gst_adapter_push(element->adapter, zerobuf);
}

static GstFlowReturn cuda_multiratespiir_transform(GstBaseTransform *base,
                                                   GstBuffer *inbuf,
                                                   GstBuffer *outbuf) {
    /*
     * output buffer is generated in cuda_multiratespiir_process function.
     */

    CudaMultirateSPIIR *element = CUDA_MULTIRATESPIIR(base);
    GstFlowReturn res;

    gsize in_size = gst_buffer_get_size(inbuf);

    GST_LOG_OBJECT(
      element,
      "transforming %s+%s buffer of %" G_GSIZE_FORMAT
      " bytes, ts %" GST_TIME_FORMAT ", duration %" GST_TIME_FORMAT
      ", offset %" G_GINT64_FORMAT ", offset_end %" G_GINT64_FORMAT,
      GST_BUFFER_FLAG_IS_SET(inbuf, GST_BUFFER_FLAG_GAP) ? "GAP" : "NONGAP",
      GST_BUFFER_IS_DISCONT(inbuf) ? "DISCONT" : "CONT", in_size,
      GST_TIME_ARGS(GST_BUFFER_PTS(inbuf)),
      GST_TIME_ARGS(GST_BUFFER_DURATION(inbuf)), GST_BUFFER_OFFSET(inbuf),
      GST_BUFFER_OFFSET_END(inbuf));

    /*
     * set device context
     */

    g_mutex_lock(&element->iir_bank_lock);
    if (!element->spstate_initialised) {
        g_cond_wait(&element->iir_bank_available, &element->iir_bank_lock);
    }
    g_mutex_unlock(&element->iir_bank_lock);

    CUDA_CHECK(cudaSetDevice(element->deviceID));
    /* check for timestamp discontinuities;  reset if needed, and set
     * flag to resync timestamp and offset counters and send event
     * downstream */

    if (G_UNLIKELY(GST_BUFFER_IS_DISCONT(inbuf)
                   || GST_BUFFER_OFFSET(inbuf) != element->next_in_offset
                   || !GST_CLOCK_TIME_IS_VALID(element->t0))) {
        GST_DEBUG_OBJECT(element, "reset spstate");
        spiir_state_reset(element->spstate, element->num_depths,
                          element->stream);
        /* FIXME: need to push_drain of data in the adapter ? if upstream never
         * produces discontinous data, no need to push_drain. */
        gst_adapter_clear(element->adapter);

        element->need_discont = TRUE;

        /*
         * (re)sync timestamp and offset book-keeping. Set t0 and offset0 to be
         * the timestamp and offset of the inbuf.
         */

        element->t0              = GST_BUFFER_PTS(inbuf);
        element->offset0         = GST_BUFFER_OFFSET(inbuf);
        element->num_gap_samples = 0;
        element->need_tail_drain = FALSE;
        element->samples_in      = 0;
        element->samples_out     = 0;
        if (element->num_head_cover_samples > 0)
            cuda_multiratespiir_update_exe_samples(
              &element->num_exe_samples, element->num_head_cover_samples);
        else
            cuda_multiratespiir_update_exe_samples(&element->num_exe_samples,
                                                   element->rate);
    }

    element->next_in_offset = GST_BUFFER_OFFSET_END(inbuf);

    /* 0-length buffers are produced to inform downstreams for current timestamp
     */
    if (in_size == 0) {
        /* time */
        if (GST_CLOCK_TIME_IS_VALID(element->t0)) {
            GST_BUFFER_PTS(outbuf) =
              element->t0
              + gst_util_uint64_scale_int_round(element->samples_out,
                                                GST_SECOND, element->rate);
        } else {
            GST_BUFFER_PTS(outbuf) = GST_CLOCK_TIME_NONE;
        }
        /* offset */
        if (element->offset0 != GST_BUFFER_OFFSET_NONE) {
            GST_BUFFER_OFFSET(outbuf) = element->offset0 + element->samples_out;
            GST_BUFFER_OFFSET_END(outbuf) = GST_BUFFER_OFFSET(outbuf);
        } else {
            GST_BUFFER_OFFSET(outbuf)     = GST_BUFFER_OFFSET_NONE;
            GST_BUFFER_OFFSET_END(outbuf) = GST_BUFFER_OFFSET_NONE;
        }

        GST_BUFFER_DURATION(outbuf) = 0;
        gst_buffer_set_size(outbuf, in_size);
        return GST_FLOW_OK;
    }

    guint in_samples             = in_size / (element->width / 8),
          num_exe_samples        = element->num_exe_samples,
          num_tail_cover_samples = element->num_tail_cover_samples, num_zeros,
          adapter_len, num_filt_samples;
    guint64 history_gap_samples, gap_buffer_len;
    gboolean is_gap;

    switch (element->gap_handle) {

    /* FIXME: case 1 may cause some bugs, have not tested it for a long time */
    case 1: // restart after gap
        // NOTE: This codepath is defunct and should be removed.
        fprintf(stderr, "This codepath is defunct and should be removed.\n");
        exit(1);
        /*
         * gap handling cuda_multiratespiir_get_available_samples (element)
         */

        if (GST_BUFFER_FLAG_IS_SET(inbuf, GST_BUFFER_FLAG_GAP)) {
            history_gap_samples = element->num_gap_samples;
            element->num_gap_samples += in_samples;

            /*
             * if receiving GAPs from the beginning, assemble same length GAPs
             */
            if (!element->need_tail_drain) {

                /*
                 * one gap buffer
                 */
                gap_buffer_len = in_samples;
                res            = cuda_multiratespiir_assemble_gap_buffer(
                  element, gap_buffer_len, outbuf);

                if (res != GST_FLOW_OK) return res;
                else
                    return GST_FLOW_OK;
            }

            /*
             * history is already cover the roll-offs,
             * produce the gap buffer
             */
            if (history_gap_samples >= (guint64)num_tail_cover_samples) {
                /*
                 * no process, gap buffer in place
                 */
                gap_buffer_len = in_samples;
                res            = cuda_multiratespiir_assemble_gap_buffer(
                  element, gap_buffer_len, outbuf);

                if (res != GST_FLOW_OK) return res;
            }

            /*
             * if receiving GAPs from some time later :
             * history number of gaps is not enough to cover the
             * total roll-offs of all the resamplers, check if current
             * number of gap samples will cover the roll-offs
             */
            if (history_gap_samples < (guint64)num_tail_cover_samples) {
                /*
                 * if current number of gap samples more than we can
                 * cover the roll-offs offset, process the buffer;
                 * otherwise absorb the inbuf
                 */
                if (element->num_gap_samples
                    >= (guint64)num_tail_cover_samples) {
                    /*
                     * one buffer to cover the roll-offs
                     */
                    num_zeros = num_tail_cover_samples - history_gap_samples;
                    adapter_push_zeros(element, num_zeros);
                    adapter_len =
                      cuda_multiratespiir_get_available_samples(element);
                    res = cuda_multiratespiir_push_drain(element, adapter_len);
                    if (res != GST_FLOW_OK) return res;

                    /*
                     * one gap buffer
                     */
                    gap_buffer_len = in_samples - num_zeros;
                    res            = cuda_multiratespiir_assemble_gap_buffer(
                      element, gap_buffer_len, outbuf);
                    if (res != GST_FLOW_OK) return res;

                } else {
                    /*
                     * if could not cover the roll-offs,
                     * absorb the buffer
                     */
                    num_zeros = in_samples;
                    adapter_push_zeros(element, num_zeros);
                    GST_INFO_OBJECT(element,
                                    "inbuf absorbed %" G_GUINT32_FORMAT
                                    " zero samples",
                                    num_zeros);
                    return GST_BASE_TRANSFORM_FLOW_DROPPED;
                }
            }
        }

        /*
         * inbuf is not gap
         */

        if (!GST_BUFFER_FLAG_IS_SET(inbuf, GST_BUFFER_FLAG_GAP)) {
            /*
             * history is gap, and gap samples has already cover the roll-offs,
             * reset spiir state
             * if history gap is smaller than a tail cover, continue processing.
             */
            if (element->num_gap_samples >= (guint64)num_tail_cover_samples) {
                if (element->need_tail_drain) {
                    adapter_len =
                      cuda_multiratespiir_get_available_samples(element);
                    cuda_multiratespiir_push_gap(
                      element, element->num_tail_cover_samples + adapter_len);
                    gst_adapter_clear(element->adapter);
                }
                spiir_state_reset(element->spstate, element->num_depths,
                                  element->stream);
                cuda_multiratespiir_update_exe_samples(
                  &element->num_exe_samples, element->num_head_cover_samples);
                num_exe_samples = element->num_exe_samples;
            }

            element->num_gap_samples = 0;
            element->need_tail_drain = TRUE;
            adapter_len = cuda_multiratespiir_get_available_samples(element);
            /*
             * here merely speed consideration: if samples ready to be processed
             * are less than num_exe_samples, wait until there are over
             * num_exe_samples
             */
            if ((gint)in_samples < (gint)(num_exe_samples - adapter_len)) {
                /* absorb the buffer */
                gst_buffer_ref(inbuf); /* don't let the adapter free it */
                gst_adapter_push(element->adapter, inbuf);
                GST_INFO_OBJECT(element,
                                "inbuf absorbed %" G_GUINT32_FORMAT " samples",
                                in_samples);
                return GST_BASE_TRANSFORM_FLOW_DROPPED;
            } else {
                /*
                 * filter
                 */
                gst_buffer_ref(inbuf); /* don't let the adapter free it */
                gst_adapter_push(element->adapter, inbuf);
                /*
                 * to speed up, number of samples to be filtered is times of
                 * num_exe_samples
                 */
                adapter_len =
                  cuda_multiratespiir_get_available_samples(element);
                if (element->num_exe_samples == (guint)element->rate)
                    num_filt_samples =
                      gst_util_uint64_scale_int(adapter_len, 1, num_exe_samples)
                      * num_exe_samples;
                else
                    num_filt_samples = num_exe_samples;
                res = cuda_multiratespiir_process(element, num_filt_samples,
                                                  outbuf);

                if (res != GST_FLOW_OK) return res;
            }
        }
        break;

    case 0: // gap is treated as 0;
        is_gap =
          GST_BUFFER_FLAG_IS_SET(inbuf, GST_BUFFER_FLAG_GAP) ? TRUE : FALSE;
        flag_segments_append(element->flag_segments, GST_BUFFER_PTS(inbuf),
                             GST_BUFFER_PTS(inbuf) + GST_BUFFER_DURATION(inbuf),
                             is_gap);

        if (GST_BUFFER_FLAG_IS_SET(inbuf, GST_BUFFER_FLAG_GAP)) {
            adapter_push_zeros(element, in_samples);
        } else {
            gst_buffer_ref(inbuf); /* don't let the adapter free it */
            gst_adapter_push(element->adapter, inbuf);
        }
        /*
         * to speed up, number of samples to be filtered is times of
         * num_exe_samples
         */
        adapter_len = cuda_multiratespiir_get_available_samples(element);
        g_assert(element->num_exe_samples > 0);
        if (adapter_len >= element->num_exe_samples) {
            if (element->num_depths > 1) element->need_tail_drain = TRUE;
            res = cuda_multiratespiir_process(element, element->num_exe_samples,
                                              outbuf);
            if (res != GST_FLOW_OK) return res;
        } else {
            GST_INFO_OBJECT(element,
                            "inbuf absorbed %" G_GUINT32_FORMAT " samples",
                            in_samples);
            return GST_BASE_TRANSFORM_FLOW_DROPPED;
        }
        break;

    default: GST_ERROR_OBJECT(element, "gap handling not supported"); break;
    }

    return GST_FLOW_OK;
}

static gboolean cuda_multiratespiir_sink_event(GstBaseTransform *base,
                                               GstEvent *event) {
    CudaMultirateSPIIR *element = CUDA_MULTIRATESPIIR(base);

    switch (GST_EVENT_TYPE(event)) {
    case GST_EVENT_SEGMENT:

        GST_DEBUG_OBJECT(element, "EVENT NEWSEGMENT");
        /* implicit assumption: spstate has been inited */
        if (element->need_tail_drain && element->num_tail_cover_samples > 0) {
            CUDA_CHECK(cudaSetDevice(element->deviceID));
            GST_DEBUG_OBJECT(element, "NEWSEGMENT, clear tails.");
            if (element->num_gap_samples >= element->num_tail_cover_samples) {
                cuda_multiratespiir_push_gap(element,
                                             element->num_tail_cover_samples);
            } else {
                adapter_push_zeros(element, element->num_tail_cover_samples);
                guint adapter_len =
                  cuda_multiratespiir_get_available_samples(element);
                cuda_multiratespiir_push_drain(element, adapter_len);
            }

            spiir_state_reset(element->spstate, element->num_depths,
                              element->stream);
        }
        element->num_gap_samples = 0;
        element->need_tail_drain = FALSE;
        element->t0              = GST_CLOCK_TIME_NONE;
        element->offset0         = GST_BUFFER_OFFSET_NONE;
        element->next_in_offset  = GST_BUFFER_OFFSET_NONE;
        element->samples_in      = 0;
        element->samples_out     = 0;
        element->need_discont    = TRUE;
        g_mutex_lock(&element->iir_bank_lock);
        if (!element->spstate)
            g_cond_wait(&element->iir_bank_available, &element->iir_bank_lock);
        if (element->num_head_cover_samples > 0)
            cuda_multiratespiir_update_exe_samples(
              &element->num_exe_samples, element->num_head_cover_samples);
        else
            cuda_multiratespiir_update_exe_samples(&element->num_exe_samples,
                                                   element->rate);
        g_mutex_unlock(&element->iir_bank_lock);

        break;

    case GST_EVENT_EOS:

        GST_DEBUG_OBJECT(element, "EVENT EOS");
        if (element->need_tail_drain) {
            CUDA_CHECK(cudaSetDevice(element->deviceID));
            if (element->num_gap_samples >= element->num_tail_cover_samples) {
                GST_DEBUG_OBJECT(element,
                                 "EOS, clear tails by pushing gap, num gap "
                                 "samples %" G_GUINT64_FORMAT,
                                 element->num_gap_samples);
                cuda_multiratespiir_push_gap(element,
                                             element->num_tail_cover_samples);
            } else {

                GST_DEBUG_OBJECT(element, "EOS, clear tails by pushing drain");
                adapter_push_zeros(element, element->num_tail_cover_samples);
                guint adapter_len =
                  cuda_multiratespiir_get_available_samples(element);
                cuda_multiratespiir_push_drain(element, adapter_len);
            }

            // spiir_state_reset (element->spstate, element->num_depths,
            // element->stream);
        }

        break;
    default: break;
    }

    return GST_BASE_TRANSFORM_CLASS(cuda_multiratespiir_parent_class)
      ->sink_event(base, event);
}

static void cuda_multiratespiir_set_property(GObject *object,
                                             guint prop_id,
                                             const GValue *value,
                                             GParamSpec *pspec) {
    CudaMultirateSPIIR *element;

    element = CUDA_MULTIRATESPIIR(object);

    GST_OBJECT_LOCK(element);
    switch (prop_id) {

    case PROP_IIRBANK_FNAME:

        GST_DEBUG("spiir bank acquiring the lock");
        g_mutex_lock(&element->iir_bank_lock);
        GST_DEBUG("spiir bank have acquired the lock");

        GST_LOG_OBJECT(element, "obtaining bank, stream id is %d",
                       element->stream_id);
        element->bank_fname = g_value_dup_string(value);
        /* bank_id is deprecated, get the stream id directly from prop
         * must make sure stream_id has already loaded */
        // cuda_multiratespiir_read_bank_id(element->bank_fname,
        // &element->bank_id);

        int deviceCount;
        cudaGetDeviceCount(&deviceCount);
        element->deviceID = (element->stream_id) % deviceCount;
        GST_LOG("device for spiir %s %d\n", element->bank_fname,
                element->deviceID);
        CUDA_CHECK(cudaSetDevice(element->deviceID));
        // cudaStreamCreateWithFlags(&element->stream, cudaStreamNonBlocking);
        cudaStreamCreate(&element->stream);

        cuda_multiratespiir_read_ndepth_and_rate(
          element->bank_fname, &element->num_depths, &element->rate);

        cuda_multiratespiir_init_cover_samples(
          &element->num_head_cover_samples, &element->num_tail_cover_samples,
          element->rate, element->num_depths, DOWN_FILT_LEN * 2, UP_FILT_LEN);

        /* we consider the num_exe_samples equals to rate unless it is at the
         * first or last buffer */
        cuda_multiratespiir_update_exe_samples(&element->num_exe_samples,
                                               element->rate);

        element->spstate =
          spiir_state_create(element->bank_fname, element->num_depths,
                             element->rate, element->num_head_cover_samples,
                             element->num_exe_samples, element->stream);

        GST_DEBUG_OBJECT(element,
                         "number of cover samples set to (%d, %d), number of "
                         "exe samples set to %d",
                         element->num_head_cover_samples,
                         element->num_tail_cover_samples,
                         element->num_exe_samples);

        if (!element->spstate) {
            GST_ERROR_OBJECT(element, "spsate could not be initialised");
        }

        element->spstate_initialised = TRUE;

        /*
         * signal ready of the bank
         */
        element->outchannels = element->spstate[0]->num_templates * 2;
        element->width       = 32; // FIXME: only can process float data
        GST_DEBUG_OBJECT(
          element, "spiir bank available, number of depths %d, outchannels %d",
          element->num_depths, element->outchannels);

        GST_DEBUG("spiir bank done read, broadcasting the lock");
        g_cond_broadcast(&element->iir_bank_available);
        g_mutex_unlock(&element->iir_bank_lock);
        GST_DEBUG("spiir bank done broadcasting");

        break;

    case PROP_GAP_HANDLE: element->gap_handle = g_value_get_int(value); break;

    case PROP_STREAM_ID: element->stream_id = g_value_get_int(value); break;

    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec); break;
    }
    GST_OBJECT_UNLOCK(element);
}

static void cuda_multiratespiir_get_property(GObject *object,
                                             guint prop_id,
                                             GValue *value,
                                             GParamSpec *pspec) {
    CudaMultirateSPIIR *element;

    element = CUDA_MULTIRATESPIIR(object);
    GST_OBJECT_LOCK(element);

    switch (prop_id) {
    case PROP_IIRBANK_FNAME:
        g_mutex_lock(&element->iir_bank_lock);
        g_value_set_string(value, element->bank_fname);
        g_mutex_unlock(&element->iir_bank_lock);
        break;

    case PROP_GAP_HANDLE: g_value_set_int(value, element->gap_handle); break;

    case PROP_STREAM_ID: g_value_set_int(value, element->stream_id); break;

    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec); break;
    }

    GST_OBJECT_UNLOCK(element);
}
