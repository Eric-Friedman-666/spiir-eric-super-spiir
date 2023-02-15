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
#include <math.h>
#include <string.h>
/*
 *  stuff from gobject/gstreamer
 */

// Suppresses a warning that only occurs on NVCC
// It should be revisited after the gstreamer upgrade
// See #15
#if defined(__CUDACC__)
#pragma diag_suppress 1217
#endif
#include <glib.h>
#if defined(__CUDACC__)
#pragma diag_default 1217
#endif

// Suppresses a warning from gstreamer using deprecated mutexes.
// Should be revisited after the gstreamer upgrade.
// See #15
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"
#include <gst/base/gstbasetransform.h>
#include <gst/gst.h>
#pragma GCC diagnostic pop

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

static void additional_initializations(GType type) {
    GST_DEBUG_CATEGORY_INIT(GST_CAT_DEFAULT, "cohfar_assignfar", 0,
                            "cohfar_assignfar element");
}

GST_BOILERPLATE_FULL(CohfarAssignfar,
                     cohfar_assignfar,
                     GstBaseTransform,
                     GST_TYPE_BASE_TRANSFORM,
                     additional_initializations);

enum property {
    PROP_0,
    PROP_IFOS,
    PROP_REFRESH_INTERVAL,
    PROP_SILENT_TIME,
    PROP_INPUT_FNAME
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
static GstFlowReturn cohfar_assignfar_transform_ip(GstBaseTransform *base,
                                                   GstBuffer *buf);
static void cohfar_assignfar_dispose(GObject *object);

static float
  _calculate_far(TriggerStats *stats, float snr, float chisq, int hist_trials) {
    if (stats->livetime <= 0) return 0;
    return BOUND(FLT_MIN, gen_fap_from_feature(snr, chisq, stats)
                            * stats->nevent / (stats->livetime * hist_trials));
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

static GstFlowReturn cohfar_assignfar_transform_ip(GstBaseTransform *trans,
                                                   GstBuffer *buf) {
    CohfarAssignfar *element = COHFAR_ASSIGNFAR(trans);
    GstFlowReturn result     = GST_FLOW_OK;

    GstClockTime t_cur = GST_BUFFER_TIMESTAMP(buf);
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
        PostcohInspiralTable *table =
          (PostcohInspiralTable *)GST_BUFFER_DATA(buf);
        PostcohInspiralTable *table_end =
          (PostcohInspiralTable *)(GST_BUFFER_DATA(buf) + GST_BUFFER_SIZE(buf));
        for (; table < table_end; table++) {
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
            }
        }
    }

    return result;
}

/*
 * ============================================================================
 *
 *                          GObject Method Overrides
 *
 * ============================================================================
 */

/* handle events (search) */
static gboolean cohfar_assignfar_event(GstBaseTransform *base,
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

    return TRUE;
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
        serialized_input_fnames =
          g_strjoin(",", element->input_fnames[STATS_FNAME_1W_IDX],
                    element->input_fnames[STATS_FNAME_1D_IDX],
                    element->input_fnames[STATS_FNAME_2H_IDX], NULL);
        g_value_set_string(value, serialized_input_fnames);
        g_free(serialized_input_fnames);
        break;

    case PROP_SILENT_TIME: g_value_set_int(value, element->silent_time); break;

    case PROP_REFRESH_INTERVAL:
        g_value_set_int(value, element->refresh_interval);
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
    }
    G_OBJECT_CLASS(parent_class)->dispose(object);
    g_strfreev(element->input_fnames);
}

/*
 * base_init()
 */

static void cohfar_assignfar_base_init(gpointer gclass) {
    GstElementClass *element_class         = GST_ELEMENT_CLASS(gclass);
    GstBaseTransformClass *transform_class = GST_BASE_TRANSFORM_CLASS(gclass);

    gst_element_class_set_details_simple(
      element_class, "assign FAR to postcoh triggers", "assign FAR",
      "assign FAR to postcoh triggers according to a given stats file.\n",
      "Qi Chu <qi.chu at ligo dot org>");
    gst_element_class_add_pad_template(
      element_class,
      //		gst_static_pad_template_get(&cohfar_background_src_template)
      gst_pad_template_new("sink", GST_PAD_SINK, GST_PAD_ALWAYS,
                           gst_caps_from_string("application/x-lal-postcoh"))

    );

    gst_element_class_add_pad_template(
      element_class,
      //		gst_static_pad_template_get(&cohfar_background_src_template)
      gst_pad_template_new("src", GST_PAD_SRC, GST_PAD_ALWAYS,
                           gst_caps_from_string("application/x-lal-postcoh")));

    transform_class->transform_ip =
      GST_DEBUG_FUNCPTR(cohfar_assignfar_transform_ip);
    transform_class->event = GST_DEBUG_FUNCPTR(cohfar_assignfar_event);
}

/*
 * class_init()
 */

static void cohfar_assignfar_class_init(CohfarAssignfarClass *klass) {
    GObjectClass *gobject_class = G_OBJECT_CLASS(klass);
    ;
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
}
/*
 * init()
 */

static void cohfar_assignfar_init(CohfarAssignfar *element,
                                  CohfarAssignfarClass *kclass) {
    element->ifos             = NULL;
    element->bgstats_2h       = NULL;
    element->bgstats_1d       = NULL;
    element->bgstats_1w       = NULL;
    element->input_fnames     = NULL;
    element->t_start          = GST_CLOCK_TIME_NONE;
    element->t_roll_start     = GST_CLOCK_TIME_NONE;
    element->pass_silent_time = FALSE;
    element->ninput           = -1;
}
