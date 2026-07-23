/*
 * Copyright (C) 2015	Qi Chu	<qi.chu@uwa.edu.au>
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc.,
 * 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
 */

/*
 * ============================================================================
 *
 *                                  Preamble
 *
 * ============================================================================
 */
#include <float.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
/*
 *  stuff from gobject/gstreamer
 */

#include <glib.h>
#include <gst/base/gstbasetransform.h>
#include <gst/gst.h>
#include <gstlal/gstlal.h>

/*
 * stuff from here
 */

#include <cohfar/background_stats_utils.h>
#include <cohfar/cohfar_assignfar.h>
#include <ifo_set.h>
#include <postcohtable.h>
#include <time.h>
#define DEFAULT_STATS_NAME "stats.xml.gz"
/* required minimal background events */
#define MIN_BACKGROUND_NEVENT 1000000
/* make sure the far value to be 0 to indicate no background event yet, or >
 * FLT_MIN */
#define BOUND(a, b) (((b) > 0) ? ((b) < (a) ? (a) : (b)) : 0)
/*
 * ============================================================================
 *
 *                                 Utilities
 *
 * ============================================================================
 */

/*
 * ============================================================================
 *
 *                           GStreamer Boiler Plate
 *
 * ============================================================================
 */
#define STATS_FNAME_1W_IDX 0
#define STATS_FNAME_1D_IDX 1
#define STATS_FNAME_2H_IDX 2

#define GST_CAT_DEFAULT cohfar_assignfar_debug
GST_DEBUG_CATEGORY_STATIC(GST_CAT_DEFAULT);

G_DEFINE_TYPE_WITH_CODE(CohfarAssignfar,
                        cohfar_assignfar,
                        GST_TYPE_BASE_TRANSFORM,
                        GST_DEBUG_CATEGORY_INIT(GST_CAT_DEFAULT,
                                                "cohfar_assignfar",
                                                0,
                                                "cohfar_assignfar element"))

enum property {
    PROP_0,
    PROP_IFOS,
    PROP_REFRESH_INTERVAL,
    PROP_SILENT_TIME,
    PROP_INPUT_FNAME,
    PROP_ASSIGN_MULTI_FAR,
    PROP_SINGLE_ENABLED,
    PROP_SINGLE_DOF,
    PROP_SINGLE_DETAIL_OUTPUT_FNAME,
    PROP_SINGLE_TEMPLATE_SHAPE_MAP_FNAME,
    PROP_SINGLE_LOG10_FAR_THRESHOLD,
    PROP_SINGLE_TAIL_LOG10_FAR,
    PROP_SINGLE_LIVETIME_STEP,
    PROP_SINGLE_STREAM_ID,
    PROP_SINGLE_STREAM_COUNT,
    PROP_SINGLE_STREAM_BANK_ID,
    PROP_SINGLE_WORKER_BANK_IDS
};

static void cohfar_assignfar_set_property(GObject *object,
                                          guint prop_id,
                                          const GValue *value,
                                          GParamSpec *pspec);

static void cohfar_assignfar_get_property(GObject *object,
                                          guint prop_id,
                                          GValue *value,
                                          GParamSpec *pspec);

/* vmethods */
static gboolean cohfar_assignfar_start(GstBaseTransform *base);
static GstFlowReturn cohfar_assignfar_transform_ip(GstBaseTransform *base,
                                                   GstBuffer *buf);
static void cohfar_assignfar_dispose(GObject *object);

static float
  _calculate_far(TriggerStats *stats, float snr, float chisq, int hist_trials) {
    if (stats->livetime <= 0) return 0;
    return BOUND(FLT_MIN, gen_fap_from_feature(snr, chisq, stats)
                            * stats->nevent / (stats->livetime * hist_trials));
}

static double _gps_to_seconds(const LIGOTimeGPS *gps) {
    if (!gps) return NAN;
    return (double)gps->gpsSeconds + 1.0e-9 * (double)gps->gpsNanoSeconds;
}

static float _best_positive_multi_far(const PostcohInspiralTable *table) {
    float best = 0.0f;
    const float fars[3] = { table->far_2h, table->far_1d, table->far_1w };
    for (int i = 0; i < 3; ++i) {
        if (isfinite(fars[i]) && fars[i] > 0.0f &&
            (best <= 0.0f || fars[i] < best)) {
            best = fars[i];
        }
    }
    return best;
}

G_LOCK_DEFINE_STATIC(assignfar_latency_file);
static FILE *assignfar_latency_file = NULL;
static gboolean assignfar_latency_checked = FALSE;

static FILE *_open_assignfar_latency_file(void) {
    if (assignfar_latency_checked) return assignfar_latency_file;

    assignfar_latency_checked = TRUE;
    const char *path = g_getenv("COHFAR_ASSIGNFAR_LATENCY_CSV");
    if (!path || !path[0]) return NULL;

    assignfar_latency_file = fopen(path, "a");
    if (!assignfar_latency_file) {
        g_warning("cohfar_assignfar: failed to open latency CSV %s", path);
        return NULL;
    }

    if (fseek(assignfar_latency_file, 0, SEEK_END) == 0 &&
        ftell(assignfar_latency_file) == 0) {
        fprintf(assignfar_latency_file,
                "event_id,bankid,tmplt_idx,end_time,end_time_ns,"
                "is_background,cohsnr,cmbchisq,far_2h,far_1d,far_1w,"
                "far_multi_min_positive,livetime_2h,livetime_1d,livetime_1w,"
                "nevent_2h,nevent_1d,nevent_1w,buf_pts_gps,feature_gps,"
                "assignment_unix\n");
        fflush(assignfar_latency_file);
    }

    return assignfar_latency_file;
}

static void _write_assignfar_latency_row(const PostcohInspiralTable *table,
                                         GstClockTime t_cur) {
    G_LOCK(assignfar_latency_file);
    FILE *file = _open_assignfar_latency_file();
    if (file) {
        const double assignment_unix = (double)g_get_real_time() / 1000000.0;
        const double buf_pts_gps =
          GST_CLOCK_TIME_IS_VALID(t_cur) ? (double)t_cur / (double)GST_SECOND
                                         : NAN;
        fprintf(file,
                "%ld,%d,%d,%d,%d,%d,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,"
                "%d,%d,%d,%d,%d,%d,%.9f,%.9f,%.9f\n",
                table->event_id, table->bankid, table->tmplt_idx,
                table->end_time.gpsSeconds, table->end_time.gpsNanoSeconds,
                table->is_background, table->cohsnr, table->cmbchisq,
                table->far_2h, table->far_1d, table->far_1w,
                _best_positive_multi_far(table),
                table->livetime_2h, table->livetime_1d, table->livetime_1w,
                table->nevent_2h, table->nevent_1d, table->nevent_1w,
                buf_pts_gps, _gps_to_seconds(&table->end_time),
                assignment_unix);
        fflush(file);
    }
    G_UNLOCK(assignfar_latency_file);
}

static void _update_fars(PostcohInspiralTable *table,
                         CohfarAssignfar *element) {
    int hist_trials   = element->hist_trials;
    int cmb_stats_idx = trigger_stats_num_stats(element->enabled_ifos) - 1;
    TriggerStatsXML *stats_1w = element->bgstats_1w;
    TriggerStatsXML *stats_1d = element->bgstats_1d;
    TriggerStatsXML *stats_2h = element->bgstats_2h;

    double max_rank = 0;
    max_rank        = MAX(trigger_stats_get_val_from_map(
                     table->cohsnr, table->cmbchisq,
                     stats_1w->multistats[cmb_stats_idx]->rank->rank_map),
                          max_rank);
    max_rank        = MAX(trigger_stats_get_val_from_map(
                     table->cohsnr, table->cmbchisq,
                     stats_1d->multistats[cmb_stats_idx]->rank->rank_map),
                          max_rank);
    max_rank        = MAX(trigger_stats_get_val_from_map(
                     table->cohsnr, table->cmbchisq,
                     stats_2h->multistats[cmb_stats_idx]->rank->rank_map),
                          max_rank);
    table->rank     = max_rank;

    table->far_1w = _calculate_far(stats_1w->multistats[cmb_stats_idx],
                                   table->cohsnr, table->cmbchisq, hist_trials);
    table->far_1d = _calculate_far(stats_1d->multistats[cmb_stats_idx],
                                   table->cohsnr, table->cmbchisq, hist_trials);
    table->far_2h = _calculate_far(stats_2h->multistats[cmb_stats_idx],
                                   table->cohsnr, table->cmbchisq, hist_trials);

    for (int ifo_id = 0, stats_idx = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        if (ifo_set__contains(element->enabled_ifos, ifo_id)) {
            table->far_1w_sngl[ifo_id] = _calculate_far(
              stats_1w->multistats[stats_idx], table->snglsnr[ifo_id],
              table->chisq[ifo_id], hist_trials);
            table->far_1d_sngl[ifo_id] = _calculate_far(
              stats_1d->multistats[stats_idx], table->snglsnr[ifo_id],
              table->chisq[ifo_id], hist_trials);
            table->far_2h_sngl[ifo_id] = _calculate_far(
              stats_2h->multistats[stats_idx], table->snglsnr[ifo_id],
              table->chisq[ifo_id], hist_trials);
            stats_idx++;
        }
    }

    GST_DEBUG_OBJECT(
      element, "The long-scale FAR %f, mid-scale FAR %f, short-scale FAR %f",
      table->far_1w, table->far_1d, table->far_2h);
}

static void _set_background_stats(PostcohInspiralTable *table,
                                  CohfarAssignfar *element) {
    int cmb_stats_idx = trigger_stats_num_stats(element->enabled_ifos) - 1;
    TriggerStatsXML *stats_1w = element->bgstats_1w;
    TriggerStatsXML *stats_1d = element->bgstats_1d;
    TriggerStatsXML *stats_2h = element->bgstats_2h;

    table->livetime_1w = stats_1w->multistats[cmb_stats_idx]->livetime;
    table->livetime_1d = stats_1d->multistats[cmb_stats_idx]->livetime;
    table->livetime_2h = stats_2h->multistats[cmb_stats_idx]->livetime;

    table->nevent_1w = stats_1w->multistats[cmb_stats_idx]->nevent;
    table->nevent_1d = stats_1d->multistats[cmb_stats_idx]->nevent;
    table->nevent_2h = stats_2h->multistats[cmb_stats_idx]->nevent;

    for (int ifo_id = 0, stats_idx = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        if (ifo_set__contains(element->enabled_ifos, ifo_id)) {
            table->livetime_1w_sngl[ifo_id] =
              stats_1w->multistats[stats_idx]->livetime;
            table->livetime_1d_sngl[ifo_id] =
              stats_1d->multistats[stats_idx]->livetime;
            table->livetime_2h_sngl[ifo_id] =
              stats_2h->multistats[stats_idx]->livetime;

            table->nevent_1w_sngl[ifo_id] =
              stats_1w->multistats[stats_idx]->nevent;
            table->nevent_1d_sngl[ifo_id] =
              stats_1d->multistats[stats_idx]->nevent;
            table->nevent_2h_sngl[ifo_id] =
              stats_2h->multistats[stats_idx]->nevent;

            stats_idx++;
        }
    }
}

/*
 * ============================================================================
 *
 *                     GstBaseTransform Method Overrides
 *
 * ============================================================================
 */

/*
 * transform_ip()
 */

static GstFlowReturn cohfar_assignfar_transform_multi(
  CohfarAssignfar *element,
  GstBuffer *buf) {
    GstFlowReturn result     = GST_FLOW_OK;

    GstClockTime t_cur = GST_BUFFER_PTS(buf);
    if (!GST_CLOCK_TIME_IS_VALID(element->t_start)) element->t_start = t_cur;

    /* Check that we have collected enough backgrounds */
    if (!GST_CLOCK_TIME_IS_VALID(element->t_roll_start)
        && (t_cur - element->t_start) / GST_SECOND
             >= (unsigned)element->silent_time) {
        /* FIXME: the order of input fnames must match the stats order */
        // printf("read input stats to assign far %s, %s, %s\n",
        // element->input_fnames[STATS_FNAME_1W_IDX],
        // element->input_fnames[STATS_FNAME_1D_IDX],
        // element->input_fnames[STATS_FNAME_2H_IDX]);
        element->pass_silent_time = TRUE;
        element->t_roll_start     = t_cur;
        if (!trigger_stats_xml_from_xml(
              element->bgstats_1w, &(element->hist_trials),
              element->input_fnames[STATS_FNAME_1W_IDX])) {
            element->pass_silent_time = FALSE;
            element->t_roll_start     = GST_CLOCK_TIME_NONE;
        }
        if (!trigger_stats_xml_from_xml(
              element->bgstats_1d, &(element->hist_trials),
              element->input_fnames[STATS_FNAME_1D_IDX])) {
            element->pass_silent_time = FALSE;
            element->t_roll_start     = GST_CLOCK_TIME_NONE;
        }
        if (!trigger_stats_xml_from_xml(
              element->bgstats_2h, &(element->hist_trials),
              element->input_fnames[STATS_FNAME_2H_IDX])) {
            element->pass_silent_time = FALSE;
            element->t_roll_start     = GST_CLOCK_TIME_NONE;
        }
    }

    /* Check if it is time to refresh the background stats */
    if (element->pass_silent_time && element->refresh_interval > 0
        && (t_cur - element->t_roll_start) / GST_SECOND
             > (unsigned)element->refresh_interval) {
        element->t_roll_start = t_cur;
        /* FIXME: the order of input fnames must match the stats order */
        // printf("read refreshed stats to assign far.");
        if (!trigger_stats_xml_from_xml(
              element->bgstats_1w, &(element->hist_trials),
              element->input_fnames[STATS_FNAME_1W_IDX])) {
            printf("1w data no longer available\n");
        }

        if (!trigger_stats_xml_from_xml(
              element->bgstats_1d, &(element->hist_trials),
              element->input_fnames[STATS_FNAME_1D_IDX])) {
            printf("1d data no longer available\n");
        }
        if (!trigger_stats_xml_from_xml(
              element->bgstats_2h, &(element->hist_trials),
              element->input_fnames[STATS_FNAME_2H_IDX])) {
            printf("2h data no longer available\n");
        }
    }

    TriggerStats *cur_stats;
    if (element->pass_silent_time) {
        ifo_set_type enabled_ifos;
        GstMapInfo mapInfo;
        gst_buffer_map(buf, &mapInfo, GST_MAP_WRITE);
        PostcohInspiralTable *table = (PostcohInspiralTable *)mapInfo.data;
        PostcohInspiralTable *table_end =
          (PostcohInspiralTable *)(mapInfo.data + mapInfo.size);
        for (; table < table_end; table++) {
            gboolean assigned_far = FALSE;
            if (table->is_background == FLAG_EMPTY) continue;
            if (!ifo_set__try_parse(table->ifos, &enabled_ifos)) {
                fprintf(stderr,
                        "cohfar_assign_transform_ip: failed to parse ifo set "
                        "\"%.16s\" (truncated to 16 characters)\n",
                        table->ifos);
                exit(0);
            }
            // This overwrites table->ifos, but not element->enabled_ifos
            enabled_ifos = scan_trigger_ifos(enabled_ifos, table);
            if (ifo_set__is_empty(enabled_ifos)) {
                fprintf(stderr, "enabled_ifos not found, cohfar_assignfar\n");
                exit(0);
            }
            int num_stats = trigger_stats_num_stats(element->enabled_ifos);
            cur_stats     = element->bgstats_1w->multistats[num_stats - 1];
            if (!ifo_set__is_empty(enabled_ifos)
                && cur_stats->nevent > MIN_BACKGROUND_NEVENT) {
                _update_fars(table, element);
                assigned_far = TRUE;
            }

            _set_background_stats(table, element);
            if (assigned_far) _write_assignfar_latency_row(table, t_cur);
        }
        gst_buffer_unmap(buf, &mapInfo);
    }

    return result;
}

static gboolean cohfar_assignfar_start(GstBaseTransform *base) {
    CohfarAssignfar *element = COHFAR_ASSIGNFAR(base);
    return crashcar_singlefar_engine_start(&element->single);
}

static GstFlowReturn cohfar_assignfar_transform_ip(GstBaseTransform *base,
                                                   GstBuffer *buf) {
    CohfarAssignfar *element = COHFAR_ASSIGNFAR(base);
    GstFlowReturn result = GST_FLOW_OK;

    /*
     * Before unification, GstBaseTransform bypassed the normal multi element
     * for GAP buffers, then the gap-aware single element passed them through
     * unchanged.  The unified element is gap-aware for the single engine, so
     * reproduce that two-element behaviour explicitly before either engine
     * observes or advances state from the GAP timestamp.
     */
    if (GST_BUFFER_FLAG_IS_SET(buf, GST_BUFFER_FLAG_GAP)) {
        return GST_FLOW_OK;
    }

    /* Preserve the old serial order exactly: multi first, then single. */
    if (element->assign_multi_far) {
        result = cohfar_assignfar_transform_multi(element, buf);
        if (result != GST_FLOW_OK) return result;
    }
    return crashcar_singlefar_engine_transform_ip(&element->single, buf);
}

/*
 * ============================================================================
 *
 *                          GObject Method Overrides
 *
 * ============================================================================
 */

/* handle events (search) */
static gboolean cohfar_assignfar_sink_event(GstBaseTransform *base,
                                            GstEvent *event) {
    CohfarAssignfar *element = COHFAR_ASSIGNFAR(base);

    switch (GST_EVENT_TYPE(event)) {
    case GST_EVENT_EOS:
        //      if (fflush (sink->file))
        //        goto flush_failed;

        GST_LOG_OBJECT(element, "EVENT EOS. Finish assign FAR");
        break;
    default: break;
    }

    return GST_BASE_TRANSFORM_CLASS(cohfar_assignfar_parent_class)
      ->sink_event(base, event);
}

/*
 * set_property()
 */

static void cohfar_assignfar_set_property(GObject *object,
                                          enum property prop_id,
                                          const GValue *value,
                                          GParamSpec *pspec) {
    CohfarAssignfar *element = COHFAR_ASSIGNFAR(object);

    GST_OBJECT_LOCK(element);
    switch (prop_id) {
    case PROP_IFOS:
        element->ifos = g_value_dup_string(value);
        element->nifo = strlen(element->ifos) / IFO_LEN;
        // TODO: Consider using ifo_set__try_parse to check for errors
        element->enabled_ifos = ifo_set__parse_or_empty(element->ifos);
        element->bgstats_1w =
          trigger_stats_xml_create(element->ifos, STATS_XML_TYPE_BACKGROUND);
        element->bgstats_1d =
          trigger_stats_xml_create(element->ifos, STATS_XML_TYPE_BACKGROUND);
        element->bgstats_2h =
          trigger_stats_xml_create(element->ifos, STATS_XML_TYPE_BACKGROUND);
        break;

    case PROP_INPUT_FNAME:
        /* must make sure ifos have been loaded */
        g_assert(element->ifos != NULL);
        element->input_fnames = g_strsplit(g_value_dup_string(value), ",", -1);
        element->ninput       = g_strv_length(element->input_fnames);
        if (element->ninput != 3) {
            fprintf(
              stderr,
              "Expected 3 input files for zerolag FAR assignment, "
              " but '%d' were provided."
              " Your cohfar-assignfar-input-fname option \"%s\" might not "
              "provide the right path"
              " for the input files. Exiting \n",
              element->ninput, g_value_dup_string(value));
            exit(0);
        }
        break;

    case PROP_SILENT_TIME: element->silent_time = g_value_get_int(value); break;

    case PROP_REFRESH_INTERVAL:
        element->refresh_interval = g_value_get_int(value);
        break;

    case PROP_ASSIGN_MULTI_FAR:
        element->assign_multi_far = g_value_get_boolean(value);
        break;

    case PROP_SINGLE_ENABLED:
        element->single.enabled = g_value_get_boolean(value);
        break;

    case PROP_SINGLE_DOF:
        element->single.dof = g_value_get_double(value);
        break;

    case PROP_SINGLE_DETAIL_OUTPUT_FNAME:
        g_free(element->single.detail_output_fname);
        element->single.detail_output_fname = g_value_dup_string(value);
        break;

    case PROP_SINGLE_TEMPLATE_SHAPE_MAP_FNAME:
        g_free(element->single.template_shape_map_fname);
        element->single.template_shape_map_fname = g_value_dup_string(value);
        if (element->single.template_shape_map) {
            g_hash_table_remove_all(element->single.template_shape_map);
        }
        element->single.template_shape_map_loaded = FALSE;
        break;

    case PROP_SINGLE_LOG10_FAR_THRESHOLD:
        element->single.log10_far_threshold = g_value_get_double(value);
        break;

    case PROP_SINGLE_TAIL_LOG10_FAR:
        element->single.tail_log10_far = g_value_get_double(value);
        break;

    case PROP_SINGLE_LIVETIME_STEP:
        element->single.livetime_step = g_value_get_double(value);
        break;

    case PROP_SINGLE_STREAM_ID:
        if (element->single.graph_binding_locked) {
            GST_ERROR_OBJECT(element,
                             "ignored immutable graph property stream-id");
            break;
        }
        element->single.stream_id = g_value_get_int(value);
        break;

    case PROP_SINGLE_STREAM_COUNT:
        if (element->single.graph_binding_locked) {
            GST_ERROR_OBJECT(element,
                             "ignored immutable graph property stream-count");
            break;
        }
        element->single.stream_count = g_value_get_int(value);
        break;

    case PROP_SINGLE_STREAM_BANK_ID:
        if (element->single.graph_binding_locked) {
            GST_ERROR_OBJECT(element,
                             "ignored immutable graph property stream-bank-id");
            break;
        }
        element->single.stream_bank_id = g_value_get_int(value);
        break;

    case PROP_SINGLE_WORKER_BANK_IDS:
        if (element->single.graph_binding_locked) {
            GST_ERROR_OBJECT(element,
                             "ignored immutable graph property worker-bank-ids");
            break;
        }
        g_free(element->single.worker_bank_ids);
        element->single.worker_bank_ids = g_value_dup_string(value);
        if (element->single.worker_bank_id_values) {
            g_array_set_size(element->single.worker_bank_id_values, 0);
        }
        break;

    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec); break;
    }

    GST_OBJECT_UNLOCK(element);
}

/*
 * get_property()
 */

static void cohfar_assignfar_get_property(GObject *object,
                                          enum property prop_id,
                                          GValue *value,
                                          GParamSpec *pspec) {
    CohfarAssignfar *element = COHFAR_ASSIGNFAR(object);

    GST_OBJECT_LOCK(element);

    // C doesn't allow a variable to be declared as the first statement of a
    // case
    gchar *serialized_input_fnames;
    switch (prop_id) {
    case PROP_IFOS: g_value_set_string(value, element->ifos); break;

    case PROP_INPUT_FNAME:
        serialized_input_fnames = NULL;
        if (element->input_fnames && element->ninput == 3) {
            serialized_input_fnames =
              g_strjoin(",", element->input_fnames[STATS_FNAME_1W_IDX],
                        element->input_fnames[STATS_FNAME_1D_IDX],
                        element->input_fnames[STATS_FNAME_2H_IDX], NULL);
        }
        g_value_set_string(value, serialized_input_fnames);
        g_free(serialized_input_fnames);
        break;

    case PROP_SILENT_TIME: g_value_set_int(value, element->silent_time); break;

    case PROP_REFRESH_INTERVAL:
        g_value_set_int(value, element->refresh_interval);
        break;
    case PROP_ASSIGN_MULTI_FAR:
        g_value_set_boolean(value, element->assign_multi_far);
        break;
    case PROP_SINGLE_ENABLED:
        g_value_set_boolean(value, element->single.enabled);
        break;
    case PROP_SINGLE_DOF:
        g_value_set_double(value, element->single.dof);
        break;
    case PROP_SINGLE_DETAIL_OUTPUT_FNAME:
        g_value_set_string(value, element->single.detail_output_fname);
        break;
    case PROP_SINGLE_TEMPLATE_SHAPE_MAP_FNAME:
        g_value_set_string(value, element->single.template_shape_map_fname);
        break;
    case PROP_SINGLE_LOG10_FAR_THRESHOLD:
        g_value_set_double(value, element->single.log10_far_threshold);
        break;
    case PROP_SINGLE_TAIL_LOG10_FAR:
        g_value_set_double(value, element->single.tail_log10_far);
        break;
    case PROP_SINGLE_LIVETIME_STEP:
        g_value_set_double(value, element->single.livetime_step);
        break;
    case PROP_SINGLE_STREAM_ID:
        g_value_set_int(value, element->single.stream_id);
        break;
    case PROP_SINGLE_STREAM_COUNT:
        g_value_set_int(value, element->single.stream_count);
        break;
    case PROP_SINGLE_STREAM_BANK_ID:
        g_value_set_int(value, element->single.stream_bank_id);
        break;
    case PROP_SINGLE_WORKER_BANK_IDS:
        g_value_set_string(value, element->single.worker_bank_ids);
        break;
    default: G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec); break;
    }
    GST_OBJECT_UNLOCK(element);
}

/*
 * dispose()
 */

static void cohfar_assignfar_dispose(GObject *object) {
    CohfarAssignfar *element = COHFAR_ASSIGNFAR(object);

    if (element->bgstats_1w) {
        trigger_stats_xml_destroy(element->bgstats_1w);
        trigger_stats_xml_destroy(element->bgstats_1d);
        trigger_stats_xml_destroy(element->bgstats_2h);
        element->bgstats_1w = NULL;
        element->bgstats_1d = NULL;
        element->bgstats_2h = NULL;
    }
    crashcar_singlefar_engine_clear(&element->single);
    g_clear_pointer(&element->input_fnames, g_strfreev);
    G_OBJECT_CLASS(cohfar_assignfar_parent_class)->dispose(object);
}

/*
 * class_init()
 */

static void cohfar_assignfar_class_init(CohfarAssignfarClass *klass) {
    GObjectClass *gobject_class = G_OBJECT_CLASS(klass);
    gobject_class->set_property =
      GST_DEBUG_FUNCPTR(cohfar_assignfar_set_property);
    gobject_class->get_property =
      GST_DEBUG_FUNCPTR(cohfar_assignfar_get_property);
    gobject_class->dispose = GST_DEBUG_FUNCPTR(cohfar_assignfar_dispose);

    g_object_class_install_property(
      gobject_class, PROP_IFOS,
      g_param_spec_string("ifos", "ifo names",
                          "ifos that participate in the pipeline", NULL,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_INPUT_FNAME,
      g_param_spec_string("input-fname", "input filename",
                          "Input background statistics filename", NULL,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_REFRESH_INTERVAL,
      g_param_spec_int(
        "refresh-interval", "refresh interval",
        "(0) never refresh stats; (N) refresh stats every N seconds. ", 0,
        G_MAXINT, 600, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_SILENT_TIME,
      g_param_spec_int("silent-time", "background silent time",
                       "(0) do not need background silent time; (N) allow N "
                       "seconds to accumulate background.",
                       0, G_MAXINT, G_MAXINT,
                       G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_ASSIGN_MULTI_FAR,
      g_param_spec_boolean(
        "assign-multi-far", "assign multi FAR",
        "run the normal multi/coherent FAR assignment before the single engine",
        TRUE, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_SINGLE_ENABLED,
      g_param_spec_boolean(
        "single-enabled", "single enabled",
        "enable the internal crashcar single-detector engine", FALSE,
        G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_SINGLE_DOF,
      g_param_spec_double(
        "dof", "effective degrees of freedom",
        "legacy single metadata only; runtime dof is derived from bankid",
        1.0e-12, G_MAXDOUBLE, 120.0,
        G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_SINGLE_DETAIL_OUTPUT_FNAME,
      g_param_spec_string(
        "detail-output-fname", "detail output filename",
        "CSV file for significant crashcar single-trigger details", NULL,
        G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_SINGLE_TEMPLATE_SHAPE_MAP_FNAME,
      g_param_spec_string(
        "template-shape-map-fname", "template shape map filename",
        "canonical CSV with ifo_id,bankid,tmplt_idx,a_eff,dof,ifo,source_class",
        NULL, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_SINGLE_LOG10_FAR_THRESHOLD,
      g_param_spec_double(
        "log10-far-threshold", "single log10 FAR threshold",
        "write detailed single rows when log10(FAR) is at or below this value",
        -G_MAXDOUBLE, G_MAXDOUBLE, -4.0,
        G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_SINGLE_TAIL_LOG10_FAR,
      g_param_spec_double(
        "tail-log10-far", "single tail log10 FAR anchor",
        "negative log10 FAR anchor for the single-detector tail fit",
        -G_MAXDOUBLE, -DBL_MIN, -2.0,
        G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_SINGLE_LIVETIME_STEP,
      g_param_spec_double(
        "livetime-step", "single livetime step",
        "default single livetime increment for FLAG_EMPTY rows",
        0.0, G_MAXDOUBLE, 1.0,
        G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_SINGLE_STREAM_ID,
      g_param_spec_int(
        "stream-id", "single stream id",
        "zero-based Postcoh stream ordinal in this worker",
        0, CRASHCAR_SINGLE_BANK_COUNT - 1, 0,
        G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_SINGLE_STREAM_COUNT,
      g_param_spec_int(
        "stream-count", "single stream count",
        "exact number of Postcoh bank streams in this worker",
        1, CRASHCAR_SINGLE_BANK_COUNT, 1,
        G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_SINGLE_STREAM_BANK_ID,
      g_param_spec_int(
        "stream-bank-id", "single stream bank id",
        "bank id derived from this Postcoh graph stream",
        0, CRASHCAR_SINGLE_BANK_COUNT - 1, 0,
        G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_SINGLE_WORKER_BANK_IDS,
      g_param_spec_string(
        "worker-bank-ids", "single worker bank ids",
        "canonical graph-ordered bank id roster", "0",
        G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    GstElementClass *gst_element_class = GST_ELEMENT_CLASS(klass);

    gst_element_class_set_metadata(
      gst_element_class, "unified FAR assignment for postcoh triggers",
      "assign FAR",
      "assign normal multi/coherent FAR and optional crashcar single FAR in-place.\n",
      "Qi Chu <qi.chu at ligo dot org>");

    GstCaps *template_caps = gst_caps_from_string("application/x-lal-postcoh");

    gst_element_class_add_pad_template(
      gst_element_class,
      //		gst_static_pad_template_get(&cohfar_background_src_template)
      gst_pad_template_new("sink", GST_PAD_SINK, GST_PAD_ALWAYS, template_caps)

    );

    gst_element_class_add_pad_template(
      gst_element_class,
      //		gst_static_pad_template_get(&cohfar_background_src_template)
      gst_pad_template_new("src", GST_PAD_SRC, GST_PAD_ALWAYS, template_caps));

    gst_caps_unref(template_caps);

    GstBaseTransformClass *transform_class = GST_BASE_TRANSFORM_CLASS(klass);

    transform_class->start = GST_DEBUG_FUNCPTR(cohfar_assignfar_start);
    transform_class->transform_ip =
      GST_DEBUG_FUNCPTR(cohfar_assignfar_transform_ip);
    transform_class->sink_event =
      GST_DEBUG_FUNCPTR(cohfar_assignfar_sink_event);
}
/*
 * init()
 */

static void cohfar_assignfar_init(CohfarAssignfar *element) {
    gst_base_transform_set_gap_aware(GST_BASE_TRANSFORM(element), TRUE);
    element->ifos             = NULL;
    element->bgstats_2h       = NULL;
    element->bgstats_1d       = NULL;
    element->bgstats_1w       = NULL;
    element->input_fnames     = NULL;
    element->t_start          = GST_CLOCK_TIME_NONE;
    element->t_roll_start     = GST_CLOCK_TIME_NONE;
    element->pass_silent_time = FALSE;
    element->ninput           = -1;
    element->assign_multi_far = TRUE;
    crashcar_singlefar_engine_init(&element->single, GST_ELEMENT(element));
}
