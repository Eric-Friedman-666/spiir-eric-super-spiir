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

/* This element will synchronize the snr sequencies from all detectors, find
 * peaks from all detectors and for each peak, do null stream analysis.
 */

// Standard and 3rd party includes
#include <assert.h>
#include <chealpix.h>
#include <gst/gst.h>
#include <lal/Date.h>
#include <lal/LIGOMetadataTables.h>
#include <lal/TimeSeries.h>
#include <lal/Units.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

// Our includes
#include <IFOMap.h>
#include <cohfar/background_stats_utils.h>
#include <cuda_debug.h>
#include <flag_segment.h>
#include <ifo_set.h>
#include <pipe_macro.h>
#include <postcoh/postcoh.h>
#include <postcoh/postcoh_kernel.h>
#include <postcoh/postcoh_utils.h>
#include <postcoh/postcohtable_utils.h>

#define DEFAULT_DETRSP_FNAME      "H1L1V1K1_detrsp.xml"
#define EPSILON                   5
#define PEAKFINDER_CLUSTER_WINDOW 5
#define RAD2DEG                   57.2957795
#define POSTCOH_BACKGROUND_LEN    160

#define GST_CAT_DEFAULT cuda_postcoh_debug
GST_DEBUG_CATEGORY_STATIC(GST_CAT_DEFAULT);

G_DEFINE_TYPE_WITH_CODE(CudaPostcoh,
                        cuda_postcoh,
                        GST_TYPE_ELEMENT,
                        GST_DEBUG_CATEGORY_INIT(GST_CAT_DEFAULT,
                                                "cuda_postcoh",
                                                0,
                                                "cuda_postcoh element"))

// FIXME: not support width=64 yet
static GstStaticPadTemplate cuda_postcoh_sink_template =
  GST_STATIC_PAD_TEMPLATE("%s",
                          GST_PAD_SINK,
                          GST_PAD_REQUEST,
                          GST_STATIC_CAPS("audio/x-raw, "
                                          "format = (string) F32LE,"
                                          "rate = (int) [1, MAX], "
                                          "channels = (int) [1, MAX], "
                                          "width = (int) 32"));
/* the following is for a src template that's the same with
 * the sink template
static GstStaticPadTemplate cuda_postcoh_src_template =
GST_STATIC_PAD_TEMPLATE (
                "src",
                GST_PAD_SRC,
                GST_PAD_ALWAYS,
                GST_STATIC_CAPS(
                "audio/x-raw, " \
                "rate = (int) [1, MAX], " \
                "channels = (int) [1, MAX], " \
                "endianness = (int) BYTE_ORDER, " \
                "width = (int) 32"
                ));
*/

enum {
    PROP_0,
    PROP_DETRSP_FNAME,
    PROP_SPIIR_BANK_FNAME,
    PROP_SNGL_TMPLT_FNAME,
    PROP_HIST_TRIALS,
    PROP_TRIAL_INTERVAL,
    PROP_OUTPUT_SKYMAP,
    PROP_COHSNR_THRESH,
    PROP_SNGLSNR_THRESH,
    PROP_STREAM_ID,
    PROP_REFRESH_INTERVAL,
    PROP_SIGNAL_REMOVAL_BG,
    PROP_SIGNAL_REMOVAL_BG_THRESHOLD
};

static void cuda_postcoh_device_set_init(CudaPostcoh *element) {
    if (element->device_id == POSTCOH_PARAMS_NOT_INIT) {
        int deviceCount;
        CUDA_CHECK(cudaGetDeviceCount(&deviceCount));
        /* FIXME: only print device info like runtime version in debug mode */
        // cuda_device_print(deviceCount);
        element->device_id = element->stream_id % deviceCount;
        GST_LOG("device for postcoh %d\n", element->device_id);
        CUDA_CHECK(cudaSetDevice(element->device_id));
        CUDA_CHECK(
          cudaStreamCreateWithFlags(&element->stream, cudaStreamNonBlocking));
    }
}

static void cuda_postcoh_set_property(GObject *object,
                                      guint id,
                                      const GValue *value,
                                      GParamSpec *pspec) {
    CudaPostcoh *element = CUDA_POSTCOH(object);

    GST_OBJECT_LOCK(element);
    switch (id) {
    /* read in detector response map */
    case PROP_DETRSP_FNAME:
        /* must make sure stream_id has already loaded */
        g_assert(element->stream_id != POSTCOH_PARAMS_NOT_INIT);
        g_mutex_lock(&element->prop_lock);
        element->detrsp_fname = g_value_dup_string(value);
        cuda_postcoh_device_set_init(element);
        CUDA_CHECK(cudaSetDevice(element->device_id));
        cuda_postcoh_map_from_xml(element->detrsp_fname, element->state,
                                  element->stream);
        GST_DEBUG("detrsp map has been read in, broad cast the lock avail");
        g_cond_broadcast(&element->prop_avail);
        g_mutex_unlock(&element->prop_lock);
        GST_DEBUG("detrsp map lock broad cast done");
        break;

    /* read in autocorrelation and sigmasq */
    case PROP_SPIIR_BANK_FNAME:
        /* must make sure stream_id has already loaded */
        g_assert(element->stream_id != POSTCOH_PARAMS_NOT_INIT);
        GST_DEBUG("autocorrelation and sigma acquiring the lock");
        g_mutex_lock(&element->prop_lock);
        GST_DEBUG("autocorrelation and sigma have acquired the lock");
        cuda_postcoh_device_set_init(element);
        CUDA_CHECK(cudaSetDevice(element->device_id));
        element->spiir_bank_fname = g_value_dup_string(value);
        cuda_postcoh_autocorr_from_xml(element->spiir_bank_fname,
                                       element->state, element->stream);
        cuda_postcoh_sigmasq_from_xml(element->spiir_bank_fname,
                                      element->state);
        GST_DEBUG("autocorrelation and sigma have been read in, broad cast the "
                  "lock avail");
        g_cond_broadcast(&element->prop_avail);
        g_mutex_unlock(&element->prop_lock);
        GST_DEBUG("autocorrelation and sigma lock broad cast done");
        break;

    /* read in source information masses and spins */
    case PROP_SNGL_TMPLT_FNAME:
        /* must make sure stream_id has already loaded */
        g_assert(element->stream_id != POSTCOH_PARAMS_NOT_INIT);
        GST_DEBUG("sngl table acquiring the lock");
        g_mutex_lock(&element->prop_lock);
        GST_DEBUG("sngl table has acquired the lock");
        element->sngl_tmplt_fname = g_value_dup_string(value);
        cuda_postcoh_sngl_tmplt_from_xml(element->sngl_tmplt_fname,
                                         &(element->sngl_table));
        GST_DEBUG("sngl tables has been read in, broad cast the lock avail");
        g_cond_broadcast(&element->prop_avail);
        g_mutex_unlock(&element->prop_lock);
        GST_DEBUG("sngl tables lock broad cast done");
        break;

    case PROP_HIST_TRIALS:
        g_mutex_lock(&element->prop_lock);
        element->hist_trials = g_value_get_int(value);
        g_cond_broadcast(&element->prop_avail);
        g_mutex_unlock(&element->prop_lock);
        break;

    case PROP_TRIAL_INTERVAL:
        g_mutex_lock(&element->prop_lock);
        element->trial_interval = g_value_get_float(value);
        g_cond_broadcast(&element->prop_avail);
        g_mutex_unlock(&element->prop_lock);
        break;

    case PROP_OUTPUT_SKYMAP:
        element->output_skymap = g_value_get_int(value);
        break;

    case PROP_COHSNR_THRESH:
        element->cohsnr_thresh = g_value_get_float(value);
        break;

    case PROP_SNGLSNR_THRESH:
        element->snglsnr_thresh        = g_value_get_float(value);
        element->state->snglsnr_thresh = element->snglsnr_thresh;
        break;

    case PROP_STREAM_ID: element->stream_id = g_value_get_int(value); break;

    case PROP_REFRESH_INTERVAL:
        element->refresh_interval = g_value_get_int(value);
        break;

    case PROP_SIGNAL_REMOVAL_BG:
        element->enable_signal_removal_bg = g_value_get_boolean(value);
        break;

    case PROP_SIGNAL_REMOVAL_BG_THRESHOLD:
        element->signal_removal_bg_threshold = g_value_get_float(value);
        break;

    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec); break;
    }
    GST_OBJECT_UNLOCK(element);
}

static void cuda_postcoh_get_property(GObject *object,
                                      guint id,
                                      GValue *value,
                                      GParamSpec *pspec) {
    CudaPostcoh *element = CUDA_POSTCOH(object);

    GST_OBJECT_LOCK(element);
    switch (id) {
    case PROP_DETRSP_FNAME:
        g_value_set_string(value, element->detrsp_fname);
        break;

    case PROP_SPIIR_BANK_FNAME:
        g_value_set_string(value, element->spiir_bank_fname);
        break;

    case PROP_SNGL_TMPLT_FNAME:
        g_value_set_string(value, element->sngl_tmplt_fname);
        break;

    case PROP_HIST_TRIALS: g_value_set_int(value, element->hist_trials); break;

    case PROP_TRIAL_INTERVAL:
        g_value_set_float(value, element->trial_interval);
        break;

    case PROP_OUTPUT_SKYMAP:
        g_value_set_int(value, element->output_skymap);
        break;

    case PROP_COHSNR_THRESH:
        g_value_set_float(value, element->cohsnr_thresh);
        break;

    case PROP_SNGLSNR_THRESH:
        g_value_set_float(value, element->snglsnr_thresh);
        break;

    case PROP_STREAM_ID: g_value_set_int(value, element->stream_id); break;

    case PROP_REFRESH_INTERVAL:
        g_value_set_int(value, element->refresh_interval);
        break;

    case PROP_SIGNAL_REMOVAL_BG:
        g_value_set_boolean(value, element->enable_signal_removal_bg);
        break;

    case PROP_SIGNAL_REMOVAL_BG_THRESHOLD:
        g_value_set_float(value, element->signal_removal_bg_threshold);
        break;

    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, id, pspec); break;
    }
    GST_OBJECT_UNLOCK(element);
}

static void set_offset_per_nanosecond(GstPostcohCollectData *data,
                                      double offset_per_nanosecond) {
    data->offset_per_nanosecond = offset_per_nanosecond;
}

static void set_channels(GstPostcohCollectData *data, gint channels) {
    data->channels = channels;
}

static gboolean
  cuda_postcoh_sink_setcaps(CudaPostcoh *postcoh, GstPad *pad, GstCaps *caps);

static gboolean sink_event(GstPad *pad, GstObject *parent, GstEvent *event) {
    CudaPostcoh *postcoh = CUDA_POSTCOH(parent);
    gboolean ret         = TRUE;
    GstCaps *caps;

    GST_LOG_OBJECT(pad, "Received event of type '%s'.",
                   gst_event_type_get_name(GST_EVENT_TYPE(event)));
    switch (GST_EVENT_TYPE(event)) {
    case GST_EVENT_CAPS:
        gst_event_parse_caps(event, &caps);
        ret = cuda_postcoh_sink_setcaps(postcoh, pad, caps);
        // do not use default caps handling.
        gst_event_unref(event);
        event = NULL;
        break;
    case GST_EVENT_TAG:
        // do not process tag.
        gst_object_unref(event);
        event = NULL;
        break;
    default: break;
    }

    if (G_LIKELY(event)) { ret = gst_pad_event_default(pad, parent, event); }
    return ret;
}

/*
 * forwards the event to all sinkpads, takes ownership of the event
 */

typedef struct {
    GstEvent *event;
    gboolean is_flush;
} EventData;

static gboolean forward_src_event_fold(const GValue *gvalue_pad,
                                       GValue *ret,
                                       EventData *data) {
    GstPad *pad = g_value_get_object(gvalue_pad);
    gst_event_ref(data->event);

    gboolean is_event_handled = gst_pad_push_event(pad, data->event);
    if (!is_event_handled) {
        if (data->is_flush) {
            // TRUE here indicates that time should be reset.
            // We do this when seeking to a new time.
            gst_pad_send_event(pad, gst_event_new_flush_stop(TRUE));
        }
        g_value_set_boolean(ret, FALSE);
    }

    gboolean should_fold_continue = TRUE;
    return should_fold_continue;
}

static gboolean
  forward_src_event(CudaPostcoh *postcoh, GstEvent *event, gboolean is_flush) {
    GstIterator *it;
    GValue vret                 = { 0 };
    EventData data              = { event, is_flush };
    gboolean is_event_forwarded = FALSE;
    gboolean is_event_handled   = FALSE;

    g_value_init(&vret, G_TYPE_BOOLEAN);
    g_value_set_boolean(&vret, TRUE);

    // An iterator checking GST_ITERATOR_RESYNC is necessary to work with
    // asynchronous threads.
    it = gst_element_iterate_sink_pads(GST_ELEMENT(postcoh));
    while (!is_event_forwarded) {
        switch (gst_iterator_fold(
          it, (GstIteratorFoldFunction)forward_src_event_fold, &vret, &data)) {
        case GST_ITERATOR_RESYNC:
            GST_LOG_OBJECT(postcoh, "GST_ITERATOR_RESYNC.");
            gst_iterator_resync(it);
            g_value_set_boolean(&vret, TRUE);
            break;

        case GST_ITERATOR_OK:
        case GST_ITERATOR_DONE:
            GST_LOG_OBJECT(postcoh, "GST_ITERATOR_OK or GST_ITERATOR_DONE.");
            is_event_handled   = g_value_get_boolean(&vret);
            is_event_forwarded = TRUE;
            break;

        case GST_ITERATOR_ERROR:
            GST_ERROR_OBJECT(
              postcoh, "Hit GST_ITERATOR_ERROR while forwarding src event.");
            exit(1);
            break;
        }
    }

    gst_iterator_free(it);

    return is_event_handled;
}

/*
 * handle events received on the source pad
 */

static gboolean src_event(GstPad *pad, GstObject *parent, GstEvent *event) {
    CudaPostcoh *postcoh    = CUDA_POSTCOH(parent);
    gboolean should_forward = TRUE;
    gboolean should_handle  = TRUE;
    gboolean is_handled     = FALSE;
    gboolean is_flush       = FALSE;

    GST_LOG_OBJECT(pad, "Received event of type '%s'.",
                   gst_event_type_get_name(GST_EVENT_TYPE(event)));
    switch (GST_EVENT_TYPE(event)) {
    case GST_EVENT_SEEK: {
        gdouble rate;
        GstSeekFlags flags;
        GstSeekType curtype, endtype;
        gint64 cur, end;

        gst_event_parse_seek(event, &rate, NULL, &flags, &curtype, &cur,
                             &endtype, &end);
        is_flush = flags & GST_SEEK_FLAG_FLUSH;

        /* FIXME:  copy the adder's logic re flushing */
        should_handle = FALSE;
        break;
    }

    /* events that can't be handled */
    case GST_EVENT_QOS:
    case GST_EVENT_NAVIGATION:
        GST_WARNING_OBJECT(pad, "Unsupported event type. Discarding event.");
        should_forward = FALSE;
        should_handle  = FALSE;
        break;

    /* forward the rest out all sink pads */
    default: break;
    }

    if (should_forward) {
        is_handled = forward_src_event(postcoh, event, is_flush);
    }

    if (should_handle && (!should_forward || is_handled)) {
        is_handled = gst_pad_event_default(pad, parent, event);
    } else {
        gst_event_unref(event);
        event = NULL;
    }

    return is_handled;
}

/* The first caps we receive on any of the sinkpads will define the caps for all
 * the other sinkpads because we can only mix streams with the same caps.
 */
static gboolean
  cuda_postcoh_sink_setcaps(CudaPostcoh *postcoh, GstPad *pad, GstCaps *caps) {
    size_t freemem;
    size_t totalmem;

    GstPostcohCollectData *caps_pad_data = gst_pad_get_element_private(pad);
    GST_LOG_OBJECT(postcoh, "Received caps from pad %s.",
                   caps_pad_data->ifo_name);

    PostcohState *state = postcoh->state;
    // Ensure postcoh's attributes are only set once, using a mutex and a flag.
    g_mutex_lock(&postcoh->prop_lock);

    // Wait until all required properties are set.
    // g_cond_wait releases the mutex, and reaquires it once the condition is
    // true. The loop is necessary in case of a stolen mutex or spurious wakeup.
    // See https://docs.gtk.org/glib/method.Cond.wait.html
    while (state->npix == POSTCOH_PARAMS_NOT_INIT
           || state->autochisq_len == POSTCOH_PARAMS_NOT_INIT
           || postcoh->hist_trials == POSTCOH_PARAMS_NOT_INIT) {
        GST_LOG_OBJECT(
          postcoh,
          "Waiting for properties to be available before setting caps.");
        g_cond_wait(&postcoh->prop_avail, &postcoh->prop_lock);
    }

    if (state->is_member_init == POSTCOH_PARAMS_NOT_INIT) {
        state->is_member_init = POSTCOH_PARAMS_INIT;

        CUDA_CHECK(cudaSetDevice(postcoh->device_id));

        GstStructure *structure = gst_caps_get_structure(caps, 0);
        gst_structure_get_int(structure, "width", &postcoh->width);
        gst_structure_get_int(structure, "rate", &postcoh->rate);
        gst_structure_get_int(structure, "channels", &postcoh->channels);

        /* postcoh and state initialization */
        postcoh->bps = (guint)(postcoh->width / 8) * postcoh->channels;
        postcoh->offset_per_nanosecond = postcoh->bps / 1e9 * (postcoh->rate);

        GST_DEBUG_OBJECT(postcoh,
                         "setting GstPostcohCollectData width %d, rate %d, "
                         "offset_per_nanosecond %f, bps %u, channels %d",
                         postcoh->width, postcoh->rate,
                         postcoh->offset_per_nanosecond, postcoh->bps,
                         postcoh->channels);

        state->nifo = GST_ELEMENT(postcoh)->numsinkpads;
        state->all_ifos =
          (gchar *)malloc(sizeof(gchar) * state->nifo * IFO_LEN + 1);
        state->peak_list =
          (PeakList **)malloc(sizeof(PeakList *) * state->nifo);
        state->dt = (float)1 / postcoh->rate;

        /* preserved_len is used to allow history data and future data for chisq
         * calculation plus a long enough buffering to account for time shifts
         */
        postcoh->preserved_len = state->autochisq_len + POSTCOH_BACKGROUND_LEN;
        // head_len is the amount of "history" that is maintained on the adapter
        postcoh->head_len = postcoh->preserved_len / 2;
        /* length for the current execution block (i.e. current data) */
        postcoh->exe_len  = postcoh->rate;
        postcoh->exe_size = postcoh->exe_len * postcoh->bps;
        /* one_take_len is the length considering history, current, and
         * future data for chisq calculation */
        postcoh->one_take_len  = postcoh->preserved_len + postcoh->exe_len;
        postcoh->one_take_size = postcoh->one_take_len * postcoh->bps;

        /* snglsnr_cpy_len is the length that only considers current and future
         * data that to be copied to the GPU structure, the GPU structure is a
         * ring buffer and already stored history data */
        postcoh->snglsnr_cpy_len  = postcoh->head_len + postcoh->exe_len;
        postcoh->snglsnr_cpy_size = postcoh->snglsnr_cpy_len * postcoh->bps;

        state->exe_len   = postcoh->rate;
        state->max_npeak = MIN(postcoh->rate, postcoh->channels / 2);
        state->trial_sample_inv =
          round(postcoh->trial_interval * postcoh->rate);
        state->snglsnr_len = postcoh->one_take_len
                             + postcoh->hist_trials * state->trial_sample_inv;
        state->hist_trials = postcoh->hist_trials;
        state->snglsnr_start_load =
          postcoh->hist_trials * state->trial_sample_inv;
        state->snglsnr_start_exe = state->snglsnr_start_load;

        GST_DEBUG_OBJECT(
          postcoh,
          "hist_trials %d, autochisq_len %d, preserved_len %d, sngl_len %d, "
          "bps %u, exe_len %d, one_take_len %d, one_take_size %" G_GSIZE_FORMAT
          ", snglsnr_cpy_len "
          "%d, snglsnr_cpy_size %" G_GSIZE_FORMAT ", start_load %d, "
          "start_exe %d, max_npeak %d",
          state->hist_trials, state->autochisq_len, postcoh->preserved_len,
          state->snglsnr_len, postcoh->bps, postcoh->exe_len,
          postcoh->one_take_len, postcoh->one_take_size,
          postcoh->snglsnr_cpy_len, postcoh->snglsnr_cpy_size,
          state->snglsnr_start_load, state->snglsnr_start_exe,
          state->max_npeak);

        state->ntmplt = postcoh->channels / 2;
        CUDA_CHECK(cudaMemGetInfo(&freemem, &totalmem));
        printf("Free memory: %d MB\nTotal memory: %d MB\n",
               (int)(freemem / 1024 / 1024), (int)(totalmem / 1024 / 1024));
        printf("Allocating %d B for dd_snglsnr\n",
               (int)sizeof(COMPLEX_F *) * state->nifo);

        CUDA_CHECK(cudaMalloc((void **)&(state->dd_snglsnr),
                              sizeof(COMPLEX_F *) * state->nifo));
        state->d_snglsnr =
          (COMPLEX_F **)malloc(sizeof(COMPLEX_F *) * state->nifo);
        /* when dumping the sngl outputs, need to follow the order in this
         * structure, see ker_coh_max_and_chisq */
        state->write_ifo_mapping = (int *)malloc(sizeof(int) * state->nifo);

        GST_OBJECT_LOCK(postcoh->collect);

        /* find the enabled_ifos and enabled_ifo_ids:
         * first find the ifos from the ifo streams
         * then, map ifo_id to its sinkpad's index */
        int i = 0;
        GList *sinkpads;
        for (i = 0, sinkpads = GST_ELEMENT(postcoh)->sinkpads; sinkpads;
             sinkpads = g_list_next(sinkpads), i++) {
            GstPad *pad                 = GST_PAD(sinkpads->data);
            GstPostcohCollectData *data = gst_pad_get_element_private(pad);
            set_offset_per_nanosecond(data, postcoh->offset_per_nanosecond);
            set_channels(data, postcoh->channels);
            // Non-standard IFO indexing (e.g. VH) works because
            // `ifo_set__try_parse` doesn't care about the ordering of IFOs
            strncpy(state->all_ifos + IFO_LEN * i, data->ifo_name,
                    sizeof(char) * IFO_LEN);
        }
        state->all_ifos[IFO_LEN * state->nifo] = '\0';
        // TODO: Consider using ifo_set__try_parse to check for errors
        state->enabled_ifos = ifo_set__parse_or_empty(state->all_ifos);
        // sizeof() only works for arrays that we've statically created, so
        // we use strlen() to get the length of the combination name
        /* overwrite the sinkpad's ifos with the standardized ifo_set string */
        strcpy(state->all_ifos, ifo_set__get_string(state->enabled_ifos));

        /* initialize enabled_ifo_ids, snglsnr matrix, and peak_list */
        for (i = 0, sinkpads = GST_ELEMENT(postcoh)->sinkpads; sinkpads;
             sinkpads = g_list_next(sinkpads), i++) {
            GstPad *pad                 = GST_PAD(sinkpads->data);
            GstPostcohCollectData *data = gst_pad_get_element_private(pad);
            for (int j = 0; j < state->nifo; j++)
                if (strncmp(data->ifo_name, state->all_ifos + IFO_LEN * j,
                            IFO_LEN)
                    == 0) {
                    state->enabled_ifo_ids[i] = j;
                    break;
                }
            int enabled_ifo_id    = state->enabled_ifo_ids[i];
            size_t mem_alloc_size = state->snglsnr_len * postcoh->bps;
            // printf("device id %d, stream addr %p, alloc for snglsnr %d\n",
            // postcoh->device_id, postcoh->stream, mem_alloc_size);

            CUDA_CHECK(cudaMemGetInfo(&freemem, &totalmem));
            printf("Free memory: %d MB  Total memory: %d MB\n",
                   (int)(freemem / 1024 / 1024), (int)(totalmem / 1024 / 1024));
            printf("Allocating SNR series %" G_GUINT64_FORMAT
                   " B, i.e. %" G_GUINT64_FORMAT " MB for ifo %d\n",
                   mem_alloc_size, (mem_alloc_size / 1024 / 1024),
                   enabled_ifo_id);

            CUDA_CHECK(cudaMalloc((void **)&(state->d_snglsnr[enabled_ifo_id]),
                                  mem_alloc_size));
            CUDA_CHECK(cudaMallocHost((void **)&(postcoh->one_take_snr[i]),
                                      postcoh->one_take_size));
            CUDA_CHECK(cudaMemsetAsync(state->d_snglsnr[enabled_ifo_id], 0,
                                       mem_alloc_size, postcoh->stream));
            CUDA_CHECK(cudaMemcpyAsync(
              &(state->dd_snglsnr[enabled_ifo_id]),
              &(state->d_snglsnr[enabled_ifo_id]), sizeof(COMPLEX_F *),
              cudaMemcpyHostToDevice, postcoh->stream));
            CUDA_CHECK(cudaStreamSynchronize(postcoh->stream));
            CUDA_CHECK(cudaPeekAtLastError());

            state->peak_list[enabled_ifo_id] =
              create_peak_list(postcoh->state, postcoh->stream);
        }
        get_write_ifo_mapping(state->all_ifos, state->nifo,
                              state->write_ifo_mapping);

        CUDA_CHECK(cudaMalloc((void **)&state->d_write_ifo_mapping,
                              sizeof(int) * state->nifo));
        CUDA_CHECK(cudaMemsetAsync(state->d_write_ifo_mapping, 0,
                                   sizeof(int) * state->nifo, postcoh->stream));
        CUDA_CHECK(cudaMemcpyAsync(
          state->d_write_ifo_mapping, state->write_ifo_mapping,
          sizeof(int) * state->nifo, cudaMemcpyHostToDevice, postcoh->stream));
        CUDA_CHECK(cudaStreamSynchronize(postcoh->stream));

        // Initialize snr_history_per_template
        for (int ifo_id = 0; ifo_id < MAX_NIFO; ifo_id++) {
            state->snr_history_per_template[ifo_id] = NULL;
        }

        GST_OBJECT_UNLOCK(postcoh->collect);
    } else {
        GstStructure *structure = gst_caps_get_structure(caps, 0);
        int width               = 0;
        int rate                = 0;
        int channels            = 0;
        gst_structure_get_int(structure, "width", &width);
        gst_structure_get_int(structure, "rate", &rate);
        gst_structure_get_int(structure, "channels", &channels);

        if (width != postcoh->width || rate != postcoh->rate
            || channels != postcoh->channels) {
            GST_ERROR_OBJECT(
              postcoh,
              "Incompatible caps. Old caps: width '%d', rate '%d', channels "
              "'%d'. New caps: width '%d', rate '%d', channels '%d'. Aborting.",
              postcoh->width, postcoh->rate, postcoh->channels, width, rate,
              channels);
            exit(1);
        }

        if (state->nifo != GST_ELEMENT(postcoh)->numsinkpads) {
            GST_ERROR_OBJECT(postcoh,
                             "Pad connected after setting caps. state->nifo "
                             "'%d', numsinkpads '%d'. Aborting.",
                             state->nifo, GST_ELEMENT(postcoh)->numsinkpads);
            exit(1);
        }
    }

    // Release the mutex once all properties are set.
    g_mutex_unlock(&postcoh->prop_lock);
    return TRUE;
}

static void destroy_notify(GstPostcohCollectData *data) {
    if (data) {
        free(data->ifo_name);
        if (data->adapter) {
            gst_adapter_clear(data->adapter);
            g_object_unref(data->adapter);
            data->adapter = NULL;
        }
        if (data->flag_segments) {
            g_array_unref(data->flag_segments);
            data->flag_segments = NULL;
        }
    }
}

static GstPad *cuda_postcoh_request_new_pad(GstElement *element,
                                            GstPadTemplate *templ,
                                            const gchar *name,
                                            const GstCaps *caps) {
    CudaPostcoh *postcoh = CUDA_POSTCOH(element);

    GstPad *newpad;
    newpad = gst_pad_new_from_template(templ, name);

    if (!gst_element_add_pad(element, newpad)) {
        gst_object_unref(newpad);
        return NULL;
    }

    GstPostcohCollectData *data;
    // 'true' corresponds to lock, which ensures a buffer is available
    // for 'collect' to take place.
    data = (GstPostcohCollectData *)gst_collect_pads_add_pad(
      postcoh->collect, newpad, sizeof(GstPostcohCollectData),
      (GstCollectDataDestroyNotify)GST_DEBUG_FUNCPTR(destroy_notify), true);
    gst_pad_set_event_function(newpad, (GstPadEventFunction)sink_event);

    if (!data) {
        gst_element_remove_pad(element, newpad);
        gst_object_unref(newpad);
        return NULL;
    }

    data->ifo_name = (gchar *)malloc(IFO_LEN * sizeof(gchar));
    g_strlcpy(data->ifo_name, name, sizeof(data->ifo_name));
    data->adapter         = gst_adapter_new();
    data->flag_segments   = g_array_new(FALSE, FALSE, sizeof(FlagSegment));
    data->is_aligned      = FALSE;
    data->aligned_offset0 = 0;
    data->next_offset     = 0;
    GST_DEBUG_OBJECT(element, "new pad for %s is added and initialised",
                     data->ifo_name);

    return GST_PAD(newpad);
}

static void cuda_postcoh_release_pad(GstElement *element, GstPad *pad) {
    CudaPostcoh *postcoh = CUDA_POSTCOH(element);
    /* FIXME: free adapter and flag_segments */
    gst_collect_pads_remove_pad(postcoh->collect, pad);
    gst_element_remove_pad(element, pad);
}

static GstStateChangeReturn
  cuda_postcoh_change_state(GstElement *element, GstStateChange transition) {
    CudaPostcoh *postcoh = CUDA_POSTCOH(element);

    switch (transition) {
    case GST_STATE_CHANGE_NULL_TO_READY: break;

    case GST_STATE_CHANGE_READY_TO_PAUSED:
        gst_collect_pads_start(postcoh->collect);
        break;

    case GST_STATE_CHANGE_PAUSED_TO_PLAYING: break;

    case GST_STATE_CHANGE_PAUSED_TO_READY:
        /* need to unblock the collectpads before calling the
         * parent change_state so that streaming can finish */
        gst_collect_pads_stop(postcoh->collect);
        break;

    default: break;
    }

    return GST_ELEMENT_CLASS(cuda_postcoh_parent_class)
      ->change_state(element, transition);
}

static gboolean
  cuda_postcoh_get_latest_start_time(GstCollectPads *pads,
                                     GstClockTime *t_latest_start,
                                     guint64 *offset_latest_start) {
    GSList *collectlist;
    GstPostcohCollectData *data;
    GstClockTime t_start_cur = GST_CLOCK_TIME_NONE;
    GstBuffer *buf;

    *t_latest_start = GST_CLOCK_TIME_NONE;

    /* invalid pads */
    g_return_val_if_fail(pads != NULL, FALSE);
    g_return_val_if_fail(GST_IS_COLLECT_PADS(pads), FALSE);

    for (collectlist = pads->data; collectlist;
         collectlist = g_slist_next(collectlist)) {
        data = collectlist->data;
        buf  = gst_collect_pads_peek(pads, (GstCollectData *)data);
        /* eos */
        if (!buf) {
            GST_ERROR_OBJECT(pads, "%s pad:EOS", data->ifo_name);
            gst_buffer_unref(buf);
            return FALSE;
        }
        /* invalid offset */
        if (!GST_BUFFER_OFFSET_IS_VALID(buf)
            || !GST_BUFFER_OFFSET_END_IS_VALID(buf)) {
            GST_ERROR_OBJECT(pads,
                             "%" GST_PTR_FORMAT ": %" GST_PTR_FORMAT
                             " does not have valid offsets",
                             ((GstCollectData *)data)->pad, buf);
            gst_buffer_unref(buf);
            return FALSE;
        }
        /* invalid timestamp */
        if (!GST_BUFFER_PTS_IS_VALID(buf)
            || !GST_BUFFER_DURATION_IS_VALID(buf)) {
            GST_ERROR_OBJECT(pads,
                             "%" GST_PTR_FORMAT ": %" GST_PTR_FORMAT
                             " does not have a valid timestamp and/or duration",
                             ((GstCollectData *)data)->pad, buf);
            gst_buffer_unref(buf);
            return FALSE;
        }

        t_start_cur = GST_BUFFER_PTS(buf);

        if (*t_latest_start == GST_CLOCK_TIME_NONE) {
            *t_latest_start      = t_start_cur;
            *offset_latest_start = GST_BUFFER_OFFSET(buf);
        } else {
            if (*t_latest_start < t_start_cur) {
                *t_latest_start      = t_start_cur;
                *offset_latest_start = GST_BUFFER_OFFSET(buf);
            }
        }
        gst_buffer_unref(buf);
    }
    return TRUE;
}

static void cuda_postcoh_push_zerobuf(GstAdapter *adapter, gsize size) {
    GstBuffer *zerobuf = gst_buffer_new_and_alloc(size);
    if (!zerobuf) {
        GST_DEBUG_OBJECT(adapter, "failure allocating zero-pad buffer");
        exit(1);
    }
    GstMapInfo mapInfo;
    gst_buffer_map(zerobuf, &mapInfo, GST_MAP_WRITE);
    memset(mapInfo.data, 0, mapInfo.size);
    gst_buffer_unmap(zerobuf, &mapInfo);
    gst_adapter_push(adapter, zerobuf);
}

static void cuda_postcoh_pad_with_fake_history(CudaPostcoh *postcoh,
                                               GstCollectPads *pads) {
    /* invalid pads */
    g_assert(pads != NULL);

    /* padding a zero buffer for some fake history data */
    gsize zerobuf_size = postcoh->head_len * postcoh->bps;

    for (GSList *collectlist = pads->data; collectlist;
         collectlist         = g_slist_next(collectlist)) {
        GstPostcohCollectData *data = collectlist->data;
        cuda_postcoh_push_zerobuf(data->adapter, zerobuf_size);
    }
}

static gboolean cuda_postcoh_fillin_discont(CudaPostcoh *postcoh,
                                            GstCollectPads *pads) {
    GSList *collectlist;
    GstPostcohCollectData *data;
    GstBuffer *buf = NULL;

    /* invalid pads */
    g_return_val_if_fail(pads != NULL, FALSE);
    g_return_val_if_fail(GST_IS_COLLECT_PADS(pads), FALSE);

    for (collectlist = pads->data; collectlist;
         collectlist = g_slist_next(collectlist)) {
        data = collectlist->data;
        buf  = gst_collect_pads_peek(pads, (GstCollectData *)data);

        if (buf != NULL) { // != if(buf)
            /* if the buffer in the pad is behind what we expected,
             * we span the gap using zero buffer.
             */
            if (GST_BUFFER_OFFSET(buf) > data->next_offset) {
                GST_DEBUG_OBJECT(data,
                                 "gap :data offset %" G_GUINT64_FORMAT
                                 "current next offset %" G_GUINT64_FORMAT,
                                 GST_BUFFER_OFFSET(buf), data->next_offset);
                gsize zerobuf_size =
                  (GST_BUFFER_OFFSET(buf) - data->next_offset) * postcoh->bps;
                cuda_postcoh_push_zerobuf(data->adapter, zerobuf_size);
            }
            ((GstPostcohCollectData *)data)->next_offset =
              GST_BUFFER_OFFSET_END(buf);
            gst_buffer_unref(buf);
        }
    }
    return TRUE;
}

static gboolean cuda_postcoh_try_push_and_get_common_size(CudaPostcoh *postcoh,
                                                          GstCollectPads *pads,
                                                          gsize *min_size) {
    GSList *collectlist;
    GstPostcohCollectData *data;
    GstBuffer *buf = NULL;

    gint num_ifos_with_data = 0;
    gsize size_cur;
    gboolean min_size_init = FALSE, is_gap;
    GstClockTime buf_end;

    *min_size = 0;

    /* The logic to find common size:
     * if one detector has no data, we obtain the data size in the adapter
     * and find the common size of this detector with other detectors who have
     * data. if there is no data in this adapter, the common size is determined
     * by other detectors.
     */
    for (collectlist = pads->data; collectlist;
         collectlist = g_slist_next(collectlist)) {
        data = collectlist->data;
        buf  = gst_collect_pads_pop(pads, (GstCollectData *)data);
        if (!buf) { // buf == NULL
            size_cur = gst_adapter_available(data->adapter);
            if (!min_size_init) {
                *min_size     = size_cur;
                min_size_init = size_cur > 0 ? TRUE : FALSE;
            } else {
                *min_size = *min_size > size_cur ? size_cur : *min_size;
            }

            continue;
        }

        buf_end = GST_BUFFER_PTS(buf) + GST_BUFFER_DURATION(buf);
        is_gap =
          GST_BUFFER_FLAG_IS_SET(buf, GST_BUFFER_FLAG_GAP) ? TRUE : FALSE;
        flag_segments_append(data->flag_segments, GST_BUFFER_PTS(buf), buf_end,
                             is_gap);
        gst_adapter_push(data->adapter, buf);

        size_cur = gst_adapter_available(data->adapter);
        if (!min_size_init) {
            *min_size     = size_cur;
            min_size_init = TRUE;
        } else {
            *min_size = *min_size > size_cur ? size_cur : *min_size;
        }
        num_ifos_with_data++;
        /* should not unref the buf insce adapter is using it */
    }
    /* If all pads returns NULL buffers, this means all pads at EOS */
    if (num_ifos_with_data == 0) { return FALSE; }

    GST_LOG_OBJECT(postcoh, "get common size %" G_GSIZE_FORMAT, *min_size);
    return TRUE;
}

static gboolean cuda_postcoh_need_recollect(CudaPostcoh *postcoh,
                                            GstCollectPads *pads) {

    GSList *collectlist;
    GstPostcohCollectData *data;
    GstBuffer *buf          = NULL;
    gboolean need_recollect = FALSE, is_gap;

    /* expected end time for the run */
    GstClockTime ts_expect = postcoh->t0
                             + gst_util_uint64_scale_int_round(
                               postcoh->samples_out + postcoh->snglsnr_cpy_len,
                               GST_SECOND, postcoh->rate),
                 buf_end;

    for (collectlist = pads->data; collectlist;
         collectlist = g_slist_next(collectlist)) {
        data = collectlist->data;
        buf  = gst_collect_pads_peek(pads, (GstCollectData *)data);
        if (buf != NULL) { // != if(buf)
            /* zerobuf remove it */
            if (gst_buffer_get_size(buf) == 0) {
                GST_DEBUG_OBJECT(postcoh, "Buffer size is zero.");
                gst_buffer_unref(buf);
                /* discard this buffer in collectpads so it can collect new one
                 */
                buf = gst_collect_pads_pop(pads, (GstCollectData *)data);
                gst_buffer_unref(buf);
                need_recollect = TRUE;
                continue;
            }
            /* accumulate not enough data */
            buf_end = GST_BUFFER_PTS(buf) + GST_BUFFER_DURATION(buf);

            if (buf_end < ts_expect) {
                gst_buffer_unref(buf);
                /* dump this buffer in collectpads adaptor so it can collect new
                 * one */
                buf    = gst_collect_pads_pop(pads, (GstCollectData *)data);
                is_gap = GST_BUFFER_FLAG_IS_SET(buf, GST_BUFFER_FLAG_GAP)
                           ? TRUE
                           : FALSE;
                flag_segments_append(data->flag_segments, GST_BUFFER_PTS(buf),
                                     buf_end, is_gap);
                gst_adapter_push(data->adapter, buf);
                need_recollect = TRUE;
                continue;
            }

            // normal buffer and ready to be postcoh processed
            gst_buffer_unref(buf);
        }
        /* do nothing if it is null buffer */
    }
    return need_recollect;
}

static gboolean cuda_postcoh_align_collected(CudaPostcoh *postcoh,
                                             GstCollectPads *pads) {

    GSList *collectlist;
    GstPostcohCollectData *data;
    GstBuffer *buf, *subbuf;
    GstClockTime t_start_cur, t_end_cur;
    gboolean all_aligned = TRUE, is_gap;
    guint64 offset_cur, offset_end_cur, buf_aligned_offset0;
    GstClockTime t0 = postcoh->t0;

    GST_DEBUG_OBJECT(pads, "begin to align offset0");

    for (collectlist = pads->data; collectlist;
         collectlist = g_slist_next(collectlist)) {
        data = collectlist->data;
        GST_DEBUG_OBJECT(pads, "now at %s is aligned %d", data->ifo_name,
                         data->is_aligned);
        if (data->is_aligned) {
            /* do not collect the buffer in this pad. wait for other pads to be
             * aligned */
            // buf = gst_collect_pads_pop(pads, (GstCollectData *)data);
            // gst_adapter_push(data->adapter, buf);
            continue;
        }
        buf            = gst_collect_pads_pop(pads, (GstCollectData *)data);
        t_start_cur    = GST_BUFFER_PTS(buf);
        t_end_cur      = t_start_cur + GST_BUFFER_DURATION(buf);
        offset_cur     = GST_BUFFER_OFFSET(buf);
        offset_end_cur = GST_BUFFER_OFFSET_END(buf);
        if (t_end_cur > t0) {
            is_gap =
              GST_BUFFER_FLAG_IS_SET(buf, GST_BUFFER_FLAG_GAP) ? TRUE : FALSE;
            flag_segments_append(data->flag_segments, t_start_cur, t_end_cur,
                                 is_gap);

            buf_aligned_offset0 = postcoh->offset0 - offset_cur;
            gsize sub_size      = (offset_end_cur - postcoh->offset0)
                             * data->channels * sizeof(float);
            subbuf = gst_buffer_copy_region(
              buf,
              GST_BUFFER_COPY_FLAGS | GST_BUFFER_COPY_TIMESTAMPS
                | GST_BUFFER_COPY_META | GST_BUFFER_COPY_MEMORY,
              buf_aligned_offset0, sub_size);
            g_assert(subbuf);

            GST_LOG_OBJECT(
              pads,
              "Drop the start of a buffer to align pads at t0. Start time of "
              "buffer '%" GST_TIME_FORMAT "', t0 '%" GST_TIME_FORMAT "'.",
              GST_TIME_ARGS(t_start_cur), GST_TIME_ARGS(postcoh->t0));

            if (buf_aligned_offset0 > 0
                || sub_size != gst_buffer_get_size(buf)) {
                subbuf                      = gst_buffer_make_writable(subbuf);
                GST_BUFFER_DURATION(subbuf) = t_end_cur - postcoh->t0;
                GST_BUFFER_OFFSET_END(subbuf) = offset_end_cur;
                if (buf_aligned_offset0 > 0) {
                    GST_BUFFER_PTS(subbuf)    = postcoh->t0;
                    GST_BUFFER_OFFSET(subbuf) = postcoh->offset0;
                }
            }

            GST_LOG_OBJECT(
              pads,
              "Creating sub buffer (EXPECTED, ACTUAL):\n"
              "size (%" G_GSIZE_FORMAT ", %" G_GSIZE_FORMAT "),\n"
              "timestamp (%" GST_TIME_FORMAT ", %" GST_TIME_FORMAT "),\n"
              "duration (%" GST_TIME_FORMAT ", %" GST_TIME_FORMAT "),\n"
              "offset (%" G_GUINT64_FORMAT ", %" G_GUINT64_FORMAT "),\n"
              "offset_end (%" G_GUINT64_FORMAT ", %" G_GUINT64_FORMAT "),\n",
              sub_size, gst_buffer_get_size(subbuf), GST_TIME_ARGS(postcoh->t0),
              GST_TIME_ARGS(GST_BUFFER_PTS(subbuf)),
              GST_TIME_ARGS(t_end_cur - postcoh->t0),
              GST_TIME_ARGS(GST_BUFFER_DURATION(subbuf)), postcoh->offset0,
              GST_BUFFER_OFFSET(subbuf), offset_end_cur,
              GST_BUFFER_OFFSET_END(subbuf));

            gst_adapter_push(data->adapter, subbuf);

            data->is_aligned = TRUE;
            /* from the first buffer in the adapter, we initiate the next offset
             */
            data->next_offset = GST_BUFFER_OFFSET_END(buf);
            gst_buffer_unref(buf);
        } else {
            all_aligned = FALSE;
            gst_buffer_unref(buf);
        }
    }

    return all_aligned;
}

static float get_new_snr(float snr, float reduced_chisq) {
    const float tunable_q = 6.0; // Tunable constants, see #128
    const float tunable_n = 2.0;

    /* Calculate the re-weighted SNR statistic ('newSNR') from given cohsnr and
    chisq values. See http://arxiv.org/abs/1208.3491 for
    definition. Previous implementation in glue/ligolw/lsctables.py and
    pycbc/events/ranking.py */

    float new_snr = 0.0;
    if (reduced_chisq > 1.0) {
        new_snr =
          snr
          * pow((0.5 * (1.0 + pow(reduced_chisq, (tunable_q / tunable_n)))),
                (-1.0 / tunable_q));
    } else {
        new_snr = snr;
    }
    return new_snr;
}

static int
  remove_backgrounds_with_single_detector_signal(PeakList *pklist,
                                                 int hist_trials,
                                                 int max_npeak,
                                                 int peak_pos,
                                                 float new_snr_thresh) {
    int num_removed_backgrounds = 0;
    for (int itrial = 0; itrial < hist_trials; itrial++) {
        int cur_background = itrial * max_npeak + peak_pos;

        for (int ifo_id = 0; ifo_id < MAX_NIFO; ifo_id++) {
            float new_snr =
              get_new_snr(pklist->snglsnr_bg[ifo_id][cur_background],
                          pklist->chisq_bg[ifo_id][cur_background]);
            if (new_snr > new_snr_thresh) {
                pklist->cohsnr_bg[cur_background] = -1;
                num_removed_backgrounds++;
                break;
            }
        }
    }

    return num_removed_backgrounds;
}

static void remove_backgrounds_with_dominant_pivotal_snr(PeakList *pklist,
                                                         int hist_trials,
                                                         int max_npeak,
                                                         int peak_pos,
                                                         int pivotal_ifo_id) {
    for (int itrial = 0; itrial < hist_trials; itrial++) {
        int cur_background = itrial * max_npeak + peak_pos;

        if (sqrt(pklist->cohsnr_bg[cur_background])
            > 1.414 + pklist->snglsnr[pivotal_ifo_id][peak_pos]) {
            GST_TRACE("mark background, %d itrial, cohsnr %f, snglsnr %f.",
                      itrial, sqrt(pklist->cohsnr_bg[cur_background]),
                      pklist->snglsnr[pivotal_ifo_id][peak_pos]);
        } else {
            GST_TRACE("no mark background, %d itrial, cohsnr %f, snglsnr %f.",
                      itrial, sqrt(pklist->cohsnr_bg[cur_background]),
                      pklist->snglsnr[pivotal_ifo_id][peak_pos]);
            pklist->cohsnr_bg[cur_background] = -1;
        }
    }
}

static int count_backgrounds(PeakList *pklist,
                             int hist_trials,
                             int max_npeak,
                             int peak_pos) {
    int num_backgrounds = 0;
    for (int itrial = 0; itrial < hist_trials; itrial++) {
        int cur_background = itrial * max_npeak + peak_pos;
        if (pklist->cohsnr_bg[cur_background] != -1) { num_backgrounds++; }
    }

    return num_backgrounds;
}

static int cuda_postcoh_select_background(PeakList *pklist,
                                          int pivotal_ifo_id,
                                          int hist_trials,
                                          int max_npeak,
                                          bool should_remove_signals,
                                          float new_snr_threshold) {
    const int npeak             = pklist->npeak[0];
    int num_removed_backgrounds = 0, selected_backgrounds = 0;

    for (int ipeak = 0; ipeak < npeak; ipeak++) {
        int cur_peak_pos = pklist->peak_pos[ipeak];

        if (should_remove_signals) {
            num_removed_backgrounds +=
              remove_backgrounds_with_single_detector_signal(
                pklist, hist_trials, max_npeak, cur_peak_pos,
                new_snr_threshold);
        }

        remove_backgrounds_with_dominant_pivotal_snr(
          pklist, hist_trials, max_npeak, cur_peak_pos, pivotal_ifo_id);
        selected_backgrounds +=
          count_backgrounds(pklist, hist_trials, max_npeak, cur_peak_pos);
    }
    GST_LOG("Total removed backgrounds due to included signal (contamination "
            "candidates) %d.",
            num_removed_backgrounds);
    return selected_backgrounds;
}

static int cuda_postcoh_select_foreground(CudaPostcoh *postcoh,
                                          ifo_set_type coh_ifos,
                                          int *skymap_peakcur) {
    PostcohState *state = postcoh->state;
    int left_entries = 0;
    for (int enabled_ifo_id = 0; enabled_ifo_id < state->nifo;
         enabled_ifo_id++) {
        int ifo_id = state->write_ifo_mapping[enabled_ifo_id];
        if (!ifo_set__renumbered_contains(coh_ifos, state->enabled_ifos,
                                          enabled_ifo_id)) {
            continue;
        }
        int final_peaks             = 0;
        PeakList *pklist               = state->peak_list[enabled_ifo_id];
        int npeak                   = pklist->npeak[0];
        int *peak_pos               = pklist->peak_pos;
        skymap_peakcur[enabled_ifo_id] = peak_pos[0];

        /*
         * select background that satisfy the criteria: cohsnr > triggersnr +
         * coh_thresh
         */
        if (npeak > 0)
            left_entries += cuda_postcoh_select_background(
              pklist, ifo_id, state->hist_trials, state->max_npeak,
              postcoh->enable_signal_removal_bg,
              postcoh->signal_removal_bg_threshold);

        /*
         * mark the rest of peak positions to be -1 to identify invalid
         * background
         */
        int cluster_peak_pos[state->max_npeak];
        for (int ipeak = 0; ipeak < state->max_npeak; ipeak++) {
            cluster_peak_pos[ipeak] = -1;
        }
        memcpy(cluster_peak_pos, peak_pos, sizeof(int) * npeak);

        /*
         * select zerolag that satisfy the criteria: cohsnr > triggersnr +
         * coh_thresh
         */
        int bubbled_peaks = 0;
        int bubbled_peak_pos[state->max_npeak];
        for (int ipeak = 0; ipeak < npeak; ipeak++) {
            /* if the difference of maximum single snr and coherent snr is
             * ignorable, it means that only one detector is in action, we
             * abandon this peak
             * */
            int peak_cur = peak_pos[ipeak];
            // FIXME: consider a different threshold for 3-detector
            if (sqrt(pklist->cohsnr[peak_cur])
                > 1.414 + pklist->snglsnr[ifo_id][peak_cur]) {
                cluster_peak_pos[final_peaks++] = peak_cur;
            } else
                bubbled_peak_pos[bubbled_peaks++] = peak_cur;
        }

        /*
         * bubble out the rest peaks
         */
        for (int ipeak = final_peaks; ipeak < npeak; ipeak++)
            cluster_peak_pos[ipeak] = bubbled_peak_pos[ipeak - final_peaks];

        npeak = final_peaks;
        memcpy(peak_pos, cluster_peak_pos, sizeof(int) * state->max_npeak);
        pklist->npeak[0] = npeak;

        GST_DEBUG("enabled_ifo_id %d, left_entries %d, npeak %d",
                  enabled_ifo_id, left_entries, npeak);
        /* mark the foreground triggers to be added to the postcoh table */
        left_entries += npeak;
    }
    return left_entries;
}

static void cuda_postcoh_record_snr_series(CudaPostcoh *postcoh,
                                           PostcohInspiralTable *output,
                                           PeakList *pklist,
                                           int peak_cur,
                                           int ifo_id) {
    PostcohState *state = postcoh->state;

    /* epoch is the GPS time of the first sample */
    LIGOTimeGPS epoch = output->end_time_sngl[ifo_id];
    g_assert(state->autochisq_len % 2 == 1);
    int snr_series_end_time_offset = (state->autochisq_len - 1) / 2;
    XLALGPSAdd(&epoch, -1.0 / postcoh->rate * snr_series_end_time_offset);

    // Allocate the memory
    // Note ownership is transferred with the buffer
    output->snr_series_list[ifo_id] =
      XLALCreateCOMPLEX8TimeSeries("snr", &epoch, 0., 1. / postcoh->rate,
                                   &lalDimensionlessUnit, state->autochisq_len);

    int snr_series_start = POSTCOH_BACKGROUND_LEN / 2
                           + pklist->len_idx[peak_cur]
                           + pklist->ntoff[ifo_id][peak_cur];

    // the first data sample
    COMPLEX8 *curr_snglsnr =
      (COMPLEX8 *)(state->snr_history_per_template[ifo_id]
                   + snr_series_start * state->ntmplt
                   + pklist->tmplt_idx[peak_cur]);

    // Load snglsnr data into snr_series_list->data->data
    for (unsigned int j = 0; j < output->snr_series_list[ifo_id]->data->length;
         curr_snglsnr += state->ntmplt, j++) {
        output->snr_series_list[ifo_id]->data->data[j] = *curr_snglsnr;
    }
}

static int cuda_postcoh_write_table_to_buf(CudaPostcoh *postcoh,
                                           GstBuffer *outbuf,
                                           ifo_set_type coh_ifos,
                                           int *skymap_peakcur) {
    PostcohState *state = postcoh->state;
    int out_size        = gst_buffer_get_size(outbuf);

    if (out_size == 0) return 0;

    GstMapInfo mapInfo;
    gst_buffer_map(outbuf, &mapInfo, GST_MAP_WRITE);
    memset(mapInfo.data, 0, out_size);
    PostcohInspiralTable *output = (PostcohInspiralTable *)mapInfo.data;

    int nifo = state->nifo;
    int ipeak, npeak = 0, itrial = 0, exe_len = state->exe_len,
               max_npeak = state->max_npeak;
    int hist_trials      = postcoh->hist_trials;

    int tmplt_idx;
    int write_entries = 0;

    GstClockTime ts = GST_BUFFER_PTS(outbuf);
    LIGOTimeGPS end_time;

    int livetime = (int)((ts - postcoh->t0) / GST_SECOND), cur_tmplt_idx;

    SnglInspiralTable *sngl_table = postcoh->sngl_table;
    /* the first entry is reserved to be used to indicate participating IFOs */
    XLALINT8NSToGPS(&end_time, ts);
    output->end_time      = end_time;
    output->is_background = FLAG_EMPTY;
    strcpy(output->ifos, ifo_set__get_string(coh_ifos));
    output++;
    write_entries++;
    /* end of the first entry */

    // TODO: Refactor this loop into smaller chunks.
    //      It could use separate loops or procedures.
    /* FIXME: can output single-detector events, consider cohsnr = single snr,
     * and cmbchisq = single chisq */
    /* only output multi-detector events, cohsnr, cmbchisq only make sense when
     * there are multiple ifos are not in a gap */
    if (ifo_set__count(coh_ifos) < 2) { return write_entries; }

    for (int pivotal_ifo = 0; pivotal_ifo < nifo; pivotal_ifo++) {
        if (!ifo_set__renumbered_contains(coh_ifos, state->enabled_ifos,
                                          pivotal_ifo)) {
            continue;
        }

        PeakList *pklist = state->peak_list[pivotal_ifo];
        npeak            = pklist->npeak[0];

        int peak_cur, len_cur, peak_cur_bg;
        int *peak_pos = pklist->peak_pos;
        for (ipeak = 0; ipeak < npeak; ipeak++) {
            output->next  = NULL;
            peak_cur      = peak_pos[ipeak];
            cur_tmplt_idx = pklist->tmplt_idx[peak_cur];
            XLALINT8NSToGPS(&end_time, ts);
            // NOTE: adjust for the merger/epoch time of the trigger
            GST_DEBUG_OBJECT(
              postcoh, "cur time %" GST_TIME_FORMAT ", sngl end time %d",
              GST_TIME_ARGS(ts), sngl_table[cur_tmplt_idx].end.gpsSeconds);
            XLALGPSAddGPS(&end_time, &(sngl_table[cur_tmplt_idx].end));
            len_cur = pklist->len_idx[peak_cur];
            XLALGPSAdd(&(end_time), (double)len_cur / exe_len);
            output->end_time = end_time;

            /* fill in the attributes for single detectors first */
            for (int enabled_ifo_id = 0; enabled_ifo_id < nifo;
                 end_time           = output->end_time, enabled_ifo_id++) {
                int ifo_id = state->write_ifo_mapping[enabled_ifo_id];

                XLALGPSAdd(&(end_time),
                           (double)pklist->ntoff[ifo_id][peak_cur] / exe_len);
                output->end_time_sngl[ifo_id] = end_time;

                output->snglsnr[ifo_id]  = pklist->snglsnr[ifo_id][peak_cur];
                output->coaphase[ifo_id] = pklist->coaphase[ifo_id][peak_cur];
                output->chisq[ifo_id]    = pklist->chisq[ifo_id][peak_cur];

                output->deff[ifo_id] =
                  sqrt(state->sigmasq[enabled_ifo_id][cur_tmplt_idx])
                  / pklist->snglsnr[ifo_id][peak_cur]; // in MPC
                // Only record snr series on active IFOs
                if (ifo_set__renumbered_contains(coh_ifos, state->enabled_ifos,
                                                 enabled_ifo_id)) {
                    cuda_postcoh_record_snr_series(postcoh, output, pklist,
                                                   peak_cur, ifo_id);
                }
            }
            /* fill in the attributes related to the coherent part */
            output->is_background = FLAG_FOREGROUND;
            output->livetime      = livetime;
            strcpy(output->ifos, ifo_set__get_string(coh_ifos));
            strncpy(output->pivotal_ifo,
                    state->all_ifos + IFO_LEN * pivotal_ifo, IFO_LEN);
            output->pivotal_ifo[IFO_LEN] = '\0';
            output->tmplt_idx            = cur_tmplt_idx;
            output->bankid               = postcoh->stream_id;
            output->pix_idx              = pklist->pix_idx[peak_cur];
            output->cohsnr =
              sqrt(pklist->cohsnr[peak_cur]); /* the returned snr from cuda
                                                 kernel is snr^2 */
            output->nullsnr  = sqrt(pklist->nullsnr[peak_cur]);
            output->cmbchisq =
              pklist->cmbchisq[peak_cur] / ifo_set__count(coh_ifos);
            output->spearman_pval = 0;
            output->fap           = 0;
            output->far           = 0;
            /* covert template index to mass values */
            tmplt_idx                 = output->tmplt_idx;
            output->template_duration = sngl_table[tmplt_idx].template_duration;
            output->mchirp            = sngl_table[tmplt_idx].mchirp;
            output->mtotal            = sngl_table[tmplt_idx].mtotal;
            output->mass1             = sngl_table[tmplt_idx].mass1;
            output->mass2             = sngl_table[tmplt_idx].mass2;
            output->spin1x            = sngl_table[tmplt_idx].spin1x;
            output->spin1y            = sngl_table[tmplt_idx].spin1y;
            output->spin1z            = sngl_table[tmplt_idx].spin1z;
            output->spin2x            = sngl_table[tmplt_idx].spin2x;
            output->spin2y            = sngl_table[tmplt_idx].spin2y;
            output->spin2z            = sngl_table[tmplt_idx].spin2z;
            output->eta               = sngl_table[tmplt_idx].eta;
            output->f_final           = sngl_table[tmplt_idx].f_final;
            /* convert pixel index to ra and dec */
            double theta, phi;
            /* ra = phi, dec = pi/2 - theta */
            pix2ang_nest(postcoh->state->nside, output->pix_idx, &theta, &phi);

            output->ra       = phi * RAD2DEG;
            output->dec      = (M_PI_2 - theta) * RAD2DEG;
            output->event_id = postcoh->cur_event_id++;
            if (postcoh->output_skymap
                && state->snglsnr_max[pivotal_ifo] > postcoh->output_skymap
                && skymap_peakcur[pivotal_ifo] == peak_cur) {
                GString *filename = NULL;
                FILE *file        = NULL;
                // TODO: Consider using ifo_set__try_parse to check for errors
                filename = g_string_new(
                  ifo_set__get_string(ifo_set__parse_or_empty(output->ifos)));
                g_string_append_printf(
                  filename, "_skymap/%s_%d_%d_%d_%d", output->pivotal_ifo,
                  output->end_time.gpsSeconds, output->end_time.gpsNanoSeconds,
                  output->bankid, output->tmplt_idx);
                strcpy(output->skymap_fname, filename->str);
                GST_LOG("file %s is written, skymap addr %p\n",
                        output->skymap_fname, pklist->cohsnr_skymap);
                file = fopen(output->skymap_fname, "w");
                fwrite(pklist->cohsnr_skymap, sizeof(float), state->npix, file);
                fwrite(pklist->nullsnr_skymap, sizeof(float), state->npix,
                       file);
                fclose(file);
                file = NULL;
                g_string_free(filename, TRUE);
            } else
                output->skymap_fname[0] = '\0';
            output->rank = 0;

            GST_LOG_OBJECT(
              postcoh,
              "end_time_sngl_0 %d.%d, ipeak %d, peak_cur %d, len_cur %d, "
              "tmplt_idx %d, pix_idx %d \t,"
              "snglsnr_0 %f, snglsnr_1 %f, snglsnr_2 %f,"
              "coaphase_0 %f, coaphase_1 %f, coa_phase_2 %f,"
              "chisq_0 %f, chisq_1 %f, chisq_2 %f,"
              "cohsnr %f, nullsnr %f, cmbchisq %f\n",
              output->end_time_sngl[0].gpsSeconds,
              output->end_time_sngl[0].gpsNanoSeconds, ipeak, peak_cur, len_cur,
              output->tmplt_idx, output->pix_idx, output->snglsnr[0],
              output->snglsnr[1], output->snglsnr[2], output->coaphase[0],
              output->coaphase[1], output->coaphase[2], output->chisq[0],
              output->chisq[1], output->chisq[2], output->cohsnr,
              output->nullsnr, output->cmbchisq);

            output++;
            write_entries++;
        }

        /* NOTE: here needs to be max_npeak for bg, npeak for zerolag. */
        for (ipeak = 0; ipeak < state->max_npeak; ipeak++) {
            for (itrial = 1; itrial <= hist_trials; itrial++) {
                peak_cur = peak_pos[ipeak];
                len_cur  = pklist->len_idx[peak_cur];
                /* check if cohsnr pass the valid test */
                peak_cur_bg = (itrial - 1) * max_npeak + peak_cur;

                if (peak_cur >= 0 && pklist->cohsnr_bg[peak_cur_bg] > 0) {
                    // output->end_time = end_time[ipeak];
                    output->is_background = FLAG_BACKGROUND;
                    output->livetime      = livetime;
                    strcpy(output->ifos, ifo_set__get_string(coh_ifos));
                    strncpy(output->pivotal_ifo,
                            state->all_ifos + IFO_LEN * pivotal_ifo, IFO_LEN);
                    output->pivotal_ifo[IFO_LEN] = '\0';
                    output->tmplt_idx            = pklist->tmplt_idx[peak_cur];
                    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
                        output->snglsnr[ifo_id] =
                          pklist->snglsnr_bg[ifo_id][peak_cur_bg];
                        output->coaphase[ifo_id] =
                          pklist->coaphase_bg[ifo_id][peak_cur_bg];
                        output->chisq[ifo_id] =
                          pklist->chisq_bg[ifo_id][peak_cur_bg];
                    }

                    // output->pix_idx = pklist->pix_idx[itrial*max_npeak +
                    // peak_cur];
                    output->cohsnr  = sqrt(pklist->cohsnr_bg[peak_cur_bg]);
                    output->nullsnr = sqrt(pklist->nullsnr_bg[peak_cur_bg]);
                    output->cmbchisq = pklist->cmbchisq_bg[peak_cur_bg]
                                       / ifo_set__count(coh_ifos);
                    output->spearman_pval   = 0;
                    output->fap             = 0;
                    output->far             = 0;
                    output->skymap_fname[0] = '\0';
                    GST_LOG_OBJECT(
                      postcoh,
                      "ipeak %d, itrial %d, len_cur %d, tmplt_idx %d, pix_idx "
                      "%d,"
                      "snglsnr[0] %f, snglsnr[1] %f, snglsnr[2] %f,"
                      "coaphase[0] %f, coaphase[1] %f, coa_phase[2] %f,"
                      "chisq[0] %f, chisq[1] %f, chisq[2] %f,"
                      "cohsnr %f, nullsnr %f, cmbchisq %f\n",
                      ipeak, itrial, len_cur, output->tmplt_idx,
                      output->pix_idx, output->snglsnr[0], output->snglsnr[1],
                      output->snglsnr[2], output->coaphase[0],
                      output->coaphase[1], output->coaphase[2],
                      output->chisq[0], output->chisq[1], output->chisq[2],
                      output->cohsnr, output->nullsnr, output->cmbchisq);

                    output++;
                    write_entries++;
                }
            }
        }

        GST_LOG_OBJECT(postcoh,
                       "write to output, ifo %d, npeak %d, %d total entries",
                       pivotal_ifo, npeak, write_entries);
    }
    gst_buffer_unmap(outbuf, &mapInfo);
    return write_entries;
}

static GstFlowReturn cuda_postcoh_new_buffer_and_push(CudaPostcoh *postcoh,
                                                      ifo_set_type coh_ifos,
                                                      gint out_len) {
    GstBuffer *outbuf = NULL;
    GstPad *srcpad    = postcoh->srcpad;
    GstFlowReturn ret;
    int left_entries    = 0;
    int skymap_peakcur[MAX_NIFO];

    /* NOTE: explicitly add one more entry to indicate the participating IFOs */
    if (ifo_set__count(coh_ifos) >= 2) {
        left_entries =
          cuda_postcoh_select_foreground(postcoh, coh_ifos, skymap_peakcur) + 1;
    } else if (ifo_set__count(coh_ifos) == 1) {
        left_entries = 1;
    }

    gsize out_size = sizeof(PostcohInspiralTable) * left_entries;

    // Buffer data is initialized in cuda_postcoh_write_table_to_buf
    outbuf = gst_buffer_new_allocate(NULL, out_size, NULL);
    if (G_UNLIKELY(!outbuf)) {
        GST_ERROR_OBJECT(srcpad, "Could not allocate postcoh-inspiral buffer");
        return GST_FLOW_ERROR;
    }

    /* set the time stamps */
    GstClockTime ts = postcoh->t0
                      + gst_util_uint64_scale_int_round(
                        postcoh->samples_out, GST_SECOND, postcoh->rate);

    GST_BUFFER_PTS(outbuf)      = ts;
    GST_BUFFER_DURATION(outbuf) = (GstClockTime)gst_util_uint64_scale_int_round(
      GST_SECOND, out_len, postcoh->rate);

    /* set the offset */
    GST_BUFFER_OFFSET(outbuf)     = postcoh->offset0 + postcoh->samples_out;
    GST_BUFFER_OFFSET_END(outbuf) = GST_BUFFER_OFFSET(outbuf) + out_len;

    gst_buffer_set_size(outbuf, out_size);

    int write_entries = cuda_postcoh_write_table_to_buf(
      postcoh, outbuf, coh_ifos, skymap_peakcur);

    /* make sure output entries equals estimation */
    g_assert(write_entries == left_entries);

    GST_LOG_OBJECT(srcpad,
                   "Processed of (%d entries) with timestamp %" GST_TIME_FORMAT
                   ", duration %" GST_TIME_FORMAT ", offset %" G_GUINT64_FORMAT
                   ", offset_end %" G_GUINT64_FORMAT,
                   left_entries, GST_TIME_ARGS(GST_BUFFER_PTS(outbuf)),
                   GST_TIME_ARGS(GST_BUFFER_DURATION(outbuf)),
                   GST_BUFFER_OFFSET(outbuf), GST_BUFFER_OFFSET_END(outbuf));

    if (left_entries == 0) GST_BUFFER_FLAG_SET(outbuf, GST_BUFFER_FLAG_GAP);

    ret = gst_pad_push(postcoh->srcpad, outbuf);
    if (ret != GST_FLOW_OK) {
        fprintf(
          stderr,
          "failed to push buffer to next element, cohfar_accumbackground");
        exit(0);
    }
    GST_LOG_OBJECT(postcoh, "pushed buffer, result = %s",
                   gst_flow_get_name(ret));
    return ret;
}

int timestamp_to_gps_idx(long gps_start, int gps_step, GstClockTime t) {
    int seconds_in_one_day = 24 * 3600;
    int gps_len            = seconds_in_one_day / gps_step;

    /* DEPRECATED: not using utc0 as the gps_start time any more */
    // unsigned long days_from_gps_start = (t / GST_SECOND) /
    // seconds_in_one_day; double time_in_one_day = (double) (t/GST_SECOND) -
    // days_from_gps_start * seconds_in_one_day; int gps_idx = (int) (round(
    // time_in_one_day / gps_step)) % gps_len;

    int days_from_gps_start =
      floor((t / GST_SECOND - (double)gps_start) / seconds_in_one_day);
    double time_in_one_day =
      (double)(t / GST_SECOND)
      - ((double)gps_start + days_from_gps_start * seconds_in_one_day);
    int gps_idx = (int)(round(time_in_one_day / gps_step)) % gps_len;

    GST_DEBUG("current days from gps_start %d, current time in one day %f, "
              "length of gps array %d, gps_idx %d,\n",
              days_from_gps_start, time_in_one_day, gps_len, gps_idx);
    return gps_idx;
}

static int peaks_over_thresh(COMPLEX_F *snglsnr,
                             PostcohState *state,
                             int enabled_ifo_id,
                             cudaStream_t stream) {
    int exe_len = state->exe_len, ntmplt = state->ntmplt, itmplt, ilen, jlen,
        npeak = 0, max_npeak = state->max_npeak;
    COMPLEX_F *isnr = snglsnr;
    float tmp_abssnr, snglsnr_thresh = state->snglsnr_thresh;
    PeakList *pklist  = state->peak_list[enabled_ifo_id];
    float *tmp_maxsnr = (float *)malloc(sizeof(float) * state->exe_len);
    int *tmp_tmpltidx = (int *)malloc(sizeof(int) * state->exe_len);
    int *peak_pos     = pklist->peak_pos;
    int *tmplt_idx    = pklist->tmplt_idx;
    int *len_idx      = pklist->len_idx;
    /* find maxsnr for each sampling point, keep the record of the tmplt_idx */
    for (ilen = 0; ilen < exe_len; ilen++) {
        tmp_maxsnr[ilen]               = 0.0;
        tmp_tmpltidx[ilen]             = -1;
        peak_pos[MIN(ilen, max_npeak)] = -1;
        for (itmplt = 0; itmplt < ntmplt; itmplt++) {
            tmp_abssnr =
              sqrt((*isnr).re * (*isnr).re + (*isnr).im * (*isnr).im);
            if (tmp_abssnr > tmp_maxsnr[ilen]) {
                tmp_maxsnr[ilen]   = tmp_abssnr;
                tmp_tmpltidx[ilen] = itmplt;
            }
            isnr++;
        }
    }
    /* find the maxsnr acrros each tmplt */
    for (ilen = 0; ilen < exe_len; ilen++) {
        if (tmp_tmpltidx[ilen] > -1) {
            /* find if the subsequential snr has larger snr,
             * yes: continue to next sample point,
             * no: save this snr and delete snr on other times of the same
             * tmplt*/
            for (jlen = ilen + 1; jlen < exe_len; jlen++) {
                if (tmp_tmpltidx[jlen] == tmp_tmpltidx[ilen]
                    && tmp_maxsnr[jlen] > tmp_maxsnr[ilen])
                    break;
                if (tmp_tmpltidx[jlen] == tmp_tmpltidx[ilen]
                    && tmp_maxsnr[jlen] < tmp_maxsnr[ilen])
                    tmp_tmpltidx[jlen] = -1;
            }

            if (jlen == exe_len && tmp_maxsnr[ilen] > snglsnr_thresh) {
                len_idx[npeak]   = ilen;
                tmplt_idx[npeak] = tmp_tmpltidx[ilen];
                peak_pos[npeak]  = npeak;
                npeak++;
            }
        }
    }

    /* keep track of the maximum single snr in this snr chunk */
    for (ilen = 0; ilen < exe_len; ilen++) {
        if (tmp_maxsnr[ilen] > state->snglsnr_max[enabled_ifo_id]) {
            state->snglsnr_max[enabled_ifo_id] = tmp_maxsnr[ilen];
        }
    }

    /* do clustering every PEAKFINDER_CLUSTER_WINDOW samples, FIXME: if set to
     * 0, the size of output will be ten times */
    int cluster_peak_pos[max_npeak], len_cluster_peak, len_next_peak,
      final_peaks       = 0, ipeak;
    cluster_peak_pos[0] = peak_pos[0];
    for (ipeak = 0; ipeak < npeak - 1; ipeak++) {
        if (peak_pos[ipeak + 1] - cluster_peak_pos[final_peaks]
            > PEAKFINDER_CLUSTER_WINDOW) {
            final_peaks++;
            cluster_peak_pos[final_peaks] = peak_pos[ipeak + 1];
        } else { // update the cluster_peak_pos if next peak pos has larger SNR
            len_cluster_peak = len_idx[cluster_peak_pos[final_peaks]];
            len_next_peak    = len_idx[peak_pos[ipeak + 1]];
            if (tmp_maxsnr[len_cluster_peak] < tmp_maxsnr[len_next_peak])
                cluster_peak_pos[final_peaks] = peak_pos[ipeak + 1];
        }
    }

    npeak = npeak == 0 ? 0 : final_peaks + 1;
    memcpy(peak_pos, cluster_peak_pos, sizeof(int) * npeak);
    pklist->npeak[0] = npeak;

    CUDA_CHECK(cudaMemcpyAsync(pklist->d_npeak, pklist->npeak,
                               sizeof(int) * (pklist->peak_intlen),
                               cudaMemcpyHostToDevice, stream));

    free(tmp_maxsnr);
    free(tmp_tmpltidx);

    return npeak;
}

static void cuda_postcoh_process(CudaPostcoh *postcoh,
                                 GstCollectPads *pads,
                                 gsize common_size) {
    GSList *collectlist;
    GstPostcohCollectData *data;

    PostcohState *state = postcoh->state;
    gsize one_take_size = postcoh->one_take_size, exe_size = postcoh->exe_size,
          snglsnr_cpy_size = postcoh->snglsnr_cpy_size;
    gint snglsnr_cpy_len   = postcoh->snglsnr_cpy_len;

    int c_npeak     = 0;
    GstClockTime ts = postcoh->t0
                      + gst_util_uint64_scale_int_round(
                        postcoh->samples_out, GST_SECOND, postcoh->rate);

    /* Refresh the detector response U and Dt matrices if reached the refresh
     * interval */
    if (postcoh->refresh_interval > 0
        && (ts - postcoh->t_roll_start) / GST_SECOND
             > (unsigned)postcoh->refresh_interval) {
        postcoh->t_roll_start = ts;
        /* re-read matrices and send them to GPU */
        CUDA_CHECK(cudaSetDevice(postcoh->device_id));
        cuda_postcoh_map_from_xml(postcoh->detrsp_fname, postcoh->state,
                                  postcoh->stream);
        GST_DEBUG("detrsp map has been updated");
    }

    LIGOTimeGPS ligo_time;
    XLALINT8NSToGPS(&ligo_time, ts);
    GstClockTime ts_exe_end;
    while (common_size >= one_take_size) {
        GST_DEBUG_OBJECT(
          postcoh,
          "cur time %" GST_TIME_FORMAT ", gps %d, common_size %" G_GSIZE_FORMAT
          ", one_take_size %" G_GSIZE_FORMAT ", exe_size %" G_GSIZE_FORMAT "\n",
          GST_TIME_ARGS(ts), ligo_time.gpsSeconds, common_size, one_take_size,
          exe_size);
        /* expected end time for the run */
        ts_exe_end =
          postcoh->t0
          + gst_util_uint64_scale_int_round(
            postcoh->samples_out + postcoh->exe_len, GST_SECOND, postcoh->rate);

        int gps_idx = timestamp_to_gps_idx(state->gps_start, state->gps_step,
                                           postcoh->next_exe_t);
        /* copy the snr data to the right location for all detectors */
        int i;
        // Note this set is based on 'enabled_ifo_id'
        ifo_set_type coh_ifos = 0;
        for (i = 0, collectlist = pads->data; collectlist;
             collectlist = g_slist_next(collectlist), i++) {
            data    = collectlist->data;
            int enabled_ifo_id = state->enabled_ifo_ids[i];
            int ifo_id         = state->write_ifo_mapping[enabled_ifo_id];

            if (!flag_segments_is_gap(data->flag_segments, postcoh->next_exe_t,
                                      ts_exe_end)) {
                ifo_set__set(&coh_ifos, ifo_id);
                GST_LOG_OBJECT(postcoh,
                               "Added IFO '%s' to coh_ifos as it is not in a "
                               "gap. coh_ifos: '%d'",
                               data->ifo_name, coh_ifos);
            }

            state->snglsnr_max[enabled_ifo_id] = 0;
            PeakList *pklist = state->peak_list[enabled_ifo_id];

            gst_adapter_copy(data->adapter, postcoh->one_take_snr[i], 0,
                             one_take_size);

            /* pointer to the current and future data
             * this data will be copied to GPU as GPU structure already stored
             * history data */
            COMPLEX_F *snglsnr =
              postcoh->one_take_snr[i] + postcoh->head_len * state->ntmplt;

            c_npeak = peaks_over_thresh(snglsnr, state, enabled_ifo_id,
                                        postcoh->stream);

            GST_DEBUG_OBJECT(postcoh,
                             "gps %d, ifo %d, c_npeak %d, max_snglsnr %f\n",
                             ligo_time.gpsSeconds, enabled_ifo_id, c_npeak,
                             state->snglsnr_max[enabled_ifo_id]);

            // this is necessory for new postcoh kernel
            // 1. expand temporal memory space if necessary
            if (snglsnr_cpy_len > pklist->len_snglsnr_buffer) {
                // re-malloc pklist->d_snglsnr_buffer
                if (pklist->d_snglsnr_buffer != NULL) {
                    cudaFree(pklist->d_snglsnr_buffer);
                }
                cudaMalloc((void **)&pklist->d_snglsnr_buffer,
                           snglsnr_cpy_size);
                pklist->len_snglsnr_buffer = snglsnr_cpy_len;
            }
            // 2. copy snglsnr to temporal gpu memory d_snglsnr_buffer
            CUDA_CHECK(cudaMemcpyAsync(pklist->d_snglsnr_buffer, snglsnr,
                                       snglsnr_cpy_size, cudaMemcpyHostToDevice,
                                       postcoh->stream));
            // 3. do transpose, at the same time, snr data will be moved to
            // proper positions in state->d_snglsnr[enabled_ifo_id]
            transpose_snglsnr((COMPLEX_F *)pklist->d_snglsnr_buffer,
                              state->d_snglsnr[enabled_ifo_id],
                              state->snglsnr_start_load, snglsnr_cpy_len,
                              state->snglsnr_len, state->ntmplt,
                              postcoh->stream);

            CUDA_CHECK(cudaStreamSynchronize(postcoh->stream));
            state->snr_history_per_template[ifo_id] = postcoh->one_take_snr[i];
        }

        for (i = 0, collectlist = pads->data; collectlist;
             collectlist = g_slist_next(collectlist), i++) {
            data    = collectlist->data;
            int enabled_ifo_id = state->enabled_ifo_ids[i];

            if (ifo_set__count(coh_ifos) >= 2
                && ifo_set__renumbered_contains(coh_ifos, state->enabled_ifos,
                                                enabled_ifo_id)) {
                if (state->peak_list[enabled_ifo_id]->npeak[0] > 0) {
                    cohsnr_and_chisq(
                      state, ifo_set__renumber(coh_ifos, state->enabled_ifos),
                      enabled_ifo_id, gps_idx,
                      postcoh->output_skymap
                        && state->snglsnr_max[enabled_ifo_id]
                             > postcoh->output_skymap,
                      postcoh->stream);
                    GST_LOG_OBJECT(
                      postcoh, "After coherent analysis for ifo %d, npeak %d.",
                      enabled_ifo_id,
                      state->peak_list[enabled_ifo_id]->npeak[0]);
                }
            }
        }

        common_size -= exe_size;
        int exe_len = state->exe_len;
        state->snglsnr_start_load =
          (state->snglsnr_start_load + exe_len) % state->snglsnr_len;
        state->snglsnr_start_exe =
          (state->snglsnr_start_exe + exe_len) % state->snglsnr_len;
        postcoh->next_exe_t += exe_len / postcoh->rate * GST_SECOND;

        /* make a buffer and send it out */
        cuda_postcoh_new_buffer_and_push(postcoh, coh_ifos, exe_len);

        for (i = 0, collectlist = pads->data; collectlist;
             collectlist = g_slist_next(collectlist), i++) {
            int enabled_ifo_id = state->enabled_ifo_ids[i];
            data = collectlist->data;
            /* move along */
            GST_DEBUG_OBJECT(postcoh,
                             "flush adapter %d, size %" G_GSIZE_FORMAT "\n",
                             enabled_ifo_id, exe_size);

            gst_adapter_flush(data->adapter, exe_size);
        }

        /* move along */
        postcoh->samples_out += exe_len;
    }
}

static GstFlowReturn collected(GstCollectPads *pads, gpointer user_data) {
    CudaPostcoh *postcoh = CUDA_POSTCOH(user_data);
    PostcohState *state  = postcoh->state;
    g_mutex_lock(&postcoh->prop_lock);
    while (state->npix == POSTCOH_PARAMS_NOT_INIT
           || state->autochisq_len == POSTCOH_PARAMS_NOT_INIT
           || postcoh->hist_trials == POSTCOH_PARAMS_NOT_INIT) {
        g_cond_wait(&postcoh->prop_avail, &postcoh->prop_lock);
        GST_LOG_OBJECT(postcoh, "collected have to wait for detrsp_map, "
                                "autocorrelation, and hist_trials to be read");
    }
    g_mutex_unlock(&postcoh->prop_lock);

    CUDA_CHECK(cudaSetDevice(postcoh->device_id));
    GstElement *element = GST_ELEMENT(postcoh);
    GstClockTime t_latest_start;
    GstFlowReturn res;
    guint64 offset_latest_start = 0;
    gsize common_size           = 0;
    gboolean has_common_size    = FALSE;

    GST_DEBUG_OBJECT(postcoh, "collected");
    /* Assure that we have enough sink pads. */
    if (element->numsinkpads < 2) {
        GST_ERROR_OBJECT(
          postcoh, "not enough sink pads, 2 required but only %d are present",
          element->numsinkpads < 2);
        return GST_FLOW_ERROR;
    }

    if (!postcoh->set_starttime) {
        /* get the latest timestamp */
        if (!cuda_postcoh_get_latest_start_time(pads, &t_latest_start,
                                                &offset_latest_start)) {
            /* bad buffer : one of the buffers is at EOS or invalid timestamp/
             * offset */
            GST_ERROR_OBJECT(
              postcoh, "cannot deduce start timestamp/ offset information");
            return GST_FLOW_ERROR;
        }
        postcoh->t0           = t_latest_start;
        postcoh->t_roll_start = t_latest_start;
        postcoh->next_exe_t   = postcoh->t0;
        postcoh->offset0      = offset_latest_start;
        GST_DEBUG_OBJECT(postcoh,
                         "set the aligned time t0 to %" GST_TIME_FORMAT
                         ", start offset0 to %" G_GUINT64_FORMAT,
                         GST_TIME_ARGS(postcoh->t0), postcoh->offset0);
        postcoh->set_starttime = TRUE;

        cuda_postcoh_pad_with_fake_history(postcoh, pads);
    }

    if (postcoh->is_all_aligned) {
        /* first fill in any discontinuity */
        cuda_postcoh_fillin_discont(postcoh, pads);

        /* if buf in any of pads is 0 size, discard this buf.
         * push the buf in adapter if it is too small
         * this means this element starts to work only when
         * there are non-zero buffers in all pads */
        if (cuda_postcoh_need_recollect(postcoh, pads)) { return GST_FLOW_OK; }

        has_common_size = cuda_postcoh_try_push_and_get_common_size(
          postcoh, pads, &common_size);
        GST_DEBUG_OBJECT(postcoh,
                         "get spanned size %" G_GSIZE_FORMAT
                         ", get spanned samples %f",
                         common_size, (float)common_size / postcoh->bps);

        if (!has_common_size) {
            /* no pad has buffer, send EOS downstream */
            GST_INFO_OBJECT(postcoh,
                            "All IFOs have null buffers, sending EOS.");
            res = gst_pad_push_event(postcoh->srcpad, gst_event_new_eos());
            return res;
        }

        cuda_postcoh_process(postcoh, pads, common_size);

    } else {
        postcoh->is_all_aligned = cuda_postcoh_align_collected(postcoh, pads);
    }
    return GST_FLOW_OK;
}

static void cuda_postcoh_dispose(GObject *object) {
    CudaPostcoh *postcoh = CUDA_POSTCOH(object);

    int i = 0;
    GList *sinkpads;
    for (i = 0, sinkpads = GST_ELEMENT(postcoh)->sinkpads; sinkpads;
         sinkpads = g_list_next(sinkpads), i++) {
        CUDA_CHECK(cudaFreeHost(postcoh->one_take_snr[i]));
    }

    if (postcoh->collect) gst_object_unref(GST_OBJECT(postcoh->collect));
    postcoh->collect = NULL;

    if (postcoh->state) {
        state_destroy(postcoh->state);
        free(postcoh->state);
        postcoh->state = NULL;
    }

    if (postcoh->srcpad) gst_object_unref(postcoh->srcpad);
    postcoh->srcpad = NULL;

    g_mutex_clear(&postcoh->prop_lock);
    g_cond_clear(&postcoh->prop_avail);

    /* destroy hashtable and its contents */
    G_OBJECT_CLASS(cuda_postcoh_parent_class)->dispose(object);
}

static void cuda_postcoh_class_init(CudaPostcohClass *klass) {
    GObjectClass *gobject_class = G_OBJECT_CLASS(klass);
    gobject_class->get_property = GST_DEBUG_FUNCPTR(cuda_postcoh_get_property);
    gobject_class->set_property = GST_DEBUG_FUNCPTR(cuda_postcoh_set_property);
    gobject_class->dispose      = GST_DEBUG_FUNCPTR(cuda_postcoh_dispose);

    g_object_class_install_property(
      gobject_class, PROP_DETRSP_FNAME,
      g_param_spec_string("detrsp-fname", "Detector response filename",
                          "Should include U map and time_diff map",
                          DEFAULT_DETRSP_FNAME,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_SPIIR_BANK_FNAME,
      g_param_spec_string("autocorrelation-fname",
                          "Autocorrelation matrix filename",
                          "Autocorrelation matrix", NULL,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_SNGL_TMPLT_FNAME,
      g_param_spec_string("sngl-tmplt-fname", "File that has SnglInspiralTable",
                          "single template filename", NULL,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_HIST_TRIALS,
      g_param_spec_int("hist-trials", "history trials",
                       "history that should be kept in times", 0, G_MAXINT, 1,
                       G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_TRIAL_INTERVAL,
      g_param_spec_float("trial-interval", "trial interval in seconds",
                         "trial interval in seconds", 0, G_MAXFLOAT, 0.1,
                         G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_OUTPUT_SKYMAP,
      g_param_spec_int("output-skymap", "if output skymap", "if output skymap",
                       0, G_MAXINT, 0,
                       G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_COHSNR_THRESH,
      g_param_spec_float("cohsnr-thresh", "coherent snr threshold",
                         "coherent snr threshold", 0.0, G_MAXFLOAT, 1.05,
                         G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_SNGLSNR_THRESH,
      g_param_spec_float("snglsnr-thresh", "single snr threshold",
                         "single snr threshold", 0.0, G_MAXFLOAT, 4.0,
                         G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_STREAM_ID,
      g_param_spec_int("stream-id", "id for cuda stream", "id for cuda stream",
                       0, G_MAXINT, 0,
                       G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_REFRESH_INTERVAL,
      g_param_spec_int(
        "detrsp-refresh-interval", "detector response refresh interval",
        "(0) never refresh stats; (N) refresh stats every N seconds. ", 0,
        G_MAXINT, 600, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));
    g_object_class_install_property(
      gobject_class, PROP_SIGNAL_REMOVAL_BG,
      g_param_spec_boolean("feature-signal-removal-bg",
                           "Signal removal from backgrounds",
                           "Enable signal removal from backgrounds.", FALSE,
                           G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_SIGNAL_REMOVAL_BG_THRESHOLD,
      g_param_spec_float(
        "feature-signal-removal-bg-threshold",
        "Signal removal from backgrounds threshold",
        "Newsnr threshold to remove single IFO signals from backgrounds.", 0.0,
        G_MAXFLOAT, 8.5, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    GstElementClass *gst_element_class = GST_ELEMENT_CLASS(klass);

    gst_element_class->request_new_pad =
      GST_DEBUG_FUNCPTR(cuda_postcoh_request_new_pad);
    gst_element_class->release_pad =
      GST_DEBUG_FUNCPTR(cuda_postcoh_release_pad);
    gst_element_class->change_state =
      GST_DEBUG_FUNCPTR(cuda_postcoh_change_state);

    gst_element_class_set_metadata(
      gst_element_class, "Post Coherent SNR and Nullstream Generator", "Filter",
      "Coherent trigger generation.\n", "Qi Chu <qi.chu at ligo dot org>");

    gst_element_class_add_pad_template(
      gst_element_class,
      gst_static_pad_template_get(&cuda_postcoh_sink_template));

    GstCaps *template_caps = gst_caps_from_string("application/x-lal-postcoh");

    gst_element_class_add_pad_template(
      gst_element_class,
      //		gst_static_pad_template_get(&cuda_postcoh_src_template)
      gst_pad_template_new("src", GST_PAD_SRC, GST_PAD_ALWAYS, template_caps));

    gst_caps_unref(template_caps);
}

static void cuda_postcoh_init(CudaPostcoh *postcoh) {
    GstElement *element = GST_ELEMENT(postcoh);

    gst_element_create_all_pads(element);
    postcoh->srcpad = gst_element_get_static_pad(element, "src");
    GstCaps *caps   = gst_pad_query_caps(postcoh->srcpad, NULL);
    GST_DEBUG_OBJECT(postcoh, "%s caps %" GST_PTR_FORMAT,
                     GST_PAD_NAME(postcoh->srcpad), caps);
    gst_caps_unref(caps);

    gst_pad_set_event_function(
      postcoh->srcpad, (GstPadEventFunction)GST_DEBUG_FUNCPTR(src_event));
    postcoh->collect = gst_collect_pads_new();
    gst_collect_pads_set_function(postcoh->collect,
                                  GST_DEBUG_FUNCPTR(collected), postcoh);

    postcoh->t0         = GST_CLOCK_TIME_NONE;
    postcoh->next_exe_t = GST_CLOCK_TIME_NONE;
    postcoh->offset0    = GST_BUFFER_OFFSET_NONE;
    // postcoh->next_in_offset = GST_BUFFER_OFFSET_NONE;
    postcoh->set_starttime  = FALSE;
    postcoh->is_all_aligned = FALSE;
    postcoh->samples_in     = 0;
    postcoh->samples_out    = 0;
    postcoh->state          = (PostcohState *)malloc(sizeof(PostcohState));
    postcoh->state->autochisq_len  = POSTCOH_PARAMS_NOT_INIT;
    postcoh->state->npix           = POSTCOH_PARAMS_NOT_INIT;
    postcoh->state->is_member_init = POSTCOH_PARAMS_NOT_INIT;
    postcoh->hist_trials           = POSTCOH_PARAMS_NOT_INIT;
    g_mutex_init(&postcoh->prop_lock);
    g_cond_init(&postcoh->prop_avail);
    postcoh->stream_id        = POSTCOH_PARAMS_NOT_INIT;
    postcoh->device_id        = POSTCOH_PARAMS_NOT_INIT;
    postcoh->process_id       = 0;
    postcoh->cur_event_id     = 0;
    postcoh->t_roll_start     = GST_CLOCK_TIME_NONE;
    postcoh->refresh_interval = 0;
}
