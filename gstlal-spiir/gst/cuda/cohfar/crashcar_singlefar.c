/*
 * Crashcar low-latency single-detector FAR stream hook.
 *
 * This element runs in-place on application/x-lal-postcoh buffers after the
 * coherent FAR assignment element. It is the C/GStreamer insertion point for
 * the eventual exact port of the Python single-detector sidecar. The current
 * implementation deliberately keeps the behaviour conservative: it exposes the
 * detector-local trigger fields and already-assigned single-FAR columns without
 * changing the coherent branch.
 */

#include <float.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <complex.h>

// Suppresses a warning that only occurs on NVCC
// It should be revisited after the gstreamer upgrade
// See #15
#if defined(__CUDACC__)
#pragma diag_suppress 1217
#endif
#include <glib.h>
#include <glib/gstdio.h>
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

#include <cohfar/crashcar_singlefar.h>
#include <ifo_set.h>
#include <pipe_macro.h>
#include <postcohtable.h>

#define GST_CAT_DEFAULT crashcar_singlefar_debug
GST_DEBUG_CATEGORY_STATIC(GST_CAT_DEFAULT);

#define CRASHCAR_CODE_VERSION "single_stream_support_v29_retained_calculated_far"
#define CRASHCAR_CLUSTER_WINDOW_SECONDS 1.0

/* Multiple crashcar elements can live in one worker process and append to the
 * same worker-level detail CSV. Guard each complete CSV line so diagnostic
 * output remains parseable without changing stream data. */
static GMutex crashcar_detail_file_mutex;
static GMutex crashcar_snr_series_file_mutex;
static GMutex crashcar_support_mutex;
static GMutex crashcar_cluster_mutex;
static GMutex crashcar_support_debug_file_mutex;
static GMutex crashcar_cluster_debug_file_mutex;
static GArray *crashcar_global_support_points[MAX_NIFO];
static const char *CRASHCAR_SNR_SERIES_SHARD_CLOSE =
  "\t</LIGO_LW>\n"
  "</LIGO_LW>\n";

typedef struct {
    double end_gps;
    double cohsnr;
    int bankid;
    int tmplt_idx;
    gboolean has_ifo[MAX_NIFO];
    double llr[MAX_NIFO];
    double gps[MAX_NIFO];
} CrashcarClusterEvent;

typedef struct {
    gboolean have_latest_buffers;
    double max_cluster_boundary;
    double buf_timestamp;
    double duration;
    guint expected_buffers;
    guint num_current_buffers;
} CrashcarBufferClusterState;

typedef struct {
    double start_gps;
    double end_gps;
    gboolean active[MAX_NIFO];
    guint active_count;
} CrashcarSingleOutputWindow;

static GArray *crashcar_cluster_events;
static CrashcarClusterEvent crashcar_cluster_candidate;
static gboolean crashcar_cluster_have_candidate = FALSE;
static double crashcar_cluster_boundary = NAN;
static double crashcar_cluster_max_seen = NAN;
static double crashcar_cluster_current_timestamp = NAN;
static guint crashcar_cluster_num_current_buffers = 0;
static GHashTable *crashcar_cluster_timestamp_counts = NULL;
static GHashTable *crashcar_cluster_completed_timestamps = NULL;
static gboolean crashcar_cluster_have_next_timestamp = FALSE;
static gint64 crashcar_cluster_next_timestamp_ns = 0;
static gboolean crashcar_cluster_is_first_event = TRUE;
static FILE *crashcar_support_debug_file = NULL;
static gboolean crashcar_support_debug_header_written = FALSE;
static FILE *crashcar_cluster_debug_file = NULL;
static gboolean crashcar_cluster_debug_header_written = FALSE;
static gboolean crashcar_single_output_policy_initialized = FALSE;
static gchar *crashcar_single_output_mode = NULL;
static GArray *crashcar_single_output_windows = NULL;

static GArray *crashcar_support_array_locked(int ifo_id) {
    if (ifo_id < 0 || ifo_id >= MAX_NIFO) return NULL;
    if (!crashcar_global_support_points[ifo_id]) {
        crashcar_global_support_points[ifo_id] =
          g_array_new(FALSE, FALSE, sizeof(CrashcarSupportPoint));
    }
    return crashcar_global_support_points[ifo_id];
}

static int crashcar_ifo_id_from_label(const char *label) {
    if (!label) return -1;
    while (g_ascii_isspace(*label) || *label == '"' || *label == '\'') ++label;
    if (g_str_has_prefix(label, "H1")) return 0;
    if (g_str_has_prefix(label, "L1")) return 1;
    if (g_str_has_prefix(label, "V1")) return 2;
    if (g_str_has_prefix(label, "K1")) return 3;
    return -1;
}

static gboolean crashcar_parse_double_field(const char *text, double *out) {
    if (!text || !out) return FALSE;
    while (g_ascii_isspace(*text) || *text == '"' || *text == '\'') ++text;
    char *end = NULL;
    double value = g_ascii_strtod(text, &end);
    while (end && (*end == '"' || *end == '\'' || g_ascii_isspace(*end))) ++end;
    if (end == text || !isfinite(value)) return FALSE;
    *out = value;
    return TRUE;
}

static gboolean crashcar_single_background_mode_is_frozen(void) {
    const char *mode = g_getenv("CRASHCAR_SINGLE_BACKGROUND_MODE");
    if (!mode || !mode[0]) mode = g_getenv("SINGLE_BACKGROUND_MODE");
    return mode && g_ascii_strcasecmp(mode, "frozen") == 0;
}

static void crashcar_load_livetime_segments(CrashcarSinglefar *element) {
    const char *fname = g_getenv("CRASHCAR_SEGMENT_LIVETIME_CSV");
    if (!fname || !fname[0]) return;

    FILE *file = fopen(fname, "r");
    if (!file) {
        GST_WARNING("unable to open CRASHCAR_SEGMENT_LIVETIME_CSV=%s", fname);
        return;
    }

    char line[1024];
    guint loaded = 0;
    while (fgets(line, sizeof(line), file)) {
        g_strchomp(line);
        char *trimmed = g_strstrip(line);
        if (!trimmed[0] || trimmed[0] == '#') continue;
        if (g_ascii_strncasecmp(trimmed, "ifo,", 4) == 0) continue;

        char **fields = g_strsplit(trimmed, ",", 4);
        if (!fields || !fields[0] || !fields[1] || !fields[2]) {
            g_strfreev(fields);
            continue;
        }
        int ifo_id = crashcar_ifo_id_from_label(fields[0]);
        double start = NAN;
        double end = NAN;
        if (ifo_id >= 0 &&
            crashcar_parse_double_field(fields[1], &start) &&
            crashcar_parse_double_field(fields[2], &end) &&
            end > start) {
            CrashcarLivetimeSegment segment;
            segment.start_gps = start;
            segment.end_gps = end;
            g_array_append_val(element->livetime_segments[ifo_id], segment);
            element->have_livetime_segments = TRUE;
            ++loaded;
        }
        g_strfreev(fields);
    }
    fclose(file);
    GST_INFO("loaded %u crashcar single-FAR livetime segments from %s",
             loaded, fname);
}

static int crashcar_csv_column(char **fields, const char *name) {
    if (!fields || !name) return -1;
    for (int i = 0; fields[i]; ++i) {
        char *field = g_strstrip(fields[i]);
        if (g_ascii_strcasecmp(field, name) == 0) return i;
    }
    return -1;
}

static const char *crashcar_csv_value(char **fields, int index) {
    if (!fields || index < 0) return "";
    for (int i = 0; i <= index; ++i) {
        if (!fields[i]) return "";
    }
    return fields[index] ? fields[index] : "";
}

static void crashcar_load_frozen_single_support(CrashcarSinglefar *element) {
    if (!element || !element->frozen_single_background) return;

    const char *fname = g_getenv("CRASHCAR_FROZEN_SINGLE_SUPPORT_CSV");
    if (!fname || !fname[0]) {
        GST_WARNING("single_background_mode=frozen but CRASHCAR_FROZEN_SINGLE_SUPPORT_CSV is unset");
        return;
    }

    FILE *file = fopen(fname, "r");
    if (!file) {
        GST_WARNING("unable to open CRASHCAR_FROZEN_SINGLE_SUPPORT_CSV=%s", fname);
        return;
    }

    int ifo_col = 0;
    int rank_col = 1;
    int far_col = -1;
    int livetime_col = 3;
    int kind_col = -1;
    char line[4096];
    guint loaded_support = 0;
    guint loaded_fit = 0;
    guint skipped = 0;

    while (fgets(line, sizeof(line), file)) {
        g_strchomp(line);
        char *trimmed = g_strstrip(line);
        if (!trimmed[0] || trimmed[0] == '#') continue;

        char **fields = g_strsplit(trimmed, ",", 0);
        if (!fields || !fields[0]) {
            g_strfreev(fields);
            continue;
        }

        if (crashcar_csv_column(fields, "ifo") >= 0) {
            int col = crashcar_csv_column(fields, "kind");
            if (col >= 0) kind_col = col;
            col = crashcar_csv_column(fields, "ifo");
            if (col >= 0) ifo_col = col;
            col = crashcar_csv_column(fields, "rank");
            if (col >= 0) rank_col = col;
            col = crashcar_csv_column(fields, "far");
            if (col >= 0) far_col = col;
            col = crashcar_csv_column(fields, "livetime");
            if (col >= 0) livetime_col = col;
            g_strfreev(fields);
            continue;
        }

        const char *kind = crashcar_csv_value(fields, kind_col);
        const int ifo_id =
          crashcar_ifo_id_from_label(crashcar_csv_value(fields, ifo_col));
        double rank = NAN;
        if (ifo_id < 0 ||
            !crashcar_parse_double_field(crashcar_csv_value(fields, rank_col),
                                         &rank)) {
            ++skipped;
            g_strfreev(fields);
            continue;
        }

        double livetime = NAN;
        if (crashcar_parse_double_field(crashcar_csv_value(fields, livetime_col),
                                        &livetime) &&
            livetime > 0.0) {
            element->frozen_livetime[ifo_id] = livetime;
        }

        const gboolean is_fit =
          kind && kind[0] && g_ascii_strcasecmp(kind, "fit") == 0;
        if (is_fit) {
            double far = NAN;
            if (!crashcar_parse_double_field(crashcar_csv_value(fields, far_col),
                                             &far) ||
                far <= 0.0) {
                ++skipped;
                g_strfreev(fields);
                continue;
            }
            const double log_far = log10(far);
            g_array_append_val(element->frozen_fit_ranks[ifo_id], rank);
            g_array_append_val(element->frozen_fit_log_fars[ifo_id], log_far);
            ++loaded_fit;
        } else {
            g_array_append_val(element->frozen_ranks[ifo_id], rank);
            ++loaded_support;
        }
        g_strfreev(fields);
    }
    fclose(file);

    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        if (!ifo_set__contains(element->enabled_ifos, ifo_id)) continue;
        if (element->frozen_ranks[ifo_id] &&
            element->frozen_ranks[ifo_id]->len > 0 &&
            element->frozen_livetime[ifo_id] > 0.0) {
            element->frozen_support_loaded = TRUE;
        }
    }
    GST_INFO("loaded frozen single-FAR support from %s (support=%u fit=%u skipped=%u loaded=%d)",
             fname, loaded_support, loaded_fit, skipped,
             element->frozen_support_loaded ? 1 : 0);
}

static GArray *crashcar_cluster_events_locked(void) {
    if (!crashcar_cluster_events) {
        crashcar_cluster_events =
          g_array_new(FALSE, FALSE, sizeof(CrashcarClusterEvent));
    }
    return crashcar_cluster_events;
}

static gboolean crashcar_cluster_event_is_better(const CrashcarClusterEvent *lhs,
                                                 const CrashcarClusterEvent *rhs) {
    if (lhs->cohsnr != rhs->cohsnr) return lhs->cohsnr > rhs->cohsnr;
    if (lhs->end_gps != rhs->end_gps) return lhs->end_gps < rhs->end_gps;
    if (lhs->bankid != rhs->bankid) return lhs->bankid < rhs->bankid;
    return lhs->tmplt_idx < rhs->tmplt_idx;
}

static double crashcar_env_double(const char *name, double fallback) {
    const char *value = g_getenv(name);
    if (!value || !value[0]) return fallback;
    char *end = NULL;
    double parsed = g_ascii_strtod(value, &end);
    if (end == value || !isfinite(parsed)) return fallback;
    return parsed;
}

static double crashcar_gps_to_seconds(const LIGOTimeGPS *gps) {
    if (!gps) return NAN;
    return (double)gps->gpsSeconds + 1.0e-9 * (double)gps->gpsNanoSeconds;
}

static const LIGOTimeGPS *crashcar_detail_end_time(const PostcohInspiralTable *table,
                                                   int ifo_id) {
    const LIGOTimeGPS *detail_end_time = &table->end_time_sngl[ifo_id];
    if (detail_end_time->gpsSeconds == 0 &&
        detail_end_time->gpsNanoSeconds == 0) {
        detail_end_time = &table->end_time;
    }
    return detail_end_time;
}

static double crashcar_assignment_window_end(const CrashcarSinglefar *element,
                                             double feature_gps) {
    double end = feature_gps;
    if (element->data_start_gps > 0.0 &&
        element->snapshot_interval_seconds > 0.0 &&
        isfinite(feature_gps) &&
        feature_gps >= element->data_start_gps) {
        const double steps = floor(
          (feature_gps - element->data_start_gps + 1.0e-9) /
          element->snapshot_interval_seconds);
        end = element->data_start_gps +
              steps * element->snapshot_interval_seconds;
    }
    if (element->data_start_gps > 0.0 &&
        element->background_window_seconds > 0.0) {
        const double first_full_end =
          element->data_start_gps + element->background_required_seconds;
        const double update = element->background_update_seconds;
        if (update > 0.0 && end >= first_full_end) {
            const double steps = floor((end - first_full_end + 1.0e-6) / update);
            end = first_full_end + steps * update;
        }
    }
    return end;
}

#define CRASHCAR_LOG_ZERO (-1.0e300)
#define CRASHCAR_NCX2_MAX_TERMS 200
#define CRASHCAR_NCX2_REL_TOL 1.0e-12
#define CRASHCAR_BETA_MAX 0.03
#define CRASHCAR_BETA_GRID_SIZE 31
#define CRASHCAR_FIT_MIN_POINTS 20
#define CRASHCAR_FIT_BOUNDARY_FAR 1.0e-2
#define CRASHCAR_FIT_PRETAIL_FAR 1.0e-1

G_DEFINE_TYPE_WITH_CODE(CrashcarSinglefar,
                        crashcar_singlefar,
                        GST_TYPE_BASE_TRANSFORM,
                        GST_DEBUG_CATEGORY_INIT(
                          GST_CAT_DEFAULT,
                          "crashcar_singlefar",
                          0,
                          "crashcar single-detector FAR stream hook"))

enum property {
    PROP_0,
    PROP_IFOS,
    PROP_ENABLED,
    PROP_DETAIL_OUTPUT_FNAME,
    PROP_TEMPLATE_SHAPE_MAP_FNAME,
    PROP_LOG10_FAR_THRESHOLD,
    PROP_MIN_SNR,
    PROP_FAR_FLOOR_COUNT,
    PROP_LIVETIME_STEP
};

static void crashcar_singlefar_set_property(GObject *object,
                                            guint prop_id,
                                            const GValue *value,
                                            GParamSpec *pspec);
static void crashcar_singlefar_get_property(GObject *object,
                                            guint prop_id,
                                            GValue *value,
                                            GParamSpec *pspec);
static void crashcar_singlefar_dispose(GObject *object);
static GstFlowReturn crashcar_singlefar_transform_ip(GstBaseTransform *base,
                                                     GstBuffer *buf);

static double crashcar_logaddexp(double a, double b) {
    if (a <= CRASHCAR_LOG_ZERO / 2.0) return b;
    if (b <= CRASHCAR_LOG_ZERO / 2.0) return a;
    if (a < b) {
        double tmp = a;
        a = b;
        b = tmp;
    }
    return a + log1p(exp(b - a));
}

static double crashcar_central_chisq_logpdf(double x, double dof) {
    if (x <= 0.0 || dof <= 0.0) return CRASHCAR_LOG_ZERO;
    double half_dof = 0.5 * dof;
    return ((half_dof - 1.0) * log(x) - 0.5 * x -
            half_dof * log(2.0) - lgamma(half_dof));
}


static gchar *crashcar_template_shape_key(int ifo_id,
                                          int bankid,
                                          int tmplt_idx) {
    return g_strdup_printf("%d:%d:%d", ifo_id, bankid, tmplt_idx);
}

static gboolean crashcar_line_starts_data(const char *line) {
    if (!line) return FALSE;
    while (*line == ' ' || *line == '\t') ++line;
    return (*line == '-' || (*line >= '0' && *line <= '9'));
}

static gboolean crashcar_parse_template_shape_line(
  const char *line, int *ifo_id, int *bankid, int *tmplt_idx,
  double *autocorr_power, double *dof) {
    if (!crashcar_line_starts_data(line)) return FALSE;
    char *copy = g_strdup(line);
    char *saveptr = NULL;
    char *fields[5] = { 0 };
    int nfield = 0;
    for (char *tok = strtok_r(copy, ",\n\r", &saveptr);
         tok && nfield < 5;
         tok = strtok_r(NULL, ",\n\r", &saveptr)) {
        fields[nfield++] = tok;
    }
    if (nfield < 5) {
        g_free(copy);
        return FALSE;
    }
    char *end = NULL;
    long parsed_ifo = strtol(fields[0], &end, 10);
    if (end == fields[0]) { g_free(copy); return FALSE; }
    long parsed_bank = strtol(fields[1], &end, 10);
    if (end == fields[1]) { g_free(copy); return FALSE; }
    long parsed_tmplt = strtol(fields[2], &end, 10);
    if (end == fields[2]) { g_free(copy); return FALSE; }
    double parsed_power = g_ascii_strtod(fields[3], &end);
    if (end == fields[3]) parsed_power = NAN;
    double parsed_dof = g_ascii_strtod(fields[4], &end);
    if (end == fields[4]) parsed_dof = NAN;

    *ifo_id = (int)parsed_ifo;
    *bankid = (int)parsed_bank;
    *tmplt_idx = (int)parsed_tmplt;
    *autocorr_power = parsed_power;
    *dof = parsed_dof;
    g_free(copy);
    return TRUE;
}

static gboolean crashcar_env_truthy(const char *name);

static void crashcar_load_template_shape_map(CrashcarSinglefar *element) {
    if (element->template_shape_map_loaded) return;
    element->template_shape_map_loaded = TRUE;
    if (!element->template_shape_map) {
        element->template_shape_map = g_hash_table_new_full(
          g_str_hash, g_str_equal, g_free, g_free);
    }
    if (!element->template_shape_map_fname ||
        !element->template_shape_map_fname[0]) {
        if (crashcar_env_truthy("CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP")) {
            GST_ERROR_OBJECT(element,
                             "CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP is set but "
                             "template-shape-map-fname is empty");
            g_error("crashcar template shape map is required but not configured");
        }
        GST_INFO_OBJECT(element,
                        "no crashcar template shape map configured; using defaults");
        return;
    }

    FILE *input = fopen(element->template_shape_map_fname, "r");
    if (!input) {
        if (crashcar_env_truthy("CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP")) {
            GST_ERROR_OBJECT(element,
                             "CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP is set but "
                             "failed to open crashcar template shape map %s",
                             element->template_shape_map_fname);
            g_error("crashcar template shape map is required but cannot be opened");
        }
        GST_WARNING_OBJECT(element,
                           "failed to open crashcar template shape map %s; using defaults",
                           element->template_shape_map_fname);
        return;
    }

    char line[1024];
    guint loaded = 0;
    while (fgets(line, sizeof(line), input)) {
        int ifo_id = -1, bankid = -1, tmplt_idx = -1;
        double autocorr_power = NAN, dof = NAN;
        if (!crashcar_parse_template_shape_line(
              line, &ifo_id, &bankid, &tmplt_idx, &autocorr_power, &dof)) {
            continue;
        }
        if (ifo_id < 0 || ifo_id >= MAX_NIFO || bankid < 0 || tmplt_idx < 0) {
            continue;
        }
        CrashcarTemplateShape *shape = g_new0(CrashcarTemplateShape, 1);
        if (autocorr_power > 0.0 && isfinite(autocorr_power)) {
            shape->autocorr_power = autocorr_power;
            shape->has_autocorr_power = TRUE;
        }
        if (dof > 0.0 && isfinite(dof)) {
            shape->dof = dof;
            shape->has_dof = TRUE;
        }
        if (!shape->has_autocorr_power && !shape->has_dof) {
            g_free(shape);
            continue;
        }
        gchar *key = crashcar_template_shape_key(ifo_id, bankid, tmplt_idx);
        g_hash_table_replace(element->template_shape_map, key, shape);
        ++loaded;
    }
    fclose(input);
    if (loaded == 0 &&
        crashcar_env_truthy("CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP")) {
        GST_ERROR_OBJECT(element,
                         "CRASHCAR_REQUIRE_TEMPLATE_SHAPE_MAP is set but "
                         "loaded zero rows from crashcar template shape map %s",
                         element->template_shape_map_fname);
        g_error("crashcar template shape map is required but loaded zero rows");
    }
    GST_INFO_OBJECT(element, "loaded %u crashcar template shape rows from %s",
                    loaded, element->template_shape_map_fname);
}

static void crashcar_lookup_template_shape(const CrashcarSinglefar *element_const,
                                           int ifo_id,
                                           int bankid,
                                           int tmplt_idx,
                                           double *autocorr_power,
                                           double *dof) {
    CrashcarSinglefar *element = (CrashcarSinglefar *)element_const;
    *autocorr_power = 1.0;
    *dof = 2.0;
    if (!element->template_shape_map_loaded) {
        crashcar_load_template_shape_map(element);
    }
    if (!element->template_shape_map) return;
    gchar *key = crashcar_template_shape_key(ifo_id, bankid, tmplt_idx);
    CrashcarTemplateShape *shape =
      (CrashcarTemplateShape *)g_hash_table_lookup(element->template_shape_map,
                                                   key);
    g_free(key);
    if (!shape) return;
    if (shape->has_autocorr_power) *autocorr_power = shape->autocorr_power;
    if (shape->has_dof) *dof = shape->dof;
}

static int crashcar_noncentral_chisq_term_mode_guess(double x,
                                                     double dof,
                                                     double noncentrality) {
    double lam = noncentrality;
    if (x <= 0.0 || dof <= 0.0 || lam <= 0.0) return 0;
    double b = 2.0 * (dof + 2.0);
    double c = 2.0 * dof - lam * x;
    double disc = b * b - 16.0 * c;
    if (disc <= 0.0) return 0;
    int mode = (int)((-b + sqrt(disc)) / 8.0);
    return mode > 0 ? mode : 0;
}

static double crashcar_poisson_logpmf(int n, double mean) {
    if (n < 0) return CRASHCAR_LOG_ZERO;
    if (mean <= 0.0) return n == 0 ? 0.0 : CRASHCAR_LOG_ZERO;
    return -mean + (double)n * log(mean) - lgamma((double)n + 1.0);
}

static double crashcar_noncentral_chisq_logpdf(double x,
                                              double dof,
                                              double noncentrality) {
    double lam = noncentrality;
    if (lam <= 0.0) return crashcar_central_chisq_logpdf(x, dof);

    double half_lam = 0.5 * lam;
    int mode = crashcar_noncentral_chisq_term_mode_guess(x, dof, lam);
    double mode_term =
      crashcar_poisson_logpmf(mode, half_lam) +
      crashcar_central_chisq_logpdf(x, dof + 2.0 * mode);

    while (mode > 0) {
        double previous =
          crashcar_poisson_logpmf(mode - 1, half_lam) +
          crashcar_central_chisq_logpdf(x, dof + 2.0 * (mode - 1));
        if (previous <= mode_term) break;
        mode -= 1;
        mode_term = previous;
    }
    while (TRUE) {
        double next = crashcar_poisson_logpmf(mode + 1, half_lam) +
                      crashcar_central_chisq_logpdf(x, dof + 2.0 * (mode + 1));
        if (next <= mode_term) break;
        mode += 1;
        mode_term = next;
    }

    double cutoff = mode_term + log(CRASHCAR_NCX2_REL_TOL);
    int side_limit = MAX(CRASHCAR_NCX2_MAX_TERMS,
                         (int)(12.0 * sqrt(MAX(1.0, half_lam))) + 50);
    double total = mode_term;

    int steps = 0;
    for (int n = mode - 1; n >= 0 && steps < side_limit; --n, ++steps) {
        double term = crashcar_poisson_logpmf(n, half_lam) +
                      crashcar_central_chisq_logpdf(x, dof + 2.0 * n);
        if (term < cutoff) break;
        total = crashcar_logaddexp(total, term);
    }

    steps = 0;
    for (int n = mode + 1; steps < side_limit; ++n, ++steps) {
        double term = crashcar_poisson_logpmf(n, half_lam) +
                      crashcar_central_chisq_logpdf(x, dof + 2.0 * n);
        if (term < cutoff) break;
        total = crashcar_logaddexp(total, term);
    }

    return total;
}

static double crashcar_noncentrality(double rho,
                                     double beta,
                                     double autocorr_power) {
    return beta * beta * rho * rho * autocorr_power;
}

static double crashcar_log_signal_shape_pdf(double rho,
                                            double chisq,
                                            double autocorr_power,
                                            double dof) {
    double x = dof * chisq;
    double weight_log = -log((double)CRASHCAR_BETA_GRID_SIZE);
    double total = CRASHCAR_LOG_ZERO;
    for (int i = 0; i < CRASHCAR_BETA_GRID_SIZE; ++i) {
        double beta = CRASHCAR_BETA_GRID_SIZE == 1
                        ? 0.5 * CRASHCAR_BETA_MAX
                        : CRASHCAR_BETA_MAX * (double)i /
                            (double)(CRASHCAR_BETA_GRID_SIZE - 1);
        double lam = crashcar_noncentrality(rho, beta, autocorr_power);
        double term = weight_log +
                      crashcar_noncentral_chisq_logpdf(x, dof, lam);
        total = crashcar_logaddexp(total, term);
    }
    return log(dof) + total;
}

static double crashcar_log_noise_shape_pdf(double rho,
                                           double chisq,
                                           double autocorr_power,
                                           double dof) {
    double x = dof * chisq;
    double noise_beta = -1.0;
    double lam = crashcar_noncentrality(rho, noise_beta, autocorr_power);
    return log(dof) + crashcar_noncentral_chisq_logpdf(x, dof, lam);
}

static double crashcar_single_detector_llr(double rho,
                                           double chisq,
                                           double autocorr_power,
                                           double dof) {
    double shape_llr =
      crashcar_log_signal_shape_pdf(rho, chisq, autocorr_power, dof) -
      crashcar_log_noise_shape_pdf(rho, chisq, autocorr_power, dof);
    return shape_llr + 0.5 * rho * rho;
}

static gboolean crashcar_singlefar_open_detail(CrashcarSinglefar *element) {
    if (element->detail_output_file) return TRUE;
    if (!element->detail_output_fname || !element->detail_output_fname[0]) {
        return FALSE;
    }

    g_mutex_lock(&crashcar_detail_file_mutex);
    if (!element->detail_output_file) {
        element->detail_output_file = fopen(element->detail_output_fname, "a");
        if (!element->detail_output_file) {
            GST_WARNING_OBJECT(element, "failed to open crashcar detail output %s",
                               element->detail_output_fname);
            g_mutex_unlock(&crashcar_detail_file_mutex);
            return FALSE;
        }

        if (fseek(element->detail_output_file, 0, SEEK_END) == 0 &&
            ftell(element->detail_output_file) == 0) {
            fprintf(element->detail_output_file,
                    "event_id,bankid,tmplt_idx,end_time,end_time_ns,ifo_id,"
                    "is_background,snglsnr,chisq,llr,direct_far,"
                    "direct_far_count_ge,bg_livetime,cohsnr,cmbchisq,far_multi,"
                    "far_1w_sngl,far_1d_sngl,far_2h_sngl,far_sngl,"
                    "autocorr_power,dof,has_snr_series,bg_start,bg_end,"
                    "window_count,total_window_count,code_version,feature_gps,"
                    "assignment_gps,assignment_unix\n");
            fflush(element->detail_output_file);
            element->detail_output_header_written = TRUE;
        }
    }
    g_mutex_unlock(&crashcar_detail_file_mutex);
    return TRUE;
}

static inline gboolean crashcar_far_is_valid(float far) {
    return far > 0.0f && isfinite(far);
}

static inline gboolean crashcar_far_double_is_valid(double far) {
    return far > 0.0 && isfinite(far);
}

static gint crashcar_compare_double(gconstpointer a, gconstpointer b) {
    const double da = *(const double *)a;
    const double db = *(const double *)b;
    return (da > db) - (da < db);
}

static double crashcar_interp_linear(const double *xs,
                                     const double *ys,
                                     guint n,
                                     double x) {
    if (n == 0) return NAN;
    if (x <= xs[0]) return ys[0];
    if (x >= xs[n - 1]) return ys[n - 1];
    guint lo = 0;
    guint hi = n - 1;
    while (hi - lo > 1) {
        guint mid = lo + (hi - lo) / 2;
        if (xs[mid] < x) lo = mid;
        else hi = mid;
    }
    if (xs[hi] == xs[lo]) return MIN(ys[lo], ys[hi]);
    double w = (x - xs[lo]) / (xs[hi] - xs[lo]);
    return ys[lo] + w * (ys[hi] - ys[lo]);
}

static gboolean crashcar_fit_line_through_fixed_point(const double *xs,
                                                      const double *ys,
                                                      guint n,
                                                      double x0,
                                                      double y0,
                                                      double *slope,
                                                      double *intercept) {
    if (n < 2) return FALSE;
    double denom = 0.0;
    double numer = 0.0;
    for (guint i = 0; i < n; ++i) {
        const double dx = xs[i] - x0;
        denom += dx * dx;
        numer += dx * (ys[i] - y0);
    }
    if (denom <= 0.0) return FALSE;
    *slope = numer / denom;
    if (!isfinite(*slope) || *slope >= 0.0) return FALSE;
    *intercept = y0 - (*slope) * x0;
    return TRUE;
}

static gboolean crashcar_fitted_far_from_ranks(const double *input_ranks,
                                               guint n_all,
                                               double livetime,
                                               double far_floor_count,
                                               double rank,
                                               double *far_out) {
    if (!far_out) return FALSE;
    *far_out = NAN;
    if (!input_ranks || n_all == 0 || livetime <= 0.0) return FALSE;
    if (n_all < CRASHCAR_FIT_MIN_POINTS) return FALSE;

    double *sorted = g_new(double, n_all);
    for (guint i = 0; i < n_all; ++i) sorted[i] = input_ranks[i];
    qsort(sorted, n_all, sizeof(double), crashcar_compare_double);

    double *raw_xs = g_new(double, n_all);
    double *raw_log_fars = g_new(double, n_all);
    guint n_raw = 0;
    double running = NAN;
    for (guint i = 0; i < n_all;) {
        const double llr = sorted[i];
        guint j = i + 1;
        while (j < n_all && sorted[j] == llr) ++j;
        const double count_ge = (double)(n_all - i);
        const double effective_count = MAX(count_ge, far_floor_count);
        const double far = effective_count / livetime;
        double log_far = log10(far);
        running = (n_raw == 0) ? log_far : MIN(running, log_far);
        raw_xs[n_raw] = llr;
        raw_log_fars[n_raw] = running;
        ++n_raw;
        i = j;
    }
    g_free(sorted);

    if (n_raw < CRASHCAR_FIT_MIN_POINTS) {
        *far_out = pow(10.0, crashcar_interp_linear(raw_xs, raw_log_fars,
                                                     n_raw, rank));
        g_free(raw_xs);
        g_free(raw_log_fars);
        return isfinite(*far_out) && *far_out > 0.0;
    }

    const double tail_log_far = log10(CRASHCAR_FIT_BOUNDARY_FAR);
    guint tail_idx = 0;
    double best_dist = fabs(raw_log_fars[0] - tail_log_far);
    for (guint i = 1; i < n_raw; ++i) {
        double dist = fabs(raw_log_fars[i] - tail_log_far);
        if (dist < best_dist) {
            best_dist = dist;
            tail_idx = i;
        }
    }
    const double x_handoff = raw_xs[tail_idx];
    const guint n_tail = n_raw - tail_idx;
    guint min_tail_points = MIN((guint)CRASHCAR_FIT_MIN_POINTS, (guint)20);
    min_tail_points = MIN(min_tail_points, n_tail);
    min_tail_points = MAX((guint)2, min_tail_points);

    double fitted_log_far = NAN;
    if (n_tail >= min_tail_points) {
        double slope = NAN;
        double intercept = NAN;
        if (crashcar_fit_line_through_fixed_point(raw_xs + tail_idx,
                                                  raw_log_fars + tail_idx,
                                                  n_tail,
                                                  x_handoff,
                                                  tail_log_far,
                                                  &slope,
                                                  &intercept)) {
            if (rank >= x_handoff) {
                fitted_log_far = slope * rank + intercept;
            } else {
                double saved = raw_log_fars[tail_idx];
                raw_log_fars[tail_idx] = tail_log_far;
                for (guint i = 1; i <= tail_idx; ++i) {
                    raw_log_fars[i] = MIN(raw_log_fars[i - 1], raw_log_fars[i]);
                }
                fitted_log_far = crashcar_interp_linear(raw_xs, raw_log_fars,
                                                        tail_idx + 1, rank);
                raw_log_fars[tail_idx] = saved;
            }
        }
    }

    if (!isfinite(fitted_log_far)) {
        fitted_log_far = crashcar_interp_linear(raw_xs, raw_log_fars,
                                                n_raw, rank);
    }
    *far_out = pow(10.0, fitted_log_far);
    g_free(raw_xs);
    g_free(raw_log_fars);
    return isfinite(*far_out) && *far_out > 0.0;
}

static gboolean crashcar_frozen_fitted_far_from_points(
    const CrashcarSinglefar *element,
    int ifo_id,
    double rank,
    double *far_out) {
    if (!far_out) return FALSE;
    *far_out = NAN;
    if (!element || ifo_id < 0 || ifo_id >= MAX_NIFO || !isfinite(rank)) {
        return FALSE;
    }

    const GArray *xs = element->frozen_fit_ranks[ifo_id];
    const GArray *ys = element->frozen_fit_log_fars[ifo_id];
    if (!xs || !ys || xs->len == 0 || xs->len != ys->len) return FALSE;

    const double log_far =
      crashcar_interp_linear((const double *)xs->data,
                             (const double *)ys->data,
                             xs->len, rank);
    if (!isfinite(log_far)) return FALSE;

    *far_out = pow(10.0, log_far);
    return crashcar_far_double_is_valid(*far_out);
}

static guint crashcar_collect_window_ranks(const CrashcarSinglefar *element,
                                           int ifo_id,
                                           double start,
                                           double end,
                                           double **ranks_out,
                                           guint *count_ge,
                                           double rank) {
    if (ranks_out) *ranks_out = NULL;
    if (count_ge) *count_ge = 0;
    if (ifo_id < 0 || ifo_id >= MAX_NIFO) {
        return 0;
    }

    g_mutex_lock(&crashcar_support_mutex);
    GArray *points = crashcar_support_array_locked(ifo_id);
    guint n = 0;
    for (guint i = 0; i < points->len; ++i) {
        CrashcarSupportPoint point = g_array_index(points, CrashcarSupportPoint, i);
        if (point.gps >= start && point.gps < end) ++n;
    }
    if (n == 0) {
        g_mutex_unlock(&crashcar_support_mutex);
        return 0;
    }
    double *ranks = g_new(double, n);
    guint j = 0;
    guint ge = 0;
    for (guint i = 0; i < points->len; ++i) {
        CrashcarSupportPoint point = g_array_index(points, CrashcarSupportPoint, i);
        if (point.gps < start || point.gps >= end) continue;
        ranks[j++] = point.rank;
        if (point.rank >= rank) ++ge;
    }
    if (ranks_out) *ranks_out = ranks;
    else g_free(ranks);
    if (count_ge) *count_ge = ge;
    g_mutex_unlock(&crashcar_support_mutex);
    return n;
}

static guint crashcar_window_total_support(const CrashcarSinglefar *element,
                                           double start,
                                           double end) {
    guint total = 0;
    g_mutex_lock(&crashcar_support_mutex);
    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        if (!ifo_set__contains(element->enabled_ifos, ifo_id)) {
            continue;
        }
        GArray *points = crashcar_support_array_locked(ifo_id);
        for (guint i = 0; i < points->len; ++i) {
            CrashcarSupportPoint point = g_array_index(points, CrashcarSupportPoint, i);
            if (point.gps >= start && point.gps < end) ++total;
        }
    }
    g_mutex_unlock(&crashcar_support_mutex);
    return total;
}

static double crashcar_window_ifo_livetime(const CrashcarSinglefar *element,
                                           int ifo_id,
                                           double start,
                                           double end) {
    if (!element || ifo_id < 0 || ifo_id >= MAX_NIFO || end <= start) {
        return 0.0;
    }
    if (!element->have_livetime_segments) {
        return end - start;
    }

    double livetime = 0.0;
    GArray *segments = element->livetime_segments[ifo_id];
    if (!segments) return 0.0;
    for (guint i = 0; i < segments->len; ++i) {
        CrashcarLivetimeSegment segment =
          g_array_index(segments, CrashcarLivetimeSegment, i);
        const double overlap_start = MAX(start, segment.start_gps);
        const double overlap_end = MIN(end, segment.end_gps);
        if (overlap_end > overlap_start) {
            livetime += overlap_end - overlap_start;
        }
    }
    return livetime;
}

static double crashcar_window_direct_far(const CrashcarSinglefar *element,
                                         guint count_ge,
                                         double livetime) {
    if (livetime <= 0.0) return INFINITY;
    double effective_count = MAX((double)count_ge, element->far_floor_count);
    return effective_count / livetime;
}

static guint crashcar_count_ge_from_rank_array(const GArray *ranks,
                                               double rank) {
    if (!ranks || !isfinite(rank)) return 0;
    guint count = 0;
    for (guint i = 0; i < ranks->len; ++i) {
        const double value = g_array_index((GArray *)ranks, double, i);
        if (value >= rank) ++count;
    }
    return count;
}

static guint crashcar_total_frozen_support(const CrashcarSinglefar *element) {
    if (!element) return 0;
    guint total = 0;
    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        if (!ifo_set__contains(element->enabled_ifos, ifo_id)) continue;
        if (element->frozen_ranks[ifo_id]) {
            total += element->frozen_ranks[ifo_id]->len;
        }
    }
    return total;
}

static void crashcar_add_foreground_support(CrashcarSinglefar *element,
                                            int ifo_id,
                                            double rank,
                                            double gps) {
    if (ifo_id < 0 || ifo_id >= MAX_NIFO || !isfinite(rank) || !isfinite(gps)) {
        return;
    }
    CrashcarSupportPoint point;
    point.rank = rank;
    point.gps = gps;

    g_mutex_lock(&crashcar_support_mutex);
    GArray *points = crashcar_support_array_locked(ifo_id);
    g_array_append_val(points, point);

    if (element->background_window_seconds <= 0.0) {
        g_mutex_unlock(&crashcar_support_mutex);
        return;
    }
    double keep_after = gps - 3.0 * element->background_window_seconds -
                        MAX(element->background_update_seconds, 0.0) - 60.0;
    guint write = 0;
    for (guint read = 0; read < points->len; ++read) {
        CrashcarSupportPoint old = g_array_index(points, CrashcarSupportPoint, read);
        if (old.gps >= keep_after) {
            if (write != read) {
                g_array_index(points, CrashcarSupportPoint, write) = old;
            }
            ++write;
        }
    }
    if (write < points->len) {
        g_array_set_size(points, write);
    }
    g_mutex_unlock(&crashcar_support_mutex);
}

static gboolean crashcar_env_truthy(const char *name) {
    const char *value = g_getenv(name);
    if (!value || !value[0]) return FALSE;
    return g_ascii_strcasecmp(value, "0") != 0 &&
           g_ascii_strcasecmp(value, "false") != 0 &&
           g_ascii_strcasecmp(value, "no") != 0;
}

static int crashcar_worker_id_from_env(void) {
    int worker = 0;
    const char *worker_env = g_getenv("SINGLE_WORKER_GROUP");
    if (!worker_env || !worker_env[0]) {
        worker_env = g_getenv("SLURM_ARRAY_TASK_ID");
    }
    if (worker_env && worker_env[0]) {
        char *end = NULL;
        long parsed = strtol(worker_env, &end, 10);
        if (end != worker_env && parsed >= 0 && parsed <= 999999) {
            worker = (int)parsed;
        }
    }
    return worker;
}

static FILE *crashcar_open_support_debug_locked(void) {
    if (crashcar_support_debug_file) return crashcar_support_debug_file;
    const char *configured = g_getenv("CRASHCAR_SUPPORT_DEBUG_FNAME");
    if ((!configured || !configured[0]) &&
        !crashcar_env_truthy("CRASHCAR_SUPPORT_DEBUG")) {
        return NULL;
    }

    char default_name[128];
    const char *fname = configured;
    if (!fname || !fname[0]) {
        int worker = crashcar_worker_id_from_env();
        g_snprintf(default_name, sizeof(default_name),
                   "crashcar_selected_support_worker%03d.csv", worker);
        fname = default_name;
    }

    crashcar_support_debug_file = fopen(fname, "a");
    if (!crashcar_support_debug_file) return NULL;
    return crashcar_support_debug_file;
}

static void crashcar_write_detector_support_debug(const PostcohInspiralTable *table,
                                                  int ifo_id,
                                                  double llr,
                                                  double feature_gps) {
    if (!table || ifo_id < 0 || ifo_id >= MAX_NIFO) return;
    g_mutex_lock(&crashcar_support_debug_file_mutex);
    FILE *file = crashcar_open_support_debug_locked();
    if (file) {
        if (!crashcar_support_debug_header_written) {
            fprintf(file,
                    "code_version,source,ifo_id,event_end_gps,bankid,tmplt_idx,cohsnr,llr,feature_gps\n");
            crashcar_support_debug_header_written = TRUE;
        }
        const double event_end_gps = crashcar_gps_to_seconds(&table->end_time);
        fprintf(file, "%s,candidate,%d,%.17g,%d,%d,%.9g,%.17g,%.17g\n",
                CRASHCAR_CODE_VERSION, ifo_id, event_end_gps,
                table->bankid, table->tmplt_idx, table->cohsnr,
                llr, feature_gps);
        fflush(file);
    }
    g_mutex_unlock(&crashcar_support_debug_file_mutex);
}

static void crashcar_write_selected_support_debug(const CrashcarClusterEvent *event,
                                                  int ifo_id) {
    if (!event || ifo_id < 0 || ifo_id >= MAX_NIFO ||
        !event->has_ifo[ifo_id]) return;
    g_mutex_lock(&crashcar_support_debug_file_mutex);
    FILE *file = crashcar_open_support_debug_locked();
    if (file) {
        if (!crashcar_support_debug_header_written) {
            fprintf(file,
                    "code_version,source,ifo_id,event_end_gps,bankid,tmplt_idx,cohsnr,llr,feature_gps\n");
            crashcar_support_debug_header_written = TRUE;
        }
        fprintf(file, "%s,selected,%d,%.17g,%d,%d,%.9g,%.17g,%.17g\n",
                CRASHCAR_CODE_VERSION, ifo_id, event->end_gps,
                event->bankid, event->tmplt_idx, event->cohsnr,
                event->llr[ifo_id], event->gps[ifo_id]);
        fflush(file);
    }
    g_mutex_unlock(&crashcar_support_debug_file_mutex);
}

static FILE *crashcar_open_cluster_debug_locked(void) {
    if (crashcar_cluster_debug_file) return crashcar_cluster_debug_file;
    const char *configured = g_getenv("CRASHCAR_CLUSTER_DEBUG_FNAME");
    if ((!configured || !configured[0]) &&
        !crashcar_env_truthy("CRASHCAR_CLUSTER_DEBUG")) {
        return NULL;
    }

    char default_name[128];
    const char *fname = configured;
    if (!fname || !fname[0]) {
        int worker = crashcar_worker_id_from_env();
        g_snprintf(default_name, sizeof(default_name),
                   "crashcar_cluster_debug_worker%03d.csv", worker);
        fname = default_name;
    }

    crashcar_cluster_debug_file = fopen(fname, "a");
    if (!crashcar_cluster_debug_file) return NULL;
    return crashcar_cluster_debug_file;
}

static double crashcar_event_end_gps_or_nan(const CrashcarClusterEvent *event) {
    return event ? event->end_gps : NAN;
}

static int crashcar_event_bankid_or_missing(const CrashcarClusterEvent *event) {
    return event ? event->bankid : -1;
}

static int crashcar_event_tmplt_idx_or_missing(const CrashcarClusterEvent *event) {
    return event ? event->tmplt_idx : -1;
}

static double crashcar_event_cohsnr_or_nan(const CrashcarClusterEvent *event) {
    return event ? event->cohsnr : NAN;
}

static void crashcar_write_cluster_debug(
  const char *action,
  const CrashcarBufferClusterState *state,
  guint events_len,
  gint peak_idx,
  const CrashcarClusterEvent *peak,
  const CrashcarClusterEvent *candidate_before,
  const CrashcarClusterEvent *candidate_after,
  const CrashcarClusterEvent *selected,
  double boundary_before,
  double old_boundary,
  double boundary_after) {
    g_mutex_lock(&crashcar_cluster_debug_file_mutex);
    FILE *file = crashcar_open_cluster_debug_locked();
    if (file) {
        if (!crashcar_cluster_debug_header_written) {
            fprintf(file,
                    "code_version,action,buf_timestamp,buf_duration,expected_buffers,num_current_buffers,have_latest_buffers,max_cluster_boundary,max_seen,boundary_before,old_boundary,boundary_after,events_len,peak_idx,peak_end_gps,peak_bankid,peak_tmplt_idx,peak_cohsnr,candidate_before_end_gps,candidate_before_bankid,candidate_before_tmplt_idx,candidate_before_cohsnr,candidate_after_end_gps,candidate_after_bankid,candidate_after_tmplt_idx,candidate_after_cohsnr,selected_end_gps,selected_bankid,selected_tmplt_idx,selected_cohsnr\n");
            crashcar_cluster_debug_header_written = TRUE;
        }
        fprintf(file,
                "%s,%s,%.17g,%.17g,%u,%u,%d,%.17g,%.17g,%.17g,%.17g,%.17g,%u,%d,%.17g,%d,%d,%.9g,%.17g,%d,%d,%.9g,%.17g,%d,%d,%.9g,%.17g,%d,%d,%.9g\n",
                CRASHCAR_CODE_VERSION,
                action ? action : "",
                state ? state->buf_timestamp : NAN,
                state ? state->duration : NAN,
                state ? state->expected_buffers : 0,
                state ? state->num_current_buffers : 0,
                state ? (int)state->have_latest_buffers : -1,
                state ? state->max_cluster_boundary : NAN,
                crashcar_cluster_max_seen,
                boundary_before,
                old_boundary,
                boundary_after,
                events_len,
                peak_idx,
                crashcar_event_end_gps_or_nan(peak),
                crashcar_event_bankid_or_missing(peak),
                crashcar_event_tmplt_idx_or_missing(peak),
                crashcar_event_cohsnr_or_nan(peak),
                crashcar_event_end_gps_or_nan(candidate_before),
                crashcar_event_bankid_or_missing(candidate_before),
                crashcar_event_tmplt_idx_or_missing(candidate_before),
                crashcar_event_cohsnr_or_nan(candidate_before),
                crashcar_event_end_gps_or_nan(candidate_after),
                crashcar_event_bankid_or_missing(candidate_after),
                crashcar_event_tmplt_idx_or_missing(candidate_after),
                crashcar_event_cohsnr_or_nan(candidate_after),
                crashcar_event_end_gps_or_nan(selected),
                crashcar_event_bankid_or_missing(selected),
                crashcar_event_tmplt_idx_or_missing(selected),
                crashcar_event_cohsnr_or_nan(selected));
        fflush(file);
    }
    g_mutex_unlock(&crashcar_cluster_debug_file_mutex);
}

static void crashcar_add_cluster_selected_support(CrashcarSinglefar *element,
                                                  const CrashcarClusterEvent *event) {
    if (!element || !event) return;
    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        if (!event->has_ifo[ifo_id]) continue;
        crashcar_write_selected_support_debug(event, ifo_id);
        crashcar_add_foreground_support(element, ifo_id,
                                        event->llr[ifo_id],
                                        event->gps[ifo_id]);
    }
}

static void crashcar_cluster_append_or_update_array(GArray *events,
                                                    const CrashcarClusterEvent *candidate) {
    if (!events || !candidate) return;
    for (guint i = 0; i < events->len; ++i) {
        CrashcarClusterEvent *event =
          &g_array_index(events, CrashcarClusterEvent, i);
        if (event->end_gps == candidate->end_gps &&
            event->bankid == candidate->bankid &&
            event->tmplt_idx == candidate->tmplt_idx) {
            for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
                if (!candidate->has_ifo[ifo_id]) continue;
                event->has_ifo[ifo_id] = TRUE;
                event->llr[ifo_id] = candidate->llr[ifo_id];
                event->gps[ifo_id] = candidate->gps[ifo_id];
            }
            if (crashcar_cluster_event_is_better(candidate, event)) {
                event->cohsnr = candidate->cohsnr;
            }
            return;
        }
    }
    g_array_append_val(events, *candidate);
}

static void crashcar_cluster_append_or_update_locked(const CrashcarClusterEvent *candidate) {
    crashcar_cluster_append_or_update_array(crashcar_cluster_events_locked(),
                                            candidate);
}


static void crashcar_buffer_events_add_row(GArray *events,
                                           const PostcohInspiralTable *table) {
    if (!events || !table) return;
    const double event_gps = crashcar_gps_to_seconds(&table->end_time);
    if (!isfinite(event_gps)) return;

    CrashcarClusterEvent candidate;
    memset(&candidate, 0, sizeof(candidate));
    candidate.end_gps = event_gps;
    candidate.cohsnr = table->cohsnr;
    candidate.bankid = table->bankid;
    candidate.tmplt_idx = table->tmplt_idx;

    crashcar_cluster_append_or_update_array(events, &candidate);
}

static void crashcar_buffer_events_add_detector(GArray *events,
                                                const PostcohInspiralTable *table,
                                                int ifo_id,
                                                double llr,
                                                double feature_gps) {
    if (!isfinite(feature_gps) || ifo_id < 0 || ifo_id >= MAX_NIFO) return;
    CrashcarClusterEvent candidate;
    memset(&candidate, 0, sizeof(candidate));
    candidate.end_gps = crashcar_gps_to_seconds(&table->end_time);
    if (!isfinite(candidate.end_gps)) {
        candidate.end_gps = feature_gps;
    }
    candidate.cohsnr = table->cohsnr;
    candidate.bankid = table->bankid;
    candidate.tmplt_idx = table->tmplt_idx;
    candidate.has_ifo[ifo_id] = TRUE;
    candidate.llr[ifo_id] = llr;
    candidate.gps[ifo_id] = feature_gps;

    crashcar_cluster_append_or_update_array(events, &candidate);
}

static double crashcar_gst_time_to_seconds(GstClockTime time) {
    if (!GST_CLOCK_TIME_IS_VALID(time)) return NAN;
    return (double)time / (double)GST_SECOND;
}

static guint crashcar_env_uint(const char *name, guint fallback) {
    const char *value = g_getenv(name);
    if (!value || !value[0]) return fallback;
    char *end = NULL;
    guint64 parsed = g_ascii_strtoull(value, &end, 10);
    if (end == value || parsed == 0 || parsed > G_MAXUINT) return fallback;
    return (guint)parsed;
}

static guint crashcar_expected_buffers_per_timestamp(void) {
    return crashcar_env_uint("CRASHCAR_EXPECTED_BUFFERS_PER_TIMESTAMP", 0);
}

static gint64 crashcar_seconds_to_ns(double seconds) {
    if (!isfinite(seconds)) return G_MININT64;
    return (gint64)llround(seconds * (double)GST_SECOND);
}

static double crashcar_ns_to_seconds(gint64 ns) {
    return (double)ns / (double)GST_SECOND;
}

static GHashTable *crashcar_cluster_timestamp_count_table_locked(void) {
    if (!crashcar_cluster_timestamp_counts) {
        crashcar_cluster_timestamp_counts =
          g_hash_table_new_full(g_int64_hash, g_int64_equal, g_free, NULL);
    }
    return crashcar_cluster_timestamp_counts;
}

static GHashTable *crashcar_cluster_completed_table_locked(void) {
    if (!crashcar_cluster_completed_timestamps) {
        crashcar_cluster_completed_timestamps =
          g_hash_table_new_full(g_int64_hash, g_int64_equal, g_free, NULL);
    }
    return crashcar_cluster_completed_timestamps;
}

static guint crashcar_cluster_increment_timestamp_count_locked(gint64 ts_ns) {
    GHashTable *counts = crashcar_cluster_timestamp_count_table_locked();
    gpointer current = g_hash_table_lookup(counts, &ts_ns);
    guint count = current ? GPOINTER_TO_UINT(current) : 0;
    ++count;
    gint64 *key = g_new(gint64, 1);
    *key = ts_ns;
    g_hash_table_replace(counts, key, GUINT_TO_POINTER(count));
    return count;
}

static gboolean crashcar_cluster_timestamp_completed_locked(gint64 ts_ns) {
    GHashTable *completed = crashcar_cluster_completed_table_locked();
    return g_hash_table_contains(completed, &ts_ns);
}

static void crashcar_cluster_mark_timestamp_completed_locked(gint64 ts_ns) {
    GHashTable *completed = crashcar_cluster_completed_table_locked();
    if (g_hash_table_contains(completed, &ts_ns)) return;
    gint64 *key = g_new(gint64, 1);
    *key = ts_ns;
    g_hash_table_add(completed, key);
}

static gboolean crashcar_cluster_advance_completed_watermark_locked(
  gint64 step_ns, double *watermark_end) {
    if (!watermark_end) return FALSE;
    *watermark_end = NAN;
    if (!crashcar_cluster_have_next_timestamp || step_ns <= 0) return FALSE;

    gboolean advanced = FALSE;
    gint64 latest_complete_end_ns = 0;
    while (crashcar_cluster_timestamp_completed_locked(
             crashcar_cluster_next_timestamp_ns)) {
        latest_complete_end_ns = crashcar_cluster_next_timestamp_ns + step_ns;
        crashcar_cluster_next_timestamp_ns += step_ns;
        advanced = TRUE;
    }
    if (advanced) {
        *watermark_end = crashcar_ns_to_seconds(latest_complete_end_ns);
    }
    return advanced;
}

static void crashcar_cluster_flush(CrashcarSinglefar *element);

static void crashcar_cluster_append_events_locked(GArray *new_events) {
    if (!new_events) return;
    for (guint i = 0; i < new_events->len; ++i) {
        CrashcarClusterEvent event =
          g_array_index(new_events, CrashcarClusterEvent, i);
        crashcar_cluster_append_or_update_locked(&event);
    }
}

static CrashcarBufferClusterState crashcar_cluster_begin_buffer(
  CrashcarSinglefar *element, GstBuffer *buf) {
    CrashcarBufferClusterState state;
    state.have_latest_buffers = FALSE;
    state.max_cluster_boundary = NAN;
    state.buf_timestamp = NAN;
    state.duration = NAN;
    state.expected_buffers = 0;
    state.num_current_buffers = 0;

    const double buf_timestamp = crashcar_gst_time_to_seconds(GST_BUFFER_PTS(buf));
    double duration = crashcar_gst_time_to_seconds(GST_BUFFER_DURATION(buf));
    if (!isfinite(duration) || duration < 0.0) {
        duration = 0.0;
    }
    const guint expected_buffers = crashcar_expected_buffers_per_timestamp();
    state.buf_timestamp = buf_timestamp;
    state.duration = duration;
    state.expected_buffers = expected_buffers;

    g_mutex_lock(&crashcar_cluster_mutex);
    if (isfinite(buf_timestamp)) {
        if (expected_buffers > 0) {
            const gint64 ts_ns = crashcar_seconds_to_ns(buf_timestamp);
            gint64 step_ns = crashcar_seconds_to_ns(duration);
            if (step_ns <= 0) step_ns = GST_SECOND;

            if (!crashcar_cluster_have_next_timestamp ||
                ts_ns < crashcar_cluster_next_timestamp_ns) {
                crashcar_cluster_next_timestamp_ns = ts_ns;
                crashcar_cluster_have_next_timestamp = TRUE;
            }

            state.num_current_buffers =
              crashcar_cluster_increment_timestamp_count_locked(ts_ns);
            if (state.num_current_buffers >= expected_buffers) {
                crashcar_cluster_mark_timestamp_completed_locked(ts_ns);
            }

            double watermark_end = NAN;
            if (crashcar_cluster_advance_completed_watermark_locked(
                  step_ns, &watermark_end)) {
                state.have_latest_buffers = TRUE;
                state.max_cluster_boundary = watermark_end;
            } else {
                state.have_latest_buffers = FALSE;
                state.max_cluster_boundary = NAN;
            }
        } else {
            if (!isfinite(crashcar_cluster_current_timestamp) ||
                buf_timestamp > crashcar_cluster_current_timestamp) {
                crashcar_cluster_current_timestamp = buf_timestamp;
                crashcar_cluster_num_current_buffers = 0;
            }
            if (fabs(buf_timestamp - crashcar_cluster_current_timestamp) < 1.0e-9) {
                ++crashcar_cluster_num_current_buffers;
            }
            state.num_current_buffers = crashcar_cluster_num_current_buffers;
            state.have_latest_buffers = FALSE;
            state.max_cluster_boundary = buf_timestamp;
        }
    } else {
        state.have_latest_buffers = FALSE;
    }

    if (expected_buffers == 0 && isfinite(state.max_cluster_boundary)) {
        crashcar_cluster_max_seen = state.max_cluster_boundary;
    }
    crashcar_write_cluster_debug(
      "begin", &state,
      crashcar_cluster_events ? crashcar_cluster_events->len : 0, -1,
      NULL,
      crashcar_cluster_have_candidate ? &crashcar_cluster_candidate : NULL,
      crashcar_cluster_have_candidate ? &crashcar_cluster_candidate : NULL,
      NULL, crashcar_cluster_boundary, NAN, crashcar_cluster_boundary);
    g_mutex_unlock(&crashcar_cluster_mutex);

    if (state.expected_buffers == 0 && !state.have_latest_buffers) {
        crashcar_cluster_flush(element);
    }
    return state;
}

static void crashcar_cluster_finish_buffer(CrashcarSinglefar *element,
                                           const CrashcarBufferClusterState *state,
                                           GArray *new_events) {
    const double cluster_window = CRASHCAR_CLUSTER_WINDOW_SECONDS;

    g_mutex_lock(&crashcar_cluster_mutex);
    const double boundary_before = crashcar_cluster_boundary;
    if (state && state->have_latest_buffers) {
        crashcar_cluster_append_events_locked(new_events);
    }
    if (crashcar_cluster_is_first_event &&
        crashcar_cluster_events && crashcar_cluster_events->len > 0 &&
        state && isfinite(state->max_cluster_boundary)) {
        crashcar_cluster_boundary = state->max_cluster_boundary + cluster_window;
        crashcar_cluster_is_first_event = FALSE;
    }
    if (state && isfinite(state->max_cluster_boundary)) {
        crashcar_cluster_max_seen = state->max_cluster_boundary;
    }
    crashcar_write_cluster_debug(
      "finish", state,
      crashcar_cluster_events ? crashcar_cluster_events->len : 0, -1,
      NULL,
      crashcar_cluster_have_candidate ? &crashcar_cluster_candidate : NULL,
      crashcar_cluster_have_candidate ? &crashcar_cluster_candidate : NULL,
      NULL, boundary_before, NAN, crashcar_cluster_boundary);
    g_mutex_unlock(&crashcar_cluster_mutex);

    if (state && state->have_latest_buffers) {
        crashcar_cluster_flush(element);
    } else {
        g_mutex_lock(&crashcar_cluster_mutex);
        crashcar_cluster_append_events_locked(new_events);
        g_mutex_unlock(&crashcar_cluster_mutex);
    }
}

static void crashcar_cluster_drop_events_leq_locked(GArray *events,
                                                    double boundary) {
    guint write = 0;
    for (guint read = 0; read < events->len; ++read) {
        CrashcarClusterEvent event =
          g_array_index(events, CrashcarClusterEvent, read);
        if (event.end_gps > boundary) {
            if (write != read) {
                g_array_index(events, CrashcarClusterEvent, write) = event;
            }
            ++write;
        }
    }
    if (write < events->len) {
        g_array_set_size(events, write);
    }
}

static void crashcar_cluster_flush(CrashcarSinglefar *element) {
    const double cluster_window = CRASHCAR_CLUSTER_WINDOW_SECONDS;
    g_mutex_lock(&crashcar_cluster_mutex);
    GArray *events = crashcar_cluster_events_locked();
    while (isfinite(crashcar_cluster_boundary) &&
           isfinite(crashcar_cluster_max_seen) &&
           crashcar_cluster_max_seen > crashcar_cluster_boundary) {
        const double boundary_before = crashcar_cluster_boundary;
        CrashcarClusterEvent candidate_before;
        memset(&candidate_before, 0, sizeof(candidate_before));
        const gboolean have_candidate_before =
          crashcar_cluster_have_candidate;
        if (have_candidate_before) {
            candidate_before = crashcar_cluster_candidate;
        }
        gint peak_idx = -1;
        CrashcarClusterEvent peak;
        memset(&peak, 0, sizeof(peak));
        for (guint i = 0; i < events->len; ++i) {
            CrashcarClusterEvent event =
              g_array_index(events, CrashcarClusterEvent, i);
            if (event.end_gps > crashcar_cluster_boundary) continue;
            if (peak_idx < 0 ||
                crashcar_cluster_event_is_better(&event, &peak)) {
                peak_idx = (gint)i;
                peak = event;
            }
        }

        const double old_boundary = crashcar_cluster_boundary;
        if (peak_idx >= 0) {
            if (!crashcar_cluster_have_candidate ||
                crashcar_cluster_event_is_better(&peak,
                                                 &crashcar_cluster_candidate)) {
                crashcar_cluster_candidate = peak;
                crashcar_cluster_have_candidate = TRUE;
                crashcar_cluster_drop_events_leq_locked(events, old_boundary);
                crashcar_cluster_boundary = peak.end_gps + cluster_window;
                crashcar_write_cluster_debug(
                  "flush_update_candidate", NULL, events->len, peak_idx,
                  &peak,
                  have_candidate_before ? &candidate_before : NULL,
                  &crashcar_cluster_candidate, NULL,
                  boundary_before, old_boundary, crashcar_cluster_boundary);
            } else {
                CrashcarClusterEvent selected = crashcar_cluster_candidate;
                crashcar_cluster_drop_events_leq_locked(events, old_boundary);
                crashcar_cluster_boundary += cluster_window;
                crashcar_add_cluster_selected_support(
                  element, &crashcar_cluster_candidate);
                memset(&crashcar_cluster_candidate, 0,
                       sizeof(crashcar_cluster_candidate));
                crashcar_cluster_have_candidate = FALSE;
                crashcar_write_cluster_debug(
                  "flush_emit_candidate", NULL, events->len, peak_idx,
                  &peak,
                  have_candidate_before ? &candidate_before : NULL,
                  NULL, &selected,
                  boundary_before, old_boundary, crashcar_cluster_boundary);
            }
        } else {
            crashcar_cluster_boundary += cluster_window;
            if (crashcar_cluster_have_candidate) {
                CrashcarClusterEvent selected = crashcar_cluster_candidate;
                crashcar_add_cluster_selected_support(
                  element, &crashcar_cluster_candidate);
                memset(&crashcar_cluster_candidate, 0,
                       sizeof(crashcar_cluster_candidate));
                crashcar_cluster_have_candidate = FALSE;
                crashcar_write_cluster_debug(
                  "flush_emit_no_peak", NULL, events->len, peak_idx,
                  NULL,
                  have_candidate_before ? &candidate_before : NULL,
                  NULL, &selected,
                  boundary_before, old_boundary, crashcar_cluster_boundary);
            } else {
                crashcar_write_cluster_debug(
                  "flush_advance_empty", NULL, events->len, peak_idx,
                  NULL, NULL, NULL, NULL,
                  boundary_before, old_boundary, crashcar_cluster_boundary);
            }
        }
    }
    g_mutex_unlock(&crashcar_cluster_mutex);
}

static gboolean crashcar_row_has_ifo(const CrashcarSinglefar *element,
                                     const PostcohInspiralTable *table,
                                     int ifo_id) {
    if (!ifo_set__contains(element->enabled_ifos, ifo_id)) return FALSE;
    if (table->ifos[0] == '\0') return FALSE;
    ifo_set_type row_ifos;
    if (!ifo_set__try_parse(table->ifos, &row_ifos)) return FALSE;
    return ifo_set__contains(row_ifos, ifo_id);
}

static void crashcar_single_output_mask_from_text(const char *text,
                                                  gboolean active[MAX_NIFO],
                                                  guint *active_count) {
    for (int i = 0; i < MAX_NIFO; ++i) active[i] = FALSE;
    *active_count = 0;
    if (!text || !text[0]) return;

    for (const char *p = text; *p; ++p) {
        int ifo_id = -1;
        switch (g_ascii_toupper(*p)) {
            case 'H': ifo_id = 0; break;
            case 'L': ifo_id = 1; break;
            case 'V': ifo_id = 2; break;
            case 'K': ifo_id = 3; break;
            default: break;
        }
        if (ifo_id >= 0 && ifo_id < MAX_NIFO && !active[ifo_id]) {
            active[ifo_id] = TRUE;
            (*active_count)++;
        }
    }
}

static void crashcar_single_output_policy_init(void) {
    if (crashcar_single_output_policy_initialized) return;
    crashcar_single_output_policy_initialized = TRUE;

    const char *mode = g_getenv("CRASHCAR_SINGLE_OUTPUT_MODE");
    if (!mode || !mode[0]) mode = g_getenv("SINGLE_OUTPUT_MODE");
    if (!mode || !mode[0]) mode = "single-only";
    crashcar_single_output_mode = g_ascii_strdown(mode, -1);
    for (gchar *p = crashcar_single_output_mode; p && *p; ++p) {
        if (*p == '_') *p = '-';
    }

    const char *schedule = g_getenv("CRASHCAR_SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE");
    if (!schedule || !schedule[0]) schedule = g_getenv("SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE");
    if (!schedule || !schedule[0]) schedule = g_getenv("SINGLE_OUTPUT_DETECTOR_SCHEDULE");
    gchar *schedule_from_file = NULL;
    if (!schedule || !schedule[0]) {
        const char *schedule_file =
          g_getenv("CRASHCAR_SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE_FILE");
        if (!schedule_file || !schedule_file[0]) {
            schedule_file = g_getenv("SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE_FILE");
        }
        if (schedule_file && schedule_file[0]) {
            gsize length = 0;
            if (g_file_get_contents(schedule_file, &schedule_from_file,
                                    &length, NULL)) {
                g_strstrip(schedule_from_file);
                if (schedule_from_file[0]) schedule = schedule_from_file;
            }
        }
    }
    if (!schedule || !schedule[0]) {
        g_free(schedule_from_file);
        return;
    }

    crashcar_single_output_windows =
      g_array_new(FALSE, FALSE, sizeof(CrashcarSingleOutputWindow));
    gchar *copy = g_strdup(schedule);
    for (gchar *p = copy; *p; ++p) {
        if (*p == ';') *p = ',';
    }
    gchar **items = g_strsplit(copy, ",", -1);
    for (guint i = 0; items && items[i]; ++i) {
        gchar *item = g_strstrip(items[i]);
        if (!item[0]) continue;
        gchar **parts = g_strsplit(item, ":", 3);
        if (!parts[0] || !parts[1] || !parts[2]) {
            g_strfreev(parts);
            continue;
        }
        gchar *endptr = NULL;
        double start = g_ascii_strtod(parts[0], &endptr);
        if (endptr == parts[0]) {
            g_strfreev(parts);
            continue;
        }
        endptr = NULL;
        double end = g_ascii_strtod(parts[1], &endptr);
        if (endptr == parts[1] || !(end > start)) {
            g_strfreev(parts);
            continue;
        }

        CrashcarSingleOutputWindow window;
        memset(&window, 0, sizeof(window));
        window.start_gps = start;
        window.end_gps = end;
        crashcar_single_output_mask_from_text(parts[2], window.active,
                                              &window.active_count);
        g_array_append_val(crashcar_single_output_windows, window);
        g_strfreev(parts);
    }
    g_strfreev(items);
    g_free(copy);
    g_free(schedule_from_file);
}

static gboolean crashcar_single_output_mode_is(const char *value) {
    crashcar_single_output_policy_init();
    return crashcar_single_output_mode &&
           g_strcmp0(crashcar_single_output_mode, value) == 0;
}

static guint crashcar_single_output_active_ifos(
    const CrashcarSinglefar *element,
    const PostcohInspiralTable *table,
    double feature_gps,
    gboolean active[MAX_NIFO]) {
    crashcar_single_output_policy_init();
    for (int i = 0; i < MAX_NIFO; ++i) active[i] = FALSE;

    if (isfinite(feature_gps) && crashcar_single_output_windows) {
        for (guint i = 0; i < crashcar_single_output_windows->len; ++i) {
            const CrashcarSingleOutputWindow *window =
              &g_array_index(crashcar_single_output_windows,
                             CrashcarSingleOutputWindow, i);
            if (feature_gps >= window->start_gps &&
                feature_gps < window->end_gps) {
                for (int j = 0; j < MAX_NIFO; ++j) active[j] = window->active[j];
                return window->active_count;
            }
        }
    }

    guint count = 0;
    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        if (!crashcar_row_has_ifo(element, table, ifo_id)) continue;
        if ((table->snglsnr[ifo_id] > 0.0f &&
             isfinite(table->snglsnr[ifo_id])) ||
            (table->chisq[ifo_id] > 0.0f &&
             isfinite(table->chisq[ifo_id]))) {
            active[ifo_id] = TRUE;
            count++;
        }
    }
    return count;
}

static gboolean crashcar_single_output_allows(
    const CrashcarSinglefar *element,
    const PostcohInspiralTable *table,
    int ifo_id,
    double feature_gps) {
    if (crashcar_single_output_mode_is("all") ||
        crashcar_single_output_mode_is("always") ||
        crashcar_single_output_mode_is("legacy")) {
        return TRUE;
    }
    if (crashcar_single_output_mode_is("none") ||
        crashcar_single_output_mode_is("never") ||
        crashcar_single_output_mode_is("off")) {
        return FALSE;
    }

    gboolean active[MAX_NIFO];
    guint active_count = crashcar_single_output_active_ifos(
      element, table, feature_gps, active);
    return active_count == 1 && ifo_id >= 0 && ifo_id < MAX_NIFO &&
           active[ifo_id];
}

static float crashcar_best_single_far(const PostcohInspiralTable *table,
                                      int ifo_id) {
    float best = 0.0f;
    const float candidates[] = { table->far_2h_sngl[ifo_id],
                                 table->far_1d_sngl[ifo_id],
                                 table->far_1w_sngl[ifo_id] };

    for (guint i = 0; i < G_N_ELEMENTS(candidates); ++i) {
        if (!crashcar_far_is_valid(candidates[i])) continue;
        if (!crashcar_far_is_valid(best) || candidates[i] < best) {
            best = candidates[i];
        }
    }
    return best;
}

static float crashcar_best_multi_far(const PostcohInspiralTable *table) {
    const float candidates[] = { table->far_1w, table->far_1d,
                                 table->far_2h };

    for (guint i = 0; i < G_N_ELEMENTS(candidates); ++i) {
        if (crashcar_far_is_valid(candidates[i])) return candidates[i];
    }
    return 0.0f;
}

static gboolean crashcar_hits_threshold(float far, double log10_far_threshold) {
    return crashcar_far_is_valid(far) &&
           log10((double)far) <= log10_far_threshold;
}

static const char *crashcar_ifo_name(int ifo_id) {
    switch (ifo_id) {
        case 0: return "H1";
        case 1: return "L1";
        case 2: return "V1";
        case 3: return "K1";
        default: return "X1";
    }
}

static gboolean crashcar_env_value_is_disabled(const char *value) {
    return value &&
           (g_ascii_strcasecmp(value, "0") == 0 ||
            g_ascii_strcasecmp(value, "false") == 0 ||
            g_ascii_strcasecmp(value, "off") == 0 ||
            g_ascii_strcasecmp(value, "none") == 0);
}

static gchar *crashcar_snr_series_output_dir(
    const CrashcarSinglefar *element) {
    const char *configured = g_getenv("CRASHCAR_SNR_SERIES_OUTPUT_DIR");
    /*
     * The preferred crashcar retention path is the normal finalsink
     * candidate/coinc XML stream.  Keep this older per-worker shard writer
     * opt-in only so it cannot silently create a second manifest or collide
     * with SPIIR_CANDIDATE_EVENT_DIR.
     */
    if (!configured || !configured[0]) return NULL;
    if (crashcar_env_value_is_disabled(configured)) return NULL;
    return g_strdup(configured);
}

static gboolean crashcar_snr_series_write_csv_enabled(void) {
    const char *configured = g_getenv("CRASHCAR_SNR_SERIES_WRITE_CSV");
    return configured && configured[0] &&
           !crashcar_env_value_is_disabled(configured);
}

static gchar *crashcar_snr_series_xml_shard_basename(void) {
    return g_strdup_printf("crashcar_single_candidate_events_worker%03d.xml",
                           crashcar_worker_id_from_env());
}

static void crashcar_write_xml_text(FILE *file, const char *text) {
    gchar *escaped = g_markup_escape_text(text ? text : "", -1);
    fputs(escaped, file);
    g_free(escaped);
}

static void crashcar_write_snr_series_xml_element(
    FILE *xml_file,
    const PostcohInspiralTable *table,
    int ifo_id,
    const COMPLEX8TimeSeries *series,
    const char *ifo,
    double llr,
    float far_sngl,
    float far_multi,
    gboolean hit_single,
    gboolean hit_multi) {
    const double epoch =
      (double)series->epoch.gpsSeconds +
      1.0e-9 * (double)series->epoch.gpsNanoSeconds;
    fprintf(xml_file,
            "\t<LIGO_LW Name=\"COMPLEX8TimeSeries\">\n"
            "\t\t<Time Type=\"GPS\" Name=\"epoch\">%.17g</Time>\n"
            "\t\t<Param Name=\"f0:param\" Type=\"real_8\" Unit=\"s^-1\">%.17g</Param>\n"
            "\t\t<Array Type=\"real_8\" Name=\"snr:array\" Unit=\"\">\n"
            "\t\t\t<Dim Name=\"Time\" Unit=\"s\" Start=\"0\" Scale=\"%.17g\">%u</Dim>\n"
            "\t\t\t<Dim Name=\"Time,Real,Imaginary\">3</Dim>\n"
            "\t\t\t<Stream Type=\"Local\" Delimiter=\" \">\n",
            epoch, (double)series->f0, (double)series->deltaT,
            series->data->length);
    for (UINT4 i = 0; i < series->data->length; ++i) {
        const COMPLEX8 sample = series->data->data[i];
        fprintf(xml_file, "\t\t\t\t%.17g %.9g %.9g \n",
                (double)i * series->deltaT,
                (double)crealf(sample), (double)cimagf(sample));
    }
    fprintf(xml_file,
            "\t\t\t</Stream>\n"
            "\t\t</Array>\n"
            "\t\t<Param Name=\"event_id:param\" Type=\"ilwd:char\">sngl_inspiral:event_id:%d</Param>\n"
            "\t\t<Param Name=\"instrument:param\" Type=\"lstring\">",
            ifo_id);
    crashcar_write_xml_text(xml_file, ifo);
    fprintf(xml_file,
            "</Param>\n"
            "\t\t<Param Name=\"crashcar_event_id:param\" Type=\"int_8s\">%ld</Param>\n"
            "\t\t<Param Name=\"bankid:param\" Type=\"int_4s\">%d</Param>\n"
            "\t\t<Param Name=\"tmplt_idx:param\" Type=\"int_4s\">%d</Param>\n"
            "\t\t<Param Name=\"llr:param\" Type=\"real_8\">%.17g</Param>\n"
            "\t\t<Param Name=\"far_sngl:param\" Type=\"real_8\">%.9g</Param>\n"
            "\t\t<Param Name=\"far_multi:param\" Type=\"real_8\">%.9g</Param>\n"
            "\t\t<Param Name=\"hit_single:param\" Type=\"int_4s\">%d</Param>\n"
            "\t\t<Param Name=\"hit_multi:param\" Type=\"int_4s\">%d</Param>\n"
            "\t\t<Param Name=\"code_version:param\" Type=\"lstring\">",
            table->event_id, table->bankid, table->tmplt_idx, llr,
            far_sngl, far_multi, hit_single ? 1 : 0, hit_multi ? 1 : 0);
    crashcar_write_xml_text(xml_file, CRASHCAR_CODE_VERSION);
    fprintf(xml_file, "</Param>\n"
                      "\t</LIGO_LW>\n");
}

static gboolean crashcar_append_snr_series_xml_shard(
    CrashcarSinglefar *element,
    const PostcohInspiralTable *table,
    int ifo_id,
    const COMPLEX8TimeSeries *series,
    const char *ifo,
    const char *xml_path,
    double llr,
    float far_sngl,
    float far_multi,
    gboolean hit_single,
    gboolean hit_multi) {
    FILE *xml_file = fopen(xml_path, "r+");
    if (!xml_file) {
        xml_file = fopen(xml_path, "w+");
    }
    if (!xml_file) {
        GST_WARNING_OBJECT(element, "failed to write crashcar SNR XML shard %s",
                           xml_path);
        return FALSE;
    }

    gboolean is_new = TRUE;
    if (fseek(xml_file, 0, SEEK_END) == 0) {
        long size = ftell(xml_file);
        long close_len = (long)strlen(CRASHCAR_SNR_SERIES_SHARD_CLOSE);
        if (size > close_len) {
            is_new = FALSE;
            fseek(xml_file, size - close_len, SEEK_SET);
        }
    }

    if (is_new) {
        fprintf(xml_file,
                "<?xml version='1.0' encoding='utf-8'?>\n"
                "<!DOCTYPE LIGO_LW SYSTEM \"http://ldas-sw.ligo.caltech.edu/doc/ligolwAPI/html/ligolw_dtd.txt\">\n"
                "<LIGO_LW>\n"
                "\t<LIGO_LW Name=\"crashcar_snr_series\">\n");
    }

    crashcar_write_snr_series_xml_element(xml_file, table, ifo_id, series, ifo,
                                          llr, far_sngl, far_multi,
                                          hit_single, hit_multi);
    fputs(CRASHCAR_SNR_SERIES_SHARD_CLOSE, xml_file);

    fclose(xml_file);
    return TRUE;
}

static void crashcar_write_snr_series_dump(
    CrashcarSinglefar *element,
    const PostcohInspiralTable *table,
    int ifo_id,
    double llr,
    double direct_far,
    double bg_livetime,
    double bg_start,
    double bg_end,
    double feature_gps,
    double assignment_gps,
    float far_sngl,
    double autocorr_power,
    double dof,
    gboolean hit_single,
    gboolean hit_multi) {
    COMPLEX8TimeSeries *series = table->snr_series_list[ifo_id];
    if (!series || !series->data || series->data->length == 0) return;

    gchar *out_dir = crashcar_snr_series_output_dir(element);
    if (!out_dir) return;

    const char *ifo = crashcar_ifo_name(ifo_id);
    const LIGOTimeGPS *detail_end_time = crashcar_detail_end_time(table, ifo_id);
    const float far_multi = crashcar_best_multi_far(table);
    const double log10_far_sngl =
      crashcar_far_is_valid(far_sngl) ? log10((double)far_sngl) : NAN;
    const double log10_far_multi =
      crashcar_far_is_valid(far_multi) ? log10((double)far_multi) : NAN;
    gchar *series_stem = g_strdup_printf(
      "event%ld_%s_%d_%09d_bank%d_tmpl%d_snr",
      table->event_id, ifo, detail_end_time->gpsSeconds,
      detail_end_time->gpsNanoSeconds, table->bankid, table->tmplt_idx);
    gchar *series_basename = g_strdup_printf("%s.csv", series_stem);
    gchar *xml_basename = crashcar_snr_series_xml_shard_basename();
    gchar *series_path = NULL;
    gchar *xml_path = g_build_filename(out_dir, xml_basename, NULL);
    gchar *manifest_path = g_build_filename(out_dir, "manifest.csv", NULL);
    const gboolean write_csv = crashcar_snr_series_write_csv_enabled();

    g_mutex_lock(&crashcar_snr_series_file_mutex);
    if (g_mkdir_with_parents(out_dir, 0775) != 0) {
        GST_WARNING_OBJECT(element, "failed to create crashcar SNR output dir %s",
                           out_dir);
        goto done_locked;
    }

    const double epoch =
      (double)series->epoch.gpsSeconds +
      1.0e-9 * (double)series->epoch.gpsNanoSeconds;
    if (write_csv) {
        series_path = g_build_filename(out_dir, series_basename, NULL);
        FILE *series_file = fopen(series_path, "w");
        if (!series_file) {
            GST_WARNING_OBJECT(element, "failed to write crashcar SNR series %s",
                               series_path);
        } else {
            fprintf(series_file,
                    "sample_index,gps,relative_time_s,real,imag,abs\n");
            for (UINT4 i = 0; i < series->data->length; ++i) {
                const COMPLEX8 sample = series->data->data[i];
                const double real = (double)crealf(sample);
                const double imag = (double)cimagf(sample);
                const double gps = epoch + (double)i * series->deltaT;
                fprintf(series_file,
                        "%u,%.17g,%.17g,%.9g,%.9g,%.9g\n",
                        i, gps, gps - feature_gps, real, imag,
                        hypot(real, imag));
            }
            fclose(series_file);
        }
    }

    crashcar_append_snr_series_xml_shard(element, table, ifo_id, series, ifo,
                                         xml_path, llr, far_sngl, far_multi,
                                         hit_single, hit_multi);

    FILE *manifest = fopen(manifest_path, "a");
    if (!manifest) {
        GST_WARNING_OBJECT(element, "failed to append crashcar SNR manifest %s",
                           manifest_path);
        goto done_locked;
    }
    if (fseek(manifest, 0, SEEK_END) == 0 && ftell(manifest) == 0) {
        fprintf(manifest,
                "archive_seq,filename,series_file,xml_file,candidate_xml_file,"
                "archive_kind,candidate_schema,retention_kind,reasons,branches,"
                "event_id,ifos,ifo,ifo_id,bankid,tmplt_idx,end_time,end_time_ns,"
                "snglsnr,chisq,llr,far_sngl,log10_far_sngl,far_multi,"
                "log10_far_multi,hit_single,hit_multi,direct_far,"
                "bg_livetime,bg_start,bg_end,feature_gps,assignment_gps,"
                "autocorr_power,dof,code_version\n");
    }
    const char *reasons =
      (hit_single && hit_multi) ? "single;multi" :
      (hit_single ? "single" : (hit_multi ? "multi" : "threshold_forced"));
    const char *branches =
      (hit_single && hit_multi) ? "single;multi" :
      (hit_single ? "single" : (hit_multi ? "multi" : "forced"));
    const char *ifos = ifo;
    fprintf(manifest,
            "%ld,%s,%s,%s,%s,candidate_event_xml,ligolw_single_snr,"
            "crashcar_single_threshold,%s,%s,%ld,%s,%s,%d,%d,%d,%d,%d,"
            "%.9g,%.9g,%.17g,%.9g,%.9g,%.9g,"
            "%.9g,%d,%d,%.9g,%.9g,%.17g,%.17g,%.17g,%.17g,%.9g,%.9g,"
            "%s\n",
            table->event_id, xml_basename, write_csv ? series_basename : "",
            xml_basename, xml_basename, reasons, branches, table->event_id,
            ifos, ifo, ifo_id, table->bankid, table->tmplt_idx,
            detail_end_time->gpsSeconds, detail_end_time->gpsNanoSeconds,
            table->snglsnr[ifo_id], table->chisq[ifo_id], llr, far_sngl,
            log10_far_sngl, far_multi, log10_far_multi, hit_single ? 1 : 0,
            hit_multi ? 1 : 0, direct_far, bg_livetime, bg_start, bg_end,
            feature_gps, assignment_gps, autocorr_power, dof,
            CRASHCAR_CODE_VERSION);
    fclose(manifest);

done_locked:
    g_mutex_unlock(&crashcar_snr_series_file_mutex);
    g_free(series_stem);
    g_free(series_basename);
    g_free(xml_basename);
    g_free(series_path);
    g_free(xml_path);
    g_free(manifest_path);
    g_free(out_dir);
}

static void crashcar_write_detail(CrashcarSinglefar *element,
                                  const PostcohInspiralTable *table,
                                  int ifo_id,
                                  double llr,
                                  double direct_far,
                                  guint direct_far_count_ge,
                                  double bg_livetime,
                                  double bg_start,
                                  double bg_end,
                                  guint window_count,
                                  guint total_window_count,
                                  double feature_gps,
                                  double assignment_gps,
                                  float far_sngl,
                                  double autocorr_power,
                                  double dof) {
    if (!crashcar_singlefar_open_detail(element)) return;

    const LIGOTimeGPS *detail_end_time = crashcar_detail_end_time(table, ifo_id);
    const double assignment_unix = (double)g_get_real_time() / 1000000.0;
    const float far_multi = crashcar_best_multi_far(table);

    g_mutex_lock(&crashcar_detail_file_mutex);
    fprintf(element->detail_output_file,
            "%ld,%d,%d,%d,%d,%d,%d,%.9g,%.9g,%.17g,%.9g,%u,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%d,%.17g,%.17g,%u,%u,%s,%.17g,%.17g,%.9f\n",
            table->event_id, table->bankid, table->tmplt_idx,
            detail_end_time->gpsSeconds, detail_end_time->gpsNanoSeconds,
            ifo_id, table->is_background, table->snglsnr[ifo_id],
            table->chisq[ifo_id], llr, direct_far, direct_far_count_ge,
            bg_livetime, table->cohsnr, table->cmbchisq,
            far_multi,
            table->far_1w_sngl[ifo_id], table->far_1d_sngl[ifo_id],
            table->far_2h_sngl[ifo_id], far_sngl, autocorr_power, dof,
            table->snr_series_list[ifo_id] != NULL ? 1 : 0,
            bg_start, bg_end, window_count, total_window_count,
            CRASHCAR_CODE_VERSION, feature_gps, assignment_gps,
            assignment_unix);
    fflush(element->detail_output_file);
    g_mutex_unlock(&crashcar_detail_file_mutex);
}

static GstFlowReturn crashcar_singlefar_transform_ip(GstBaseTransform *base,
                                                     GstBuffer *buf) {
    CrashcarSinglefar *element = CRASHCAR_SINGLEFAR(base);

    if (!element->enabled) return GST_FLOW_OK;

    GstMapInfo mapInfo;
    gst_buffer_map(buf, &mapInfo, GST_MAP_WRITE);
    PostcohInspiralTable *table_begin = (PostcohInspiralTable *)mapInfo.data;
    PostcohInspiralTable *table_end =
      (PostcohInspiralTable *)(mapInfo.data + mapInfo.size);
    const gboolean preserve_table_single_far =
      crashcar_env_truthy("CRASHCAR_PRESERVE_TABLE_SINGLE_FAR");
    const gboolean frozen_single_background_active =
      element->frozen_single_background && element->frozen_support_loaded;
    gboolean is_first_buffer_row = TRUE;

    if (!preserve_table_single_far && !element->frozen_single_background) {
        GArray *buffer_events =
          g_array_new(FALSE, FALSE, sizeof(CrashcarClusterEvent));

        for (PostcohInspiralTable *table = table_begin; table < table_end; ++table) {
            const gboolean is_heartbeat_row = is_first_buffer_row;
            is_first_buffer_row = FALSE;
            if (is_heartbeat_row) {
                continue;
            }
            if (table->is_background != FLAG_FOREGROUND) {
                continue;
            }

            crashcar_buffer_events_add_row(buffer_events, table);

            for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
                if (!crashcar_row_has_ifo(element, table, ifo_id)) continue;
                if (table->snglsnr[ifo_id] < element->min_snr) continue;
                if (!(table->chisq[ifo_id] > 0.0f) ||
                    !isfinite(table->chisq[ifo_id])) {
                    continue;
                }

                double autocorr_power = 1.0;
                double dof = 2.0;
                crashcar_lookup_template_shape(element, ifo_id, table->bankid,
                                               table->tmplt_idx,
                                               &autocorr_power, &dof);
                double llr = crashcar_single_detector_llr(
                  table->snglsnr[ifo_id], table->chisq[ifo_id],
                  autocorr_power, dof);

                const LIGOTimeGPS *detail_time = crashcar_detail_end_time(table, ifo_id);
                const double feature_gps = crashcar_gps_to_seconds(detail_time);
                crashcar_write_detector_support_debug(table, ifo_id, llr,
                                                      feature_gps);
                crashcar_buffer_events_add_detector(buffer_events, table,
                                                    ifo_id, llr, feature_gps);
            }
        }

        CrashcarBufferClusterState cluster_state =
          crashcar_cluster_begin_buffer(element, buf);
        crashcar_cluster_finish_buffer(element, &cluster_state, buffer_events);
        g_array_free(buffer_events, TRUE);
    }

    is_first_buffer_row = TRUE;
    for (PostcohInspiralTable *table = table_begin; table < table_end; ++table) {
        const gboolean is_heartbeat_row = is_first_buffer_row;
        is_first_buffer_row = FALSE;
        if (is_heartbeat_row) {
            continue;
        }
        if (table->is_background != FLAG_FOREGROUND) {
            continue;
        }

        for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
            if (!crashcar_row_has_ifo(element, table, ifo_id)) continue;
            if (!(table->chisq[ifo_id] > 0.0f) ||
                !isfinite(table->chisq[ifo_id])) {
                continue;
            }
            const gboolean single_component_eligible =
              table->snglsnr[ifo_id] >= element->min_snr;
            const gboolean write_all_snr_series_for_component =
              element->snr_series_log10_far_threshold >= 90.0;
            const float far_multi_for_component =
              crashcar_best_multi_far(table);
            const gboolean snr_series_hit_multi_for_component =
              crashcar_hits_threshold(
                far_multi_for_component,
                element->snr_series_log10_far_threshold);
            if (!single_component_eligible &&
                !write_all_snr_series_for_component &&
                !snr_series_hit_multi_for_component) {
                continue;
            }

            double autocorr_power = 1.0;
            double dof = 2.0;
            crashcar_lookup_template_shape(element, ifo_id, table->bankid,
                                           table->tmplt_idx,
                                           &autocorr_power, &dof);
            double llr = crashcar_single_detector_llr(
              table->snglsnr[ifo_id], table->chisq[ifo_id],
              autocorr_power, dof);

            const LIGOTimeGPS *detail_time = crashcar_detail_end_time(table, ifo_id);
            const double feature_gps = crashcar_gps_to_seconds(detail_time);
            double assignment_gps = feature_gps;
            if (!isfinite(assignment_gps)) {
                assignment_gps = crashcar_gps_to_seconds(&table->end_time);
            }
            double bg_end = crashcar_assignment_window_end(element, assignment_gps);
            double bg_start = bg_end - element->background_window_seconds;
            if (element->data_start_gps > 0.0 && bg_start < element->data_start_gps) {
                bg_start = element->data_start_gps;
            }
            double bg_span = bg_end - bg_start;
            double bg_livetime =
              crashcar_window_ifo_livetime(element, ifo_id, bg_start, bg_end);
            gboolean required_window_ready =
              element->data_start_gps <= 0.0 ||
              bg_end >= element->data_start_gps +
                        element->background_required_seconds - 1.0e-6;
            gboolean full_window_ready =
              isfinite(feature_gps) && isfinite(assignment_gps) &&
              element->background_window_seconds > 0.0 &&
              bg_span >= element->background_window_seconds - 1.0e-6 &&
              required_window_ready;

            const gboolean allow_single_output =
              crashcar_single_output_allows(element, table, ifo_id,
                                            feature_gps);
            guint direct_far_count_ge = 0;
            double *window_ranks = NULL;
            const double *fit_ranks = NULL;
            guint window_count = 0;
            guint total_window_count = 0;
            double direct_far = INFINITY;
            double fitted_far = NAN;
            gboolean has_fitted_far = FALSE;
            /*
             * Compute single-detector background/FAR diagnostics for every
             * eligible trigger once the accumulation window is ready.  The
             * single-output policy only controls whether this FAR is written
             * back to the zerolag table; simultaneous-detector periods still
             * need finite direct FAR values for BG support and plotting.
             */
            if (single_component_eligible && !preserve_table_single_far) {
                if (frozen_single_background_active) {
                    GArray *frozen_ranks = element->frozen_ranks[ifo_id];
                    bg_livetime = element->frozen_livetime[ifo_id];
                    window_count = frozen_ranks ? frozen_ranks->len : 0;
                    total_window_count = crashcar_total_frozen_support(element);
                    direct_far_count_ge =
                      crashcar_count_ge_from_rank_array(frozen_ranks, llr);
                    fit_ranks =
                      (frozen_ranks && frozen_ranks->len > 0) ?
                        (const double *)frozen_ranks->data : NULL;
                    if (element->data_start_gps > 0.0 &&
                        element->background_window_seconds > 0.0) {
                        bg_start = element->data_start_gps;
                        bg_end = bg_start + element->background_window_seconds;
                        bg_span = bg_end - bg_start;
                    }
                    required_window_ready = TRUE;
                    full_window_ready =
                      isfinite(feature_gps) && isfinite(assignment_gps) &&
                      bg_livetime > 0.0 && window_count > 0;
                } else if (full_window_ready && bg_livetime > 0.0) {
                    window_count = crashcar_collect_window_ranks(
                      element, ifo_id, bg_start, bg_end, &window_ranks,
                      &direct_far_count_ge, llr);
                    total_window_count = crashcar_window_total_support(element,
                                                                       bg_start,
                                                                       bg_end);
                    fit_ranks = window_ranks;
                }
            }
            if (single_component_eligible && full_window_ready &&
                bg_livetime > 0.0 && !preserve_table_single_far &&
                window_count > 0) {
                direct_far = crashcar_window_direct_far(element,
                                                        direct_far_count_ge,
                                                        bg_livetime);
                if (frozen_single_background_active &&
                    crashcar_frozen_fitted_far_from_points(element, ifo_id,
                                                           llr, &fitted_far)) {
                    has_fitted_far = TRUE;
                } else if (total_window_count > 30 && fit_ranks) {
                    has_fitted_far = crashcar_fitted_far_from_ranks(
                      fit_ranks, window_count, bg_livetime,
                      element->far_floor_count, llr, &fitted_far);
                }
            }

            float calculated_far_sngl = 0.0f;
            if (has_fitted_far && crashcar_far_double_is_valid(fitted_far)) {
                calculated_far_sngl = (float)fitted_far;
            } else if (crashcar_far_double_is_valid(direct_far)) {
                calculated_far_sngl = (float)direct_far;
            }
            float far_sngl = crashcar_best_single_far(table, ifo_id);
            if (single_component_eligible && full_window_ready &&
                allow_single_output &&
                !preserve_table_single_far) {
                if (crashcar_far_is_valid(calculated_far_sngl)) {
                    far_sngl = calculated_far_sngl;
                }
            }
            if (single_component_eligible && allow_single_output &&
                crashcar_far_is_valid(far_sngl)) {
                table->far_sngl[ifo_id] = far_sngl;
                table->far_1w_sngl[ifo_id] = far_sngl;
                table->far_1d_sngl[ifo_id] = far_sngl;
                table->far_2h_sngl[ifo_id] = far_sngl;
            } else if (single_component_eligible && !allow_single_output) {
                table->far_sngl[ifo_id] = 0.0f;
                table->far_1w_sngl[ifo_id] = 0.0f;
                table->far_1d_sngl[ifo_id] = 0.0f;
                table->far_2h_sngl[ifo_id] = 0.0f;
                far_sngl = 0.0f;
            }

            gboolean write_all_details =
              element->log10_far_threshold >= 90.0;
            gboolean write_all_snr_series =
              element->snr_series_log10_far_threshold >= 90.0 ||
              crashcar_env_truthy("CRASHCAR_SINGLE_SNR_SERIES_PRESELECT_ALL");
            float far_multi = crashcar_best_multi_far(table);
            gboolean hit_single_far =
              single_component_eligible &&
              crashcar_hits_threshold(far_sngl,
                                      element->log10_far_threshold);
            gboolean hit_multi_far =
              crashcar_hits_threshold(far_multi,
                                      element->log10_far_threshold);
            gboolean snr_series_hit_single_far =
              single_component_eligible &&
              crashcar_hits_threshold(
                crashcar_far_is_valid(far_sngl) ?
                  far_sngl : calculated_far_sngl,
                element->snr_series_log10_far_threshold);
            gboolean snr_series_hit_multi_far =
              crashcar_hits_threshold(
                far_multi, element->snr_series_log10_far_threshold);
            if (single_component_eligible &&
                (write_all_details || hit_single_far || hit_multi_far)) {
                crashcar_write_detail(element, table, ifo_id, llr, direct_far,
                                      direct_far_count_ge, bg_livetime,
                                      bg_start, bg_end, window_count,
                                      total_window_count, feature_gps,
                                      assignment_gps,
                                      far_sngl, autocorr_power, dof);
            }
            if (write_all_snr_series || snr_series_hit_single_far ||
                snr_series_hit_multi_far) {
                const float snr_series_far_sngl =
                  crashcar_far_is_valid(far_sngl) ?
                    far_sngl : calculated_far_sngl;
                crashcar_write_snr_series_dump(
                  element, table, ifo_id, llr, direct_far, bg_livetime,
                  bg_start, bg_end, feature_gps, assignment_gps,
                  snr_series_far_sngl, autocorr_power, dof,
                  snr_series_hit_single_far, snr_series_hit_multi_far);
            }
            g_free(window_ranks);
        }
    }

    if (element->detail_output_file) {
        g_mutex_lock(&crashcar_detail_file_mutex);
        fflush(element->detail_output_file);
        g_mutex_unlock(&crashcar_detail_file_mutex);
    }
    gst_buffer_unmap(buf, &mapInfo);
    return GST_FLOW_OK;
}

static void crashcar_singlefar_set_property(GObject *object,
                                            guint prop_id,
                                            const GValue *value,
                                            GParamSpec *pspec) {
    CrashcarSinglefar *element = CRASHCAR_SINGLEFAR(object);

    GST_OBJECT_LOCK(element);
    switch (prop_id) {
    case PROP_IFOS:
        g_free(element->ifos);
        element->ifos = g_value_dup_string(value);
        element->nifo = element->ifos ? strlen(element->ifos) / IFO_LEN : 0;
        element->enabled_ifos = ifo_set__parse_or_empty(element->ifos);
        break;
    case PROP_ENABLED:
        element->enabled = g_value_get_boolean(value);
        break;
    case PROP_DETAIL_OUTPUT_FNAME:
        g_free(element->detail_output_fname);
        element->detail_output_fname = g_value_dup_string(value);
        break;
    case PROP_TEMPLATE_SHAPE_MAP_FNAME:
        g_free(element->template_shape_map_fname);
        element->template_shape_map_fname = g_value_dup_string(value);
        if (element->template_shape_map) {
            g_hash_table_remove_all(element->template_shape_map);
        }
        element->template_shape_map_loaded = FALSE;
        break;
    case PROP_LOG10_FAR_THRESHOLD:
        element->log10_far_threshold = g_value_get_double(value);
        break;
    case PROP_MIN_SNR:
        element->min_snr = g_value_get_double(value);
        break;
    case PROP_FAR_FLOOR_COUNT:
        element->far_floor_count = g_value_get_double(value);
        break;
    case PROP_LIVETIME_STEP:
        element->livetime_step = g_value_get_double(value);
        break;
    default:
        G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
        break;
    }
    GST_OBJECT_UNLOCK(element);
}

static void crashcar_singlefar_get_property(GObject *object,
                                            guint prop_id,
                                            GValue *value,
                                            GParamSpec *pspec) {
    CrashcarSinglefar *element = CRASHCAR_SINGLEFAR(object);

    GST_OBJECT_LOCK(element);
    switch (prop_id) {
    case PROP_IFOS:
        g_value_set_string(value, element->ifos);
        break;
    case PROP_ENABLED:
        g_value_set_boolean(value, element->enabled);
        break;
    case PROP_DETAIL_OUTPUT_FNAME:
        g_value_set_string(value, element->detail_output_fname);
        break;
    case PROP_TEMPLATE_SHAPE_MAP_FNAME:
        g_value_set_string(value, element->template_shape_map_fname);
        break;
    case PROP_LOG10_FAR_THRESHOLD:
        g_value_set_double(value, element->log10_far_threshold);
        break;
    case PROP_MIN_SNR:
        g_value_set_double(value, element->min_snr);
        break;
    case PROP_FAR_FLOOR_COUNT:
        g_value_set_double(value, element->far_floor_count);
        break;
    case PROP_LIVETIME_STEP:
        g_value_set_double(value, element->livetime_step);
        break;
    default:
        G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
        break;
    }
    GST_OBJECT_UNLOCK(element);
}

static void crashcar_singlefar_dispose(GObject *object) {
    CrashcarSinglefar *element = CRASHCAR_SINGLEFAR(object);

    if (element->detail_output_file) {
        g_mutex_lock(&crashcar_detail_file_mutex);
        fflush(element->detail_output_file);
        fclose(element->detail_output_file);
        element->detail_output_file = NULL;
        g_mutex_unlock(&crashcar_detail_file_mutex);
    }
    g_free(element->ifos);
    element->ifos = NULL;
    g_free(element->template_shape_map_fname);
    element->template_shape_map_fname = NULL;
    if (element->template_shape_map) {
        g_hash_table_destroy(element->template_shape_map);
        element->template_shape_map = NULL;
    }
    g_free(element->detail_output_fname);
    element->detail_output_fname = NULL;
    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        if (element->ranks[ifo_id]) {
            g_array_free(element->ranks[ifo_id], TRUE);
            element->ranks[ifo_id] = NULL;
        }
        if (element->support_points[ifo_id]) {
            g_array_free(element->support_points[ifo_id], TRUE);
            element->support_points[ifo_id] = NULL;
        }
        if (element->livetime_segments[ifo_id]) {
            g_array_free(element->livetime_segments[ifo_id], TRUE);
            element->livetime_segments[ifo_id] = NULL;
        }
        if (element->frozen_ranks[ifo_id]) {
            g_array_free(element->frozen_ranks[ifo_id], TRUE);
            element->frozen_ranks[ifo_id] = NULL;
        }
        if (element->frozen_fit_ranks[ifo_id]) {
            g_array_free(element->frozen_fit_ranks[ifo_id], TRUE);
            element->frozen_fit_ranks[ifo_id] = NULL;
        }
        if (element->frozen_fit_log_fars[ifo_id]) {
            g_array_free(element->frozen_fit_log_fars[ifo_id], TRUE);
            element->frozen_fit_log_fars[ifo_id] = NULL;
        }
    }

    G_OBJECT_CLASS(crashcar_singlefar_parent_class)->dispose(object);
}

static void crashcar_singlefar_class_init(CrashcarSinglefarClass *klass) {
    GObjectClass *gobject_class = G_OBJECT_CLASS(klass);

    gobject_class->set_property =
      GST_DEBUG_FUNCPTR(crashcar_singlefar_set_property);
    gobject_class->get_property =
      GST_DEBUG_FUNCPTR(crashcar_singlefar_get_property);
    gobject_class->dispose = GST_DEBUG_FUNCPTR(crashcar_singlefar_dispose);

    g_object_class_install_property(
      gobject_class, PROP_IFOS,
      g_param_spec_string("ifos", "ifo names",
                          "ifos that participate in the pipeline", "H1L1",
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_ENABLED,
      g_param_spec_boolean("enabled", "enabled",
                           "enable crashcar single-detector processing", FALSE,
                           G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_DETAIL_OUTPUT_FNAME,
      g_param_spec_string("detail-output-fname", "detail output filename",
                          "CSV file for significant crashcar trigger details",
                          NULL, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_TEMPLATE_SHAPE_MAP_FNAME,
      g_param_spec_string("template-shape-map-fname",
                          "template shape map filename",
                          "CSV with ifo_id,bankid,tmplt_idx,autocorr_power,dof",
                          NULL, G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_LOG10_FAR_THRESHOLD,
      g_param_spec_double(
        "log10-far-threshold", "log10 FAR threshold",
        "write detailed rows when log10(FAR) is at or below this value",
        -G_MAXDOUBLE, G_MAXDOUBLE, -4.0,
        G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_MIN_SNR,
      g_param_spec_double("min-snr", "minimum single-detector SNR",
                          "ignore detector-local rows below this SNR", 0.0,
                          G_MAXDOUBLE, 4.0,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_FAR_FLOOR_COUNT,
      g_param_spec_double("far-floor-count", "FAR floor count",
                          "pseudo-count used to avoid zero direct FAR", 0.0,
                          G_MAXDOUBLE, 1.0,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    g_object_class_install_property(
      gobject_class, PROP_LIVETIME_STEP,
      g_param_spec_double("livetime-step", "livetime step",
                          "default livetime increment for FLAG_EMPTY rows",
                          0.0, G_MAXDOUBLE, 1.0,
                          G_PARAM_READWRITE | G_PARAM_STATIC_STRINGS));

    GstElementClass *gst_element_class = GST_ELEMENT_CLASS(klass);
    gst_element_class_set_metadata(
      gst_element_class, "crashcar single-detector FAR stream hook",
      "single-detector FAR", "low-latency single-detector FAR stream hook",
      "Eric Qingyuan Liang");

    GstCaps *template_caps = gst_caps_from_string("application/x-lal-postcoh");
    gst_element_class_add_pad_template(
      gst_element_class,
      gst_pad_template_new("sink", GST_PAD_SINK, GST_PAD_ALWAYS,
                           template_caps));
    gst_element_class_add_pad_template(
      gst_element_class,
      gst_pad_template_new("src", GST_PAD_SRC, GST_PAD_ALWAYS, template_caps));
    gst_caps_unref(template_caps);

    GstBaseTransformClass *transform_class = GST_BASE_TRANSFORM_CLASS(klass);
    transform_class->transform_ip =
      GST_DEBUG_FUNCPTR(crashcar_singlefar_transform_ip);
}

static void crashcar_singlefar_init(CrashcarSinglefar *element) {
    element->ifos = g_strdup("H1L1");
    element->nifo = strlen(element->ifos) / IFO_LEN;
    element->enabled_ifos = ifo_set__parse_or_empty(element->ifos);
    element->enabled = FALSE;
    element->log10_far_threshold = -4.0;
    element->snr_series_log10_far_threshold =
      crashcar_env_double("CRASHCAR_SNR_SERIES_LOG10_FAR_THRESHOLD", -4.0);
    element->min_snr = 4.0;
    element->far_floor_count = 1.0;
    element->livetime_step = 1.0;
    element->background_window_seconds =
      crashcar_env_double("BACKGROUND_ACCUMULATION_SECONDS", 10800.0);
    element->background_required_seconds =
      crashcar_env_double("CRASHCAR_BACKGROUND_REQUIRED_SECONDS",
                          element->background_window_seconds);
    element->background_update_seconds =
      crashcar_env_double("BACKGROUND_UPDATE_TRIGGER_SECONDS",
                          element->background_window_seconds);
    element->snapshot_interval_seconds =
      crashcar_env_double("CRASHCAR_SNAPSHOT_INTERVAL_SECONDS",
        crashcar_env_double("ZEROLAG_SNAPSHOT_INTERVAL_SECONDS",
          crashcar_env_double("FINALSINK_SNAPSHOT_INTERVAL_SECONDS", 0.0)));
    element->data_start_gps = crashcar_env_double("DATA_START_TIME", 0.0);
    element->have_livetime_segments = FALSE;
    element->frozen_single_background =
      crashcar_single_background_mode_is_frozen();
    element->frozen_support_loaded = FALSE;
    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        element->livetime[ifo_id] = 0.0;
        element->frozen_livetime[ifo_id] = 0.0;
        element->ranks[ifo_id] = g_array_new(FALSE, FALSE, sizeof(double));
        element->support_points[ifo_id] =
          g_array_new(FALSE, FALSE, sizeof(CrashcarSupportPoint));
        element->livetime_segments[ifo_id] =
          g_array_new(FALSE, FALSE, sizeof(CrashcarLivetimeSegment));
        element->frozen_ranks[ifo_id] =
          g_array_new(FALSE, FALSE, sizeof(double));
        element->frozen_fit_ranks[ifo_id] =
          g_array_new(FALSE, FALSE, sizeof(double));
        element->frozen_fit_log_fars[ifo_id] =
          g_array_new(FALSE, FALSE, sizeof(double));
    }
    crashcar_load_livetime_segments(element);
    crashcar_load_frozen_single_support(element);
    element->template_shape_map_fname = NULL;
    element->template_shape_map = NULL;
    element->template_shape_map_loaded = FALSE;
    element->detail_output_fname = NULL;
    element->detail_output_file = NULL;
    element->detail_output_header_written = FALSE;
}
