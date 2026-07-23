#ifndef _POSIX_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#endif

/*
 * Crashcar low-latency single-detector FAR engine.
 *
 * This file is an internal module of the unified cohfar_assignfar element.  It
 * owns the single-detector state and mutates the same Postcoh buffer only after
 * the unchanged multi/coherent FAR step.  It registers no GStreamer type and
 * owns no pads, so the graph contains one FAR element per Postcoh bank stream.
 */

#include <errno.h>
#include <fcntl.h>
#include <float.h>
#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

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

#define CRASHCAR_CODE_VERSION "r24_live_injection_bg_coverage"
/* Multiple per-bank engine instances can live in one worker process and append to the
 * same worker-level products.  Serialize each shared write without altering
 * the normal multi/coherent branch. */
static GMutex crashcar_detail_file_mutex;
static GMutex crashcar_support_mutex;
static GArray *crashcar_global_support_points[MAX_NIFO];

typedef struct {
    gint64 gps_ns;
    double llr;
    double far;
} CrashcarBackgroundPoint;

typedef struct {
    guint64 version;
    gint64 epoch_gps_ns;
    gint64 window_start_gps_ns;
    gint64 window_end_gps_ns;
    double tail_log10_far;
    gint64 livetime_ns[2];
    GArray *points[2];
    GArray *ranks[2];
    double r_tail[2];
    double tail_slope[2];
    guint fit_unique_rank_count[2];
    char file_sha256[CRASHCAR_SHA256_HEX_LENGTH + 1];
} CrashcarParsedBackground;

/*
 * One Slurm worker owns one process.  Every per-bank crashcar engine in that
 * process consumes the normal Postcoh delivery order and shares this single
 * paired H1/L1 authority.  This is scientific state only: it neither waits for
 * bank streams nor handles GAP/EOS, scheduling, buffers, or pipeline control.
 */
typedef struct {
    gboolean worker_bound;
    int worker_id;
    gboolean valid;
    guint64 version;
    gint64 epoch_gps_ns;
    gint64 window_start_gps_ns;
    gint64 window_end_gps_ns;
    double tail_log10_far;
    gint64 livetime_ns[2];
    GArray *points[2];
    GArray *ranks[2];
    double r_tail[2];
    double tail_slope[2];
    guint fit_unique_rank_count[2];
    char provenance_sha256[CRASHCAR_SHA256_HEX_LENGTH + 1];
    gint64 last_candidate_epoch_ns;
} CrashcarWorkerAuthority;

typedef struct {
    gboolean valid;
    gint64 available_after_gps_ns;
    CrashcarParsedBackground parsed;
} CrashcarPendingAuthority;

static CrashcarWorkerAuthority crashcar_worker_authority = {
  .worker_bound = FALSE,
  .worker_id = -1
};
static CrashcarPendingAuthority crashcar_pending_authority = {
  .valid = FALSE,
  .available_after_gps_ns = 0,
  .parsed = { 0 }
};


typedef struct {
    PostcohInspiralTable *table;
    gint64 row_assignment_gps_ns;
    gint64 row_bg_end_ns;
    gint64 row_bg_start_ns;
    guint64 row_bg_span_ns;
    gboolean required_window_ready;
    gsize original_ordinal;
    long event_id;
    CrashcarSingleFinalRoute final_route;
    gboolean append_future_support[MAX_NIFO];
    double future_support_llr[MAX_NIFO];
    gint64 future_support_gps_ns[MAX_NIFO];
} CrashcarRowWork;

typedef enum {
    CRASHCAR_AUTHORITY_SELECTION_NONE = 0,
    CRASHCAR_AUTHORITY_SELECTION_VALID = 1,
    CRASHCAR_AUTHORITY_SELECTION_INVALID = 2
} CrashcarAuthoritySelection;

static int crashcar_compare_row_work(const void *left_raw,
                                     const void *right_raw) {
    const CrashcarRowWork *left = (const CrashcarRowWork *)left_raw;
    const CrashcarRowWork *right = (const CrashcarRowWork *)right_raw;
    if (left->row_assignment_gps_ns < right->row_assignment_gps_ns) return -1;
    if (left->row_assignment_gps_ns > right->row_assignment_gps_ns) return 1;
    if (left->original_ordinal < right->original_ordinal) return -1;
    if (left->original_ordinal > right->original_ordinal) return 1;
    if (left->event_id < right->event_id) return -1;
    if (left->event_id > right->event_id) return 1;
    return 0;
}
static GArray *crashcar_support_array_locked(int ifo_id) {
    if (ifo_id < 0 || ifo_id >= MAX_NIFO) return NULL;
    if (!crashcar_global_support_points[ifo_id]) {
        crashcar_global_support_points[ifo_id] =
          g_array_new(FALSE, FALSE, sizeof(CrashcarSupportPoint));
    }
    return crashcar_global_support_points[ifo_id];
}

gboolean crashcar_singlefar_ifos_valid(const char *ifos) {
    return g_strcmp0(ifos, "H1L1") == 0;
}

static REAL8 *crashcar_singlefar_row_llr_slot(
  PostcohInspiralTable *table,
  int ifo_id) {
    if (!table) return NULL;
    if (ifo_id == 0) return &table->H1_LLR;
    if (ifo_id == 1) return &table->L1_LLR;
    return NULL;
}

void crashcar_singlefar_prepare_row_llrs(PostcohInspiralTable *table) {
    if (!table) return;
    table->H1_LLR = 0.0;
    table->L1_LLR = 0.0;
}

guint crashcar_singlefar_support_count(int ifo_id) {
    if (ifo_id < 0 || ifo_id >= MAX_NIFO) return 0;
    g_mutex_lock(&crashcar_support_mutex);
    GArray *points = crashcar_support_array_locked(ifo_id);
    const guint count = points ? points->len : 0;
    g_mutex_unlock(&crashcar_support_mutex);
    return count;
}

static const char *crashcar_single_background_mode(void) {
    const char *mode = g_getenv("CRASHCAR_SINGLE_BACKGROUND_MODE");
    if (!mode || !mode[0]) mode = g_getenv("SINGLE_BACKGROUND_MODE");
    return mode && mode[0] ? mode : "rolling";
}

static gboolean crashcar_single_background_mode_is_live_readonly(void) {
    return g_ascii_strcasecmp(
      crashcar_single_background_mode(), "live_readonly") == 0;
}

static gboolean
crashcar_single_background_mode_is_live_injection_consumer(void) {
    const char *injection_mode = g_getenv("WGUO_O3A_INJECTION_MODE");
    const char *role = g_getenv("CRASHCAR_INTERNAL_LIVE_BACKGROUND_ROLE");
    return crashcar_single_background_mode_is_live_readonly() &&
      injection_mode && g_ascii_strcasecmp(injection_mode, "blind") == 0 &&
      role && g_ascii_strcasecmp(role, "consumer") == 0;
}

/*
 * A continuing no-injection producer can be ahead of the injection
 * foreground GPS.  Only the validated live-readonly injection consumer may
 * use that complete worker-local snapshot before its coverage endpoint.
 * Rolling no-injection authority keeps the strict event-time causal rule.
 */
static gboolean crashcar_live_coverage_is_eligible(
  const CrashcarSingleFarEngine *element,
  gint64 coverage_gps_ns,
  gint64 event_gps_ns) {
    if (!element || !element->live_single_background_readonly ||
        coverage_gps_ns <= 0 || event_gps_ns <= 0) {
        return FALSE;
    }
    return coverage_gps_ns <= event_gps_ns ||
      crashcar_single_background_mode_is_live_injection_consumer();
}

static gboolean crashcar_single_background_mode_is_valid(void) {
    return g_ascii_strcasecmp(
             crashcar_single_background_mode(), "rolling") == 0 ||
      crashcar_single_background_mode_is_live_readonly();
}

static gboolean crashcar_single_background_mode_is_bg_only(void) {
    const char *value = g_getenv("CRASHCAR_BG_ONLY");
    return value && value[0] &&
      g_ascii_strcasecmp(value, "0") != 0 &&
      g_ascii_strcasecmp(value, "false") != 0 &&
      g_ascii_strcasecmp(value, "no") != 0;
}

#define CRASHCAR_SEGMENT_JSON_SCHEMA_VERSION 1
#define CRASHCAR_SEGMENT_JSON_MAX_BYTES (16u * 1024u * 1024u)
#define CRASHCAR_BACKGROUND_JSON_SCHEMA_VERSION 4
#define CRASHCAR_BACKGROUND_JSON_MAX_BYTES (256u * 1024u * 1024u)
#define CRASHCAR_BACKGROUND_MAX_POINTS_PER_IFO 1000000u
#define CRASHCAR_BACKGROUND_MAX_POINTS_TOTAL 2000000u
#define CRASHCAR_NS_PER_SECOND G_GINT64_CONSTANT(1000000000)

typedef struct {
    const char *cursor;
    const char *end;
    gchar *failure;
} CrashcarJsonCursor;

static gboolean crashcar_set_failure(gchar **failure, const char *message) {
    if (failure && !*failure) *failure = g_strdup(message);
    return FALSE;
}

static gboolean crashcar_json_fail(CrashcarJsonCursor *input,
                                   const char *message) {
    if (input && !input->failure) input->failure = g_strdup(message);
    return FALSE;
}

static gboolean crashcar_json_expect(CrashcarJsonCursor *input,
                                     const char *literal) {
    if (!input || !literal) return FALSE;
    const size_t length = strlen(literal);
    if ((size_t)(input->end - input->cursor) < length ||
        memcmp(input->cursor, literal, length) != 0) {
        return crashcar_json_fail(input, "noncanonical JSON token or key order");
    }
    input->cursor += length;
    return TRUE;
}

static gboolean crashcar_parse_canonical_int64_cursor(
  CrashcarJsonCursor *input,
  gint64 *value_out) {
    if (!input || !value_out || input->cursor >= input->end) {
        return crashcar_json_fail(input, "missing canonical integer");
    }
    const char *cursor = input->cursor;
    gboolean negative = FALSE;
    if (*cursor == '-') {
        negative = TRUE;
        ++cursor;
        if (cursor >= input->end) {
            return crashcar_json_fail(input, "truncated canonical integer");
        }
    }
    if (*cursor < '0' || *cursor > '9') {
        return crashcar_json_fail(input, "canonical integer requires digits");
    }
    if (*cursor == '0' &&
        cursor + 1 < input->end &&
        cursor[1] >= '0' && cursor[1] <= '9') {
        return crashcar_json_fail(input, "canonical integer has leading zero");
    }

    const guint64 limit =
      negative ? ((guint64)G_MAXINT64 + G_GUINT64_CONSTANT(1))
               : (guint64)G_MAXINT64;
    guint64 magnitude = 0;
    const char *digit = cursor;
    while (digit < input->end && *digit >= '0' && *digit <= '9') {
        const guint next = (guint)(*digit - '0');
        if (magnitude > (limit - next) / G_GUINT64_CONSTANT(10)) {
            return crashcar_json_fail(input, "canonical integer overflows int64");
        }
        magnitude = magnitude * G_GUINT64_CONSTANT(10) + next;
        ++digit;
    }
    if (negative && magnitude == 0) {
        return crashcar_json_fail(input, "canonical integer forbids negative zero");
    }
    if (negative) {
        *value_out =
          magnitude == ((guint64)G_MAXINT64 + G_GUINT64_CONSTANT(1))
            ? G_MININT64
            : -(gint64)magnitude;
    } else {
        *value_out = (gint64)magnitude;
    }
    input->cursor = digit;
    return TRUE;
}

static gboolean crashcar_parse_canonical_nonnegative_int64(
  CrashcarJsonCursor *input,
  gint64 *value_out) {
    gint64 value = 0;
    if (!crashcar_parse_canonical_int64_cursor(input, &value)) return FALSE;
    if (value < 0) {
        return crashcar_json_fail(input, "expected nonnegative canonical integer");
    }
    *value_out = value;
    return TRUE;
}

static gboolean crashcar_sha256_is_lowercase64(const char *value) {
    if (!value || strlen(value) != CRASHCAR_SHA256_HEX_LENGTH) return FALSE;
    for (guint i = 0; i < CRASHCAR_SHA256_HEX_LENGTH; ++i) {
        if (!((value[i] >= '0' && value[i] <= '9') ||
              (value[i] >= 'a' && value[i] <= 'f'))) {
            return FALSE;
        }
    }
    return TRUE;
}

static gboolean crashcar_json_parse_sha256(CrashcarJsonCursor *input,
                                           char value_out[
                                             CRASHCAR_SHA256_HEX_LENGTH + 1]) {
    if (!crashcar_json_expect(input, "\"")) return FALSE;
    if ((size_t)(input->end - input->cursor) <
        CRASHCAR_SHA256_HEX_LENGTH + 1) {
        return crashcar_json_fail(input, "truncated sha256 JSON string");
    }
    memcpy(value_out, input->cursor, CRASHCAR_SHA256_HEX_LENGTH);
    value_out[CRASHCAR_SHA256_HEX_LENGTH] = '\0';
    input->cursor += CRASHCAR_SHA256_HEX_LENGTH;
    if (!crashcar_sha256_is_lowercase64(value_out) ||
        !crashcar_json_expect(input, "\"")) {
        return crashcar_json_fail(input, "sha256 must be exact lowercase64");
    }
    return TRUE;
}

static gboolean crashcar_checked_gps_ns(gint64 seconds,
                                        gint64 nanoseconds,
                                        gint64 *gps_ns_out) {
    if (!gps_ns_out || nanoseconds < 0 ||
        nanoseconds >= CRASHCAR_NS_PER_SECOND) {
        return FALSE;
    }
    const gint64 min_whole_seconds =
      G_MININT64 / CRASHCAR_NS_PER_SECOND;
    const gint64 min_boundary_seconds = min_whole_seconds - 1;
    const gint64 min_boundary_nanoseconds =
      CRASHCAR_NS_PER_SECOND -
      (min_whole_seconds * CRASHCAR_NS_PER_SECOND - G_MININT64);
    const gint64 max_seconds = G_MAXINT64 / CRASHCAR_NS_PER_SECOND;
    const gint64 max_nanoseconds =
      G_MAXINT64 - max_seconds * CRASHCAR_NS_PER_SECOND;
    if (seconds < min_boundary_seconds || seconds > max_seconds ||
        (seconds == min_boundary_seconds &&
         nanoseconds < min_boundary_nanoseconds) ||
        (seconds == max_seconds && nanoseconds > max_nanoseconds)) {
        return FALSE;
    }
    if (seconds == min_boundary_seconds) {
        *gps_ns_out =
          G_MININT64 + (nanoseconds - min_boundary_nanoseconds);
    } else {
        *gps_ns_out = seconds * CRASHCAR_NS_PER_SECOND + nanoseconds;
    }
    return TRUE;
}

static gboolean crashcar_ordered_distance_u64(gint64 start,
                                              gint64 end,
                                              guint64 *distance_out) {
    if (!distance_out || end < start) return FALSE;
    *distance_out = (guint64)end - (guint64)start;
    return TRUE;
}

static gboolean crashcar_add_nonnegative_offset(
  gint64 base,
  guint64 offset,
  gint64 *result_out) {
    if (!result_out) return FALSE;
    if (base >= 0) {
        if (offset > (guint64)(G_MAXINT64 - base)) return FALSE;
        *result_out = base + (gint64)offset;
        return TRUE;
    }
    const guint64 distance_to_zero =
      (guint64)(-(base + 1)) + G_GUINT64_CONSTANT(1);
    if (offset < distance_to_zero) {
        *result_out = base + (gint64)offset;
        return TRUE;
    }
    const guint64 positive = offset - distance_to_zero;
    if (positive > (guint64)G_MAXINT64) return FALSE;
    *result_out = (gint64)positive;
    return TRUE;
}

static gboolean crashcar_subtract_nonnegative(
  gint64 value,
  gint64 amount,
  gint64 *result_out) {
    if (!result_out || amount < 0 || value < G_MININT64 + amount) {
        return FALSE;
    }
    *result_out = value - amount;
    return TRUE;
}

static gboolean crashcar_json_parse_gps_ns(CrashcarJsonCursor *input,
                                           gint64 *gps_ns_out) {
    gint64 seconds = 0;
    gint64 nanoseconds = 0;
    if (!crashcar_json_expect(input, "{\"seconds\":") ||
        !crashcar_parse_canonical_int64_cursor(input, &seconds) ||
        !crashcar_json_expect(input, ",\"nanoseconds\":") ||
        !crashcar_parse_canonical_nonnegative_int64(input, &nanoseconds) ||
        !crashcar_json_expect(input, "}")) {
        return FALSE;
    }
    if (!crashcar_checked_gps_ns(seconds, nanoseconds, gps_ns_out)) {
        return crashcar_json_fail(input, "GPS object overflows signed int64 ns");
    }
    return TRUE;
}

static gboolean crashcar_json_parse_segment_def_id(
  CrashcarJsonCursor *input) {
    gint64 identifier = 0;
    if (!crashcar_json_expect(
          input, "\"segment_definer:segment_def_id:") ||
        !crashcar_parse_canonical_nonnegative_int64(input, &identifier) ||
        !crashcar_json_expect(input, "\"")) {
        return FALSE;
    }
    (void)identifier;
    return TRUE;
}

static gboolean crashcar_json_parse_interval(
  CrashcarJsonCursor *input,
  gint64 run_start_ns,
  gint64 run_end_ns,
  gint64 *previous_end_ns,
  gint64 *livetime_sum_ns,
  GArray *segments) {
    gint64 start_ns = 0;
    gint64 end_ns = 0;
    if (!crashcar_json_expect(input, "{\"start\":") ||
        !crashcar_json_parse_gps_ns(input, &start_ns) ||
        !crashcar_json_expect(input, ",\"end\":") ||
        !crashcar_json_parse_gps_ns(input, &end_ns) ||
        !crashcar_json_expect(input, "}")) {
        return FALSE;
    }
    if (start_ns < run_start_ns || end_ns > run_end_ns ||
        start_ns >= end_ns) {
        return crashcar_json_fail(input, "segment interval is outside run bounds");
    }
    if (*previous_end_ns != G_MININT64 && start_ns <= *previous_end_ns) {
        return crashcar_json_fail(
          input, "segment intervals are not strictly ordered and merged");
    }
    guint64 duration_u64 = 0;
    if (!crashcar_ordered_distance_u64(
          start_ns, end_ns, &duration_u64) ||
        duration_u64 > (guint64)G_MAXINT64) {
        return crashcar_json_fail(input, "segment duration overflows int64");
    }
    const gint64 duration_ns = (gint64)duration_u64;
    if (*livetime_sum_ns > G_MAXINT64 - duration_ns) {
        return crashcar_json_fail(input, "segment livetime overflows int64");
    }
    CrashcarLivetimeSegment segment = { start_ns, end_ns };
    g_array_append_val(segments, segment);
    *livetime_sum_ns += duration_ns;
    *previous_end_ns = end_ns;
    return TRUE;
}

static gboolean crashcar_json_parse_target(
  CrashcarJsonCursor *input,
  gint64 run_start_ns,
  gint64 run_end_ns,
  GArray *segments,
  gint64 *declared_livetime_ns_out) {
    gint64 raw_row_count = 0;
    gint64 empty_row_count = 0;
    gint64 merged_interval_count = 0;
    gint64 declared_livetime_ns = 0;
    if (!crashcar_json_expect(input, "{\"segment_def_id\":") ||
        !crashcar_json_parse_segment_def_id(input) ||
        !crashcar_json_expect(input, ",\"raw_row_count\":") ||
        !crashcar_parse_canonical_nonnegative_int64(input, &raw_row_count) ||
        !crashcar_json_expect(input, ",\"empty_row_count\":") ||
        !crashcar_parse_canonical_nonnegative_int64(input, &empty_row_count) ||
        !crashcar_json_expect(input, ",\"merged_interval_count\":") ||
        !crashcar_parse_canonical_nonnegative_int64(
          input, &merged_interval_count) ||
        !crashcar_json_expect(input, ",\"livetime_ns\":") ||
        !crashcar_parse_canonical_nonnegative_int64(
          input, &declared_livetime_ns) ||
        !crashcar_json_expect(input, ",\"intervals\":[")) {
        return FALSE;
    }
    guint64 run_span_ns = 0;
    if (!crashcar_ordered_distance_u64(
          run_start_ns, run_end_ns, &run_span_ns) ||
        raw_row_count < empty_row_count ||
        merged_interval_count > raw_row_count - empty_row_count ||
        merged_interval_count > G_MAXUINT ||
        ((merged_interval_count == 0) !=
         (declared_livetime_ns == 0)) ||
        (guint64)declared_livetime_ns > run_span_ns) {
        return crashcar_json_fail(input, "target count or livetime is invalid");
    }

    gint64 previous_end_ns = G_MININT64;
    gint64 computed_livetime_ns = 0;
    for (gint64 index = 0; index < merged_interval_count; ++index) {
        if (index > 0 && !crashcar_json_expect(input, ",")) return FALSE;
        if (!crashcar_json_parse_interval(
              input, run_start_ns, run_end_ns, &previous_end_ns,
              &computed_livetime_ns, segments)) {
            return FALSE;
        }
    }
    if (!crashcar_json_expect(input, "]}")) return FALSE;
    if ((gint64)segments->len != merged_interval_count ||
        computed_livetime_ns != declared_livetime_ns) {
        return crashcar_json_fail(
          input, "target interval count or livetime sum mismatch");
    }
    *declared_livetime_ns_out = declared_livetime_ns;
    return TRUE;
}

static gboolean crashcar_parse_env_gps_seconds(const char *name,
                                               gint64 *gps_ns_out,
                                               gchar **failure) {
    const char *value = g_getenv(name);
    if (!value || !value[0]) {
        return crashcar_set_failure(failure, "missing segment run frontier");
    }
    CrashcarJsonCursor input = { value, value + strlen(value), NULL };
    gint64 seconds = 0;
    if (!crashcar_parse_canonical_int64_cursor(&input, &seconds) ||
        input.cursor != input.end ||
        !crashcar_checked_gps_ns(seconds, 0, gps_ns_out)) {
        g_free(input.failure);
        return crashcar_set_failure(
          failure,
          "segment run frontier is not canonical signed-int64 GPS ns");
    }
    g_free(input.failure);
    return TRUE;
}

static gboolean crashcar_parse_env_worker_id(int *worker_id_out,
                                             gchar **failure) {
    const char *value = g_getenv("CRASHCAR_WORKER_ID");
    if (!value || !value[0]) {
        return crashcar_set_failure(failure, "missing CRASHCAR_WORKER_ID");
    }
    CrashcarJsonCursor input = { value, value + strlen(value), NULL };
    gint64 worker_id = 0;
    if (!crashcar_parse_canonical_nonnegative_int64(&input, &worker_id) ||
        input.cursor != input.end || worker_id > INT_MAX) {
        g_free(input.failure);
        return crashcar_set_failure(
          failure, "CRASHCAR_WORKER_ID is not a canonical nonnegative int");
    }
    g_free(input.failure);
    *worker_id_out = (int)worker_id;
    return TRUE;
}

static gboolean crashcar_parse_duration_ns(const char *text,
                                           gint64 *duration_ns_out) {
    if (!text || !text[0] || !duration_ns_out) return FALSE;
    CrashcarJsonCursor input = { text, text + strlen(text), NULL };
    gint64 seconds = 0;
    if (!crashcar_parse_canonical_nonnegative_int64(&input, &seconds)) {
        g_free(input.failure);
        return FALSE;
    }

    gint64 nanoseconds = 0;
    if (input.cursor < input.end) {
        if (*input.cursor != '.') {
            g_free(input.failure);
            return FALSE;
        }
        ++input.cursor;
        const char *fraction_begin = input.cursor;
        guint digits = 0;
        while (input.cursor < input.end &&
               *input.cursor >= '0' && *input.cursor <= '9') {
            if (digits >= 9) {
                g_free(input.failure);
                return FALSE;
            }
            nanoseconds =
              nanoseconds * 10 + (gint64)(*input.cursor - '0');
            ++input.cursor;
            ++digits;
        }
        if (input.cursor != input.end || input.cursor == fraction_begin) {
            g_free(input.failure);
            return FALSE;
        }
        while (digits < 9) {
            nanoseconds *= 10;
            ++digits;
        }
    }
    g_free(input.failure);
    return crashcar_checked_gps_ns(
      seconds, nanoseconds, duration_ns_out);
}

static gboolean crashcar_load_exact_window_config(
  CrashcarSingleFarEngine *element,
  gchar **failure) {
    const char *background =
      g_getenv("BACKGROUND_ACCUMULATION_SECONDS");
    const char *required =
      g_getenv("CRASHCAR_BACKGROUND_REQUIRED_SECONDS");
    const char *update =
      g_getenv("BACKGROUND_UPDATE_TRIGGER_SECONDS");
    const char *snapshot =
      g_getenv("CRASHCAR_SNAPSHOT_INTERVAL_SECONDS");
    if (!snapshot || !snapshot[0]) {
        snapshot = g_getenv("ZEROLAG_SNAPSHOT_INTERVAL_SECONDS");
    }
    if (!snapshot || !snapshot[0]) {
        snapshot = g_getenv("FINALSINK_SNAPSHOT_INTERVAL_SECONDS");
    }
    if (!background || !background[0]) background = "10800";
    if (!required || !required[0]) required = background;
    if (!update || !update[0]) update = background;
    if (!snapshot || !snapshot[0]) snapshot = "0";

    if (!crashcar_parse_duration_ns(
          background, &element->background_window_ns) ||
        !crashcar_parse_duration_ns(
          required, &element->background_required_ns) ||
        !crashcar_parse_duration_ns(
          update, &element->background_update_ns) ||
        !crashcar_parse_duration_ns(
          snapshot, &element->snapshot_interval_ns) ||
        element->background_window_ns <= 0 ||
        element->background_required_ns <= 0 ||
        element->background_update_ns <= 0) {
        return crashcar_set_failure(
          failure,
          "window durations must be canonical positive decimal seconds");
    }
    element->background_window_seconds =
      (double)element->background_window_ns /
      (double)CRASHCAR_NS_PER_SECOND;
    element->background_required_seconds =
      (double)element->background_required_ns /
      (double)CRASHCAR_NS_PER_SECOND;
    element->background_update_seconds =
      (double)element->background_update_ns /
      (double)CRASHCAR_NS_PER_SECOND;
    element->snapshot_interval_seconds =
      (double)element->snapshot_interval_ns /
      (double)CRASHCAR_NS_PER_SECOND;
    element->data_start_gps =
      (double)element->segment_run_start_gps_ns /
      (double)CRASHCAR_NS_PER_SECOND;
    return TRUE;
}

static gboolean crashcar_read_single_fd_snapshot(
  const char *fname,
  gchar **bytes_out,
  gsize *size_out,
  gchar **failure) {
    int fd = open(fname, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) {
        return crashcar_set_failure(
          failure, "cannot open canonical segment JSON with O_NOFOLLOW");
    }
    struct stat before;
    struct stat after;
    if (fstat(fd, &before) != 0 || !S_ISREG(before.st_mode) ||
        before.st_size <= 0 ||
        (guint64)before.st_size > CRASHCAR_SEGMENT_JSON_MAX_BYTES) {
        close(fd);
        return crashcar_set_failure(
          failure, "canonical segment JSON is not a bounded regular file");
    }
    const gsize size = (gsize)before.st_size;
    gchar *bytes = g_malloc(size + 1);
    gsize offset = 0;
    while (offset < size) {
        ssize_t amount = read(fd, bytes + offset, size - offset);
        if (amount < 0 && errno == EINTR) continue;
        if (amount <= 0) {
            g_free(bytes);
            close(fd);
            return crashcar_set_failure(
              failure, "canonical segment JSON snapshot read was incomplete");
        }
        offset += (gsize)amount;
    }
    if (fstat(fd, &after) != 0 ||
        before.st_dev != after.st_dev ||
        before.st_ino != after.st_ino ||
        before.st_size != after.st_size) {
        g_free(bytes);
        close(fd);
        return crashcar_set_failure(
          failure, "canonical segment JSON changed during snapshot read");
    }
    if (close(fd) != 0) {
        g_free(bytes);
        return crashcar_set_failure(
          failure, "canonical segment JSON snapshot close failed");
    }
    bytes[size] = '\0';
    *bytes_out = bytes;
    *size_out = size;
    return TRUE;
}

static gboolean crashcar_load_livetime_segments(CrashcarSingleFarEngine *element,
                                                gchar **failure) {
    const char *fname = g_getenv("CRASHCAR_SEGMENT_LIVETIME_CSV");
    const char *expected_json_sha =
      g_getenv("CRASHCAR_SEGMENT_LIVETIME_JSON_SHA256");
    const char *expected_source_sha =
      g_getenv("CRASHCAR_SEGMENT_SOURCE_XML_SHA256");
    if (!element || !fname || !fname[0]) {
        return crashcar_set_failure(
          failure, "missing CRASHCAR_SEGMENT_LIVETIME_CSV");
    }
    if (!crashcar_sha256_is_lowercase64(expected_json_sha) ||
        !crashcar_sha256_is_lowercase64(expected_source_sha)) {
        return crashcar_set_failure(
          failure, "segment JSON/source pins must be exact lowercase64");
    }

    gint64 expected_run_start_ns = 0;
    gint64 expected_run_end_ns = 0;
    int expected_worker_id = -1;
    if (!crashcar_parse_env_gps_seconds(
          "CRASHCAR_SEGMENT_RUN_START", &expected_run_start_ns, failure) ||
        !crashcar_parse_env_gps_seconds(
          "CRASHCAR_SEGMENT_RUN_END", &expected_run_end_ns, failure) ||
        expected_run_start_ns >= expected_run_end_ns ||
        !crashcar_parse_env_worker_id(&expected_worker_id, failure)) {
        if (failure && !*failure) {
            *failure = g_strdup("segment run frontier is empty");
        }
        return FALSE;
    }

    gchar *bytes = NULL;
    gsize size = 0;
    if (!crashcar_read_single_fd_snapshot(
          fname, &bytes, &size, failure)) {
        return FALSE;
    }
    gchar *actual_json_sha = g_compute_checksum_for_data(
      G_CHECKSUM_SHA256, (const guchar *)bytes, size);
    if (!actual_json_sha || strcmp(actual_json_sha, expected_json_sha) != 0) {
        g_free(actual_json_sha);
        g_free(bytes);
        return crashcar_set_failure(
          failure, "canonical segment JSON snapshot sha256 mismatch");
    }
    g_free(actual_json_sha);
    if (size < 3 || bytes[size - 1] != '\n' ||
        memchr(bytes, '\0', size) != NULL ||
        memchr(bytes, '\r', size) != NULL ||
        memchr(bytes, '\n', size - 1) != NULL ||
        !g_utf8_validate(bytes, size, NULL)) {
        g_free(bytes);
        return crashcar_set_failure(
          failure, "canonical segment JSON bytes or terminal newline invalid");
    }

    GArray *parsed_segments[MAX_NIFO] = { NULL, NULL, NULL, NULL };
    parsed_segments[0] =
      g_array_new(FALSE, FALSE, sizeof(CrashcarLivetimeSegment));
    parsed_segments[1] =
      g_array_new(FALSE, FALSE, sizeof(CrashcarLivetimeSegment));
    char parsed_source_sha[CRASHCAR_SHA256_HEX_LENGTH + 1] = { 0 };
    gint64 schema_version = 0;
    gint64 parsed_run_start_ns = 0;
    gint64 parsed_run_end_ns = 0;
    gint64 declared_livetime_ns[MAX_NIFO] = { 0, 0, 0, 0 };
    CrashcarJsonCursor input = { bytes, bytes + size - 1, NULL };
    gboolean valid =
      crashcar_json_expect(&input, "{\"schema_version\":") &&
      crashcar_parse_canonical_nonnegative_int64(&input, &schema_version) &&
      schema_version == CRASHCAR_SEGMENT_JSON_SCHEMA_VERSION &&
      crashcar_json_expect(&input, ",\"source_xml_sha256\":") &&
      crashcar_json_parse_sha256(&input, parsed_source_sha) &&
      crashcar_json_expect(&input, ",\"run_start\":") &&
      crashcar_json_parse_gps_ns(&input, &parsed_run_start_ns) &&
      crashcar_json_expect(&input, ",\"run_end\":") &&
      crashcar_json_parse_gps_ns(&input, &parsed_run_end_ns) &&
      crashcar_json_expect(&input, ",\"targets\":{\"H1\":") &&
      crashcar_json_parse_target(
        &input, parsed_run_start_ns, parsed_run_end_ns,
        parsed_segments[0], &declared_livetime_ns[0]) &&
      crashcar_json_expect(&input, ",\"L1\":") &&
      crashcar_json_parse_target(
        &input, parsed_run_start_ns, parsed_run_end_ns,
        parsed_segments[1], &declared_livetime_ns[1]) &&
      crashcar_json_expect(&input, "}}") &&
      input.cursor == input.end &&
      strcmp(parsed_source_sha, expected_source_sha) == 0 &&
      parsed_run_start_ns == expected_run_start_ns &&
      parsed_run_end_ns == expected_run_end_ns;
    if (!valid) {
        if (failure && !*failure) {
            *failure = input.failure
              ? g_strdup(input.failure)
              : g_strdup("canonical segment JSON binding mismatch");
        }
        g_free(input.failure);
        g_array_free(parsed_segments[0], TRUE);
        g_array_free(parsed_segments[1], TRUE);
        g_free(bytes);
        return FALSE;
    }
    g_free(input.failure);
    g_free(bytes);

    for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
        g_array_set_size(element->livetime_segments[ifo_id], 0);
        if (parsed_segments[ifo_id]->len > 0) {
            g_array_append_vals(
              element->livetime_segments[ifo_id],
              parsed_segments[ifo_id]->data,
              parsed_segments[ifo_id]->len);
        }
        g_array_free(parsed_segments[ifo_id], TRUE);
    }
    element->have_livetime_segments = TRUE;
    element->segment_livetime_binding_valid = TRUE;
    element->segment_run_start_gps_ns = parsed_run_start_ns;
    element->segment_run_end_gps_ns = parsed_run_end_ns;
    g_strlcpy(element->segment_source_xml_sha256, parsed_source_sha,
              sizeof(element->segment_source_xml_sha256));
    g_strlcpy(element->segment_livetime_json_sha256, expected_json_sha,
              sizeof(element->segment_livetime_json_sha256));
    element->worker_id = expected_worker_id;
    element->authority_mode =
      element->live_single_background_readonly
        ? CRASHCAR_SINGLE_AUTHORITY_MODE_LIVE_READONLY
        : (crashcar_single_background_mode_is_bg_only()
             ? CRASHCAR_SINGLE_AUTHORITY_MODE_BG_ONLY
             : CRASHCAR_SINGLE_AUTHORITY_MODE_CAUSAL_NOINJ);
    GST_INFO_OBJECT(element->owner,
      "loaded pinned segment JSON schema=%d H1_intervals=%u L1_intervals=%u "
      "H1_livetime_ns=%" G_GINT64_FORMAT " L1_livetime_ns=%" G_GINT64_FORMAT
      " worker=%d",
      CRASHCAR_SEGMENT_JSON_SCHEMA_VERSION,
      element->livetime_segments[0]->len,
      element->livetime_segments[1]->len,
      declared_livetime_ns[0], declared_livetime_ns[1],
      element->worker_id);
    return TRUE;
}

static gboolean crashcar_copy_required_sha_env(
  const char *name,
  char destination[CRASHCAR_SHA256_HEX_LENGTH + 1],
  gchar **failure) {
    const char *value = g_getenv(name);
    if (!crashcar_sha256_is_lowercase64(value)) {
        return crashcar_set_failure(
          failure, "background manifest digest pin is missing or invalid");
    }
    g_strlcpy(destination, value, CRASHCAR_SHA256_HEX_LENGTH + 1);
    return TRUE;
}

static gboolean crashcar_load_background_binding(
  CrashcarSingleFarEngine *element,
  gchar **failure) {
    if (!element || element->worker_id < 0) {
        return crashcar_set_failure(
          failure, "background binding requires a valid worker id");
    }
    const char *worker_count_text = g_getenv("CRASHCAR_BG_WORKER_COUNT");
    CrashcarJsonCursor count_input = {
      worker_count_text,
      worker_count_text ? worker_count_text + strlen(worker_count_text) : NULL,
      NULL
    };
    gint64 worker_count = 0;
    if (!worker_count_text || !worker_count_text[0] ||
        !crashcar_parse_canonical_nonnegative_int64(
          &count_input, &worker_count) ||
        count_input.cursor != count_input.end ||
        worker_count < 1 || worker_count > 4096 ||
        element->worker_id >= worker_count) {
        g_free(count_input.failure);
        return crashcar_set_failure(
          failure, "background worker count/id binding is invalid");
    }
    g_free(count_input.failure);

    gint64 origin_gps_ns = 0;
    if (!crashcar_parse_env_gps_seconds(
          "CRASHCAR_BG_ORIGIN_GPS", &origin_gps_ns, failure)) {
        return FALSE;
    }
    const char *path = element->live_single_background_readonly
      ? g_getenv("CRASHCAR_LIVE_SINGLE_BACKGROUND_JSON")
      : g_getenv("CRASHCAR_SINGLE_BACKGROUND_JSON");
    if (!path || !path[0] || !g_path_is_absolute(path)) {
        return crashcar_set_failure(
          failure, "single_background.json path must be absolute");
    }

    if (!crashcar_copy_required_sha_env(
          "CRASHCAR_BG_RUN_NAMESPACE_SHA256",
          element->run_namespace_sha256, failure) ||
        !crashcar_copy_required_sha_env(
          "CRASHCAR_BG_SOURCE_MANIFEST_SHA256",
          element->source_manifest_sha256, failure) ||
        !crashcar_copy_required_sha_env(
          "CRASHCAR_BG_RUNTIME_MANIFEST_SHA256",
          element->runtime_manifest_sha256, failure) ||
        !crashcar_copy_required_sha_env(
          "CRASHCAR_BG_CONFIG_SHA256",
          element->config_sha256, failure) ||
        !crashcar_copy_required_sha_env(
          "CRASHCAR_BG_SEGMENT_XML_SHA256",
          element->background_segment_xml_sha256, failure) ||
        !crashcar_copy_required_sha_env(
          "CRASHCAR_BG_SEGMENT_CANONICAL_SHA256",
          element->background_segment_canonical_sha256, failure) ||
        !crashcar_copy_required_sha_env(
          "CRASHCAR_TEMPLATE_SHAPE_MAP_SHA256",
          element->template_shape_map_sha256, failure)) {
        return FALSE;
    }
    if (!element->live_single_background_readonly &&
        (origin_gps_ns != element->segment_run_start_gps_ns ||
         strcmp(element->background_segment_xml_sha256,
                element->segment_source_xml_sha256) != 0 ||
         strcmp(element->background_segment_canonical_sha256,
                element->segment_livetime_json_sha256) != 0)) {
        return crashcar_set_failure(
          failure, "live schema4 segment/origin pins mismatch runtime input");
    }

    element->background_worker_count = (int)worker_count;
    element->background_origin_gps_ns = origin_gps_ns;
    g_free(element->background_json_fname);
    element->background_json_fname = g_strdup(path);
    element->background_binding_valid = TRUE;
    return TRUE;
}

static double crashcar_env_double(const char *name, double fallback) {
    const char *value = g_getenv(name);
    if (!value || !value[0]) return fallback;
    char *end = NULL;
    double parsed = g_ascii_strtod(value, &end);
    if (end == value || !isfinite(parsed)) return fallback;
    return parsed;
}

static gboolean crashcar_ligo_gps_to_ns(const LIGOTimeGPS *gps,
                                         gint64 *gps_ns_out) {
    if (!gps || !gps_ns_out) return FALSE;
    return crashcar_checked_gps_ns(
      (gint64)gps->gpsSeconds,
      (gint64)gps->gpsNanoSeconds,
      gps_ns_out);
}

static double crashcar_ns_to_seconds(gint64 gps_ns) {
    return (double)gps_ns / (double)CRASHCAR_NS_PER_SECOND;
}


static const LIGOTimeGPS *crashcar_component_end_time(
  const PostcohInspiralTable *table,
  int ifo_id) {
    return &table->end_time_sngl[ifo_id];
}

static gboolean crashcar_assignment_window_end_ns(
  const CrashcarSingleFarEngine *element,
  gint64 row_assignment_gps_ns,
  gint64 *end_gps_ns_out) {
    if (!element || !end_gps_ns_out ||
        row_assignment_gps_ns < element->segment_run_start_gps_ns ||
        row_assignment_gps_ns >= element->segment_run_end_gps_ns) {
        return FALSE;
    }

    gint64 end_gps_ns = row_assignment_gps_ns;

    gint64 first_full_end_ns = 0;
    if (!crashcar_add_nonnegative_offset(
          element->segment_run_start_gps_ns,
          (guint64)element->background_required_ns,
          &first_full_end_ns)) {
        return FALSE;
    }
    if (end_gps_ns >= first_full_end_ns &&
        element->background_update_ns > 0) {
        guint64 distance_ns = 0;
        if (!crashcar_ordered_distance_u64(
              first_full_end_ns, end_gps_ns, &distance_ns)) {
            return FALSE;
        }
        const guint64 update_ns =
          (guint64)element->background_update_ns;
        const guint64 offset_ns =
          (distance_ns / update_ns) * update_ns;
        if (!crashcar_add_nonnegative_offset(
              first_full_end_ns, offset_ns, &end_gps_ns)) {
            return FALSE;
        }
    }
    *end_gps_ns_out = end_gps_ns;
    return TRUE;
}
#define CRASHCAR_BINARY64_EXACT_INTEGER_LIMIT G_GINT64_CONSTANT(9007199254740992)


#define CRASHCAR_MIN_SNR 0x1.0000000000000p+2
#define CRASHCAR_BETA_STEP 0x1.89374bc6a7efap-9
#define CRASHCAR_LOG_2PI 0x1.d67f1c864beb4p+0
#define CRASHCAR_LOG_64 0x1.0a2b23f3bab73p+2
#define CRASHCAR_HALF 0x1.0000000000000p-1
#define CRASHCAR_BETA_GRID_SIZE 64

gboolean crashcar_singlefar_dof_for_bank(int bankid, double *dof_out) {
    if (!dof_out) return FALSE;
    *dof_out = NAN;
    if (bankid >= 0 && bankid <= 99) {
        *dof_out = 120.0;
        return TRUE;
    }
    if (bankid >= 100 && bankid <= 383) {
        *dof_out = 600.0;
        return TRUE;
    }
    return FALSE;
}

guint crashcar_singlefar_beta_grid_size(void) {
    return CRASHCAR_BETA_GRID_SIZE;
}

gboolean crashcar_singlefar_beta_at(guint index, double *beta_out) {
    if (!beta_out || index >= CRASHCAR_BETA_GRID_SIZE) return FALSE;
    const double j_as_double = (double)index;
    const double beta_product = CRASHCAR_BETA_STEP * j_as_double;
    const double beta = CRASHCAR_BETA_STEP + beta_product;
    if (!isfinite(j_as_double) || !isfinite(beta_product) ||
        !(beta > 0.0) || !isfinite(beta)) return FALSE;
    *beta_out = beta;
    return TRUE;
}

static gsize crashcar_singlefar_debug_initialized = 0;

static gchar *crashcar_template_shape_key(int ifo_id,
                                          int bankid,
                                          int tmplt_idx) {
    return g_strdup_printf("%d:%d:%d", ifo_id, bankid, tmplt_idx);
}

static const char *CRASHCAR_TEMPLATE_SHAPE_HEADER =
  "ifo_id,bankid,tmplt_idx,a_eff,dof,ifo,source_class";
#define CRASHCAR_TEMPLATE_SHAPE_MAX_PAYLOAD_BYTES 255
#define CRASHCAR_TEMPLATE_SHAPE_BANK_COUNT 384
#define CRASHCAR_TEMPLATE_SHAPE_TEMPLATES_PER_BANK 1000
#define CRASHCAR_TEMPLATE_SHAPE_ROWS_PER_IFO \
  (CRASHCAR_TEMPLATE_SHAPE_BANK_COUNT * \
   CRASHCAR_TEMPLATE_SHAPE_TEMPLATES_PER_BANK)
#define CRASHCAR_TEMPLATE_SHAPE_EXPECTED_ROWS \
  (2 * CRASHCAR_TEMPLATE_SHAPE_ROWS_PER_IFO)

static gboolean crashcar_parse_canonical_nonnegative_int(const char *token,
                                                         int *value) {
    if (!token || !value || token[0] == '\0') return FALSE;
    const size_t length = strlen(token);
    if ((length > 1 && token[0] == '0') || length > 10) return FALSE;
    for (const char *cursor = token; *cursor; ++cursor) {
        if (!g_ascii_isdigit(*cursor)) return FALSE;
    }
    errno = 0;
    char *end = NULL;
    long parsed = strtol(token, &end, 10);
    if (errno == ERANGE || end == token || !end || *end != '\0' ||
        parsed < 0 || parsed > INT_MAX) return FALSE;
    char canonical[32];
    g_snprintf(canonical, sizeof(canonical), "%ld", parsed);
    if (strcmp(canonical, token) != 0) return FALSE;
    *value = (int)parsed;
    return TRUE;
}

static gboolean crashcar_parse_worker_bank_roster(
  CrashcarSingleFarEngine *element,
  gchar **failure) {
    if (!element || !element->worker_bank_id_values ||
        !element->worker_bank_ids || !element->worker_bank_ids[0] ||
        element->stream_count <= 0 ||
        element->stream_count > CRASHCAR_TEMPLATE_SHAPE_BANK_COUNT ||
        element->stream_id < 0 ||
        element->stream_id >= element->stream_count ||
        element->stream_bank_id < 0 ||
        element->stream_bank_id >= CRASHCAR_TEMPLATE_SHAPE_BANK_COUNT) {
        return crashcar_set_failure(
          failure, "missing crashcar graph stream/bank binding");
    }
    gchar **tokens = g_strsplit(element->worker_bank_ids, ",", -1);
    const guint token_count = tokens ? g_strv_length(tokens) : 0;
    if (token_count != (guint)element->stream_count) {
        g_strfreev(tokens);
        return crashcar_set_failure(
          failure, "worker bank roster count differs from stream count");
    }

    g_array_set_size(element->worker_bank_id_values, 0);
    for (guint index = 0; index < token_count; ++index) {
        int bank_id = -1;
        if (!crashcar_parse_canonical_nonnegative_int(
              tokens[index], &bank_id) ||
            bank_id >= CRASHCAR_TEMPLATE_SHAPE_BANK_COUNT) {
            g_array_set_size(element->worker_bank_id_values, 0);
            g_strfreev(tokens);
            return crashcar_set_failure(
              failure, "worker bank roster is not canonical integer CSV");
        }
        if (element->worker_bank_id_values->len > 0 &&
            bank_id <= g_array_index(
              element->worker_bank_id_values, int,
              element->worker_bank_id_values->len - 1)) {
            g_array_set_size(element->worker_bank_id_values, 0);
            g_strfreev(tokens);
            return crashcar_set_failure(
              failure,
              "worker bank roster must be strictly increasing and unique");
        }
        g_array_append_val(element->worker_bank_id_values, bank_id);
    }
    g_strfreev(tokens);
    if (g_array_index(element->worker_bank_id_values, int,
                      (guint)element->stream_id) !=
        element->stream_bank_id) {
        g_array_set_size(element->worker_bank_id_values, 0);
        return crashcar_set_failure(
          failure, "stream bank id differs from graph roster ordinal");
    }
    return TRUE;
}

static gboolean crashcar_row_bank_matches_graph(
  const CrashcarSingleFarEngine *element,
  int bank_id) {
    return element && element->graph_binding_locked &&
           element->worker_bank_id_values &&
           element->worker_bank_id_values->len ==
             (guint)element->stream_count &&
           element->stream_count > 0 &&
           element->stream_count <= CRASHCAR_TEMPLATE_SHAPE_BANK_COUNT &&
           element->stream_id >= 0 &&
           element->stream_id < element->stream_count &&
           element->stream_bank_id >= 0 &&
           element->stream_bank_id < CRASHCAR_TEMPLATE_SHAPE_BANK_COUNT &&
           g_array_index(element->worker_bank_id_values, int,
                         (guint)element->stream_id) ==
             element->stream_bank_id &&
           bank_id == element->stream_bank_id;
}

static gboolean crashcar_parse_canonical_binary64_hex(const char *token,
                                                       double *value) {
    if (!token || !value) return FALSE;
    const size_t length = strlen(token);
    /* 0x1. + 13 binary64 mantissa hex digits + p + sign + one
     * exponent digit is the shortest canonical finite positive encoding. */
    if (length < 20 || token[0] != '0' || token[1] != 'x' ||
        token[2] != '1' || token[3] != '.') return FALSE;
    for (size_t i = 4; i < 17; ++i) {
        if (!g_ascii_isxdigit(token[i]) || g_ascii_isupper(token[i])) {
            return FALSE;
        }
    }
    if (token[17] != 'p' || (token[18] != '+' && token[18] != '-')) {
        return FALSE;
    }
    if (token[19] == '\0') return FALSE;
    for (const char *cursor = token + 19; *cursor; ++cursor) {
        if (!g_ascii_isdigit(*cursor)) return FALSE;
    }
    if (token[19] == '0' && token[20] != '\0') return FALSE;

    errno = 0;
    char *end = NULL;
    double parsed = g_ascii_strtod(token, &end);
    if (errno == ERANGE || end == token || !end || *end != '\0' ||
        !(parsed > 0.0) || !isfinite(parsed)) return FALSE;
    char canonical[64];
    g_snprintf(canonical, sizeof(canonical), "%.13a", parsed);
    if (strcmp(canonical, token) != 0) return FALSE;
    *value = parsed;
    return TRUE;
}

gboolean crashcar_singlefar_parse_template_shape_row(
  const char *line, int *ifo_id, int *bankid, int *tmplt_idx,
  double *a_eff, double *dof) {
    if (!line || !ifo_id || !bankid || !tmplt_idx || !a_eff || !dof ||
        line[0] == '\0' || strchr(line, '\n') || strchr(line, '\r') ||
        strchr(line, '\0') != line + strlen(line)) return FALSE;
    if (strcmp(line, CRASHCAR_TEMPLATE_SHAPE_HEADER) == 0) return FALSE;
    gchar **fields = g_strsplit(line, ",", -1);
    if (g_strv_length(fields) != 7) {
        g_strfreev(fields);
        return FALSE;
    }

    int parsed_ifo = -1, parsed_bank = -1, parsed_tmplt = -1;
    double parsed_a_eff = NAN;
    gboolean valid =
      crashcar_parse_canonical_nonnegative_int(fields[0], &parsed_ifo) &&
      crashcar_parse_canonical_nonnegative_int(fields[1], &parsed_bank) &&
      crashcar_parse_canonical_nonnegative_int(fields[2], &parsed_tmplt) &&
      crashcar_parse_canonical_binary64_hex(fields[3], &parsed_a_eff) &&
      (parsed_ifo == 0 || parsed_ifo == 1) &&
      parsed_bank >= 0 && parsed_bank < CRASHCAR_TEMPLATE_SHAPE_BANK_COUNT &&
      parsed_tmplt >= 0 &&
      parsed_tmplt < CRASHCAR_TEMPLATE_SHAPE_TEMPLATES_PER_BANK;
    double required_dof = NAN;
    const char *required_ifo = NULL;
    const char *required_source_class = NULL;
    const char *required_dof_text = NULL;
    if (valid && crashcar_singlefar_dof_for_bank(parsed_bank, &required_dof)) {
        required_ifo = parsed_ifo == 0 ? "H1" : "L1";
        required_source_class = parsed_bank <= 99 ? "BNS" : "NSBH";
        required_dof_text = parsed_bank <= 99 ? "120" : "600";
        valid = strcmp(fields[4], required_dof_text) == 0 &&
                strcmp(fields[5], required_ifo) == 0 &&
                strcmp(fields[6], required_source_class) == 0;
    } else {
        valid = FALSE;
    }
    if (valid) {
        *ifo_id = parsed_ifo;
        *bankid = parsed_bank;
        *tmplt_idx = parsed_tmplt;
        *a_eff = parsed_a_eff;
        *dof = required_dof;
    }
    g_strfreev(fields);
    return valid;
}

static void crashcar_load_template_shape_map(CrashcarSingleFarEngine *element) {
    if (element->template_shape_map_loaded) return;
    element->template_shape_map_loaded = TRUE;
    if (!element->template_shape_map) {
        element->template_shape_map = g_hash_table_new_full(
          g_str_hash, g_str_equal, g_free, g_free);
    }
    if (!element->template_shape_map_fname ||
        !element->template_shape_map_fname[0]) {
        GST_ERROR_OBJECT(element->owner, "canonical A_eff map is not configured");
        g_error("canonical crashcar A_eff map is required");
    }

    FILE *input = fopen(element->template_shape_map_fname, "rb");
    if (!input) {
        GST_ERROR_OBJECT(element->owner, "failed to open canonical A_eff map %s",
                         element->template_shape_map_fname);
        g_error("canonical crashcar A_eff map cannot be opened");
    }

    GChecksum *shape_checksum = g_checksum_new(G_CHECKSUM_SHA256);
    if (!shape_checksum) {
        fclose(input);
        g_error("canonical crashcar A_eff map checksum cannot be created");
    }
    char *line = NULL;
    size_t line_capacity = 0;
    guint loaded = 0;
    guint line_number = 0;
    gboolean valid = TRUE;
    ssize_t bytes_read = 0;
    while ((bytes_read = getline(&line, &line_capacity, input)) >= 0) {
        g_checksum_update(
          shape_checksum, (const guchar *)line, (gsize)bytes_read);
        ++line_number;
        if (bytes_read <= 0 || line[bytes_read - 1] != '\n' ||
            !g_utf8_validate(line, bytes_read, NULL) ||
            memchr(line, '\0', (size_t)bytes_read) != NULL ||
            memchr(line, '\r', (size_t)bytes_read) != NULL) {
            valid = FALSE;
            break;
        }
        const size_t payload_length = (size_t)bytes_read - 1;
        if (payload_length == 0 ||
            payload_length > CRASHCAR_TEMPLATE_SHAPE_MAX_PAYLOAD_BYTES ||
            memchr(line, '\n', payload_length) != NULL) {
            valid = FALSE;
            break;
        }
        line[payload_length] = '\0';
        if (line_number == 1) {
            if (strcmp(line, CRASHCAR_TEMPLATE_SHAPE_HEADER) != 0) {
                valid = FALSE;
                break;
            }
            continue;
        }
        if (loaded >= CRASHCAR_TEMPLATE_SHAPE_EXPECTED_ROWS) {
            valid = FALSE;
            break;
        }
        int ifo_id = -1, bankid = -1, tmplt_idx = -1;
        double a_eff = NAN, dof = NAN;
        if (!crashcar_singlefar_parse_template_shape_row(
              line, &ifo_id, &bankid, &tmplt_idx, &a_eff, &dof)) {
            valid = FALSE;
            break;
        }
        const guint expected_ifo = loaded / CRASHCAR_TEMPLATE_SHAPE_ROWS_PER_IFO;
        const guint within_ifo = loaded % CRASHCAR_TEMPLATE_SHAPE_ROWS_PER_IFO;
        const guint expected_bank =
          within_ifo / CRASHCAR_TEMPLATE_SHAPE_TEMPLATES_PER_BANK;
        const guint expected_tmplt =
          within_ifo % CRASHCAR_TEMPLATE_SHAPE_TEMPLATES_PER_BANK;
        if ((guint)ifo_id != expected_ifo || (guint)bankid != expected_bank ||
            (guint)tmplt_idx != expected_tmplt) {
            valid = FALSE;
            break;
        }
        gchar *key = crashcar_template_shape_key(ifo_id, bankid, tmplt_idx);
        if (g_hash_table_contains(element->template_shape_map, key)) {
            g_free(key);
            valid = FALSE;
            break;
        }
        CrashcarTemplateShape *shape = g_new0(CrashcarTemplateShape, 1);
        shape->a_eff = a_eff;
        shape->has_a_eff = TRUE;
        shape->dof = dof;
        shape->has_dof = TRUE;
        g_hash_table_insert(element->template_shape_map, key, shape);
        ++loaded;
    }
    if (ferror(input) || line_number == 0 ||
        loaded != CRASHCAR_TEMPLATE_SHAPE_EXPECTED_ROWS) {
        valid = FALSE;
    }
    free(line);
    if (fclose(input) != 0) valid = FALSE;
    const char *actual_shape_sha = g_checksum_get_string(shape_checksum);
    if (!element->background_binding_valid ||
        !crashcar_sha256_is_lowercase64(actual_shape_sha) ||
        strcmp(actual_shape_sha, element->template_shape_map_sha256) != 0) {
        valid = FALSE;
    }
    if (!valid) {
        GST_ERROR_OBJECT(element->owner,
                         "invalid canonical A_eff map %s at line %u; loaded %u/%u",
                         element->template_shape_map_fname, line_number, loaded,
                         (guint)CRASHCAR_TEMPLATE_SHAPE_EXPECTED_ROWS);
        g_checksum_free(shape_checksum);
        g_error("invalid canonical crashcar A_eff map");
    }
    g_checksum_free(shape_checksum);
    GST_INFO_OBJECT(element->owner, "loaded %u canonical crashcar A_eff rows from %s",
                    loaded, element->template_shape_map_fname);
}

static gboolean crashcar_lookup_template_shape(
    const CrashcarSingleFarEngine *element_const,
    int ifo_id,
    int bankid,
    int tmplt_idx,
    double *a_eff,
    double *dof) {
    CrashcarSingleFarEngine *element = (CrashcarSingleFarEngine *)element_const;
    if (!a_eff || !dof) return FALSE;
    *a_eff = NAN;
    *dof = NAN;
    if (ifo_id < 0 || ifo_id > 1 || bankid < 0 || bankid >= 384 ||
        tmplt_idx < 0 || tmplt_idx >= 1000) return FALSE;
    if (!element->template_shape_map_loaded) {
        crashcar_load_template_shape_map(element);
    }
    gchar *key = crashcar_template_shape_key(ifo_id, bankid, tmplt_idx);
    CrashcarTemplateShape *shape =
      (CrashcarTemplateShape *)g_hash_table_lookup(element->template_shape_map,
                                                   key);
    g_free(key);
    double required_dof = NAN;
    if (!shape || !shape->has_a_eff || !shape->has_dof ||
        !(shape->a_eff > 0.0) || !isfinite(shape->a_eff) ||
        !crashcar_singlefar_dof_for_bank(bankid, &required_dof) ||
        shape->dof != required_dof) return FALSE;
    *a_eff = shape->a_eff;
    *dof = required_dof;
    return TRUE;
}

static gboolean crashcar_gaussian_log_term(double x,
                                                double mu,
                                                double variance,
                                                double *out) {
    if (!out || !isfinite(x) || !isfinite(mu) ||
        !(variance > 0.0) || !isfinite(variance)) return FALSE;
    const double delta = x - mu;
    const double delta2 = delta * delta;
    const double scaled = delta2 / variance;
    const double log_variance = log(variance);
    const double norm_term = CRASHCAR_LOG_2PI + log_variance;
    const double total = norm_term + scaled;
    const double log_normal = -CRASHCAR_HALF * total;
    if (!isfinite(delta) || !isfinite(delta2) || !isfinite(scaled) ||
        !isfinite(log_variance) || !isfinite(norm_term) ||
        !isfinite(total) || !isfinite(log_normal)) return FALSE;
    *out = log_normal;
    return TRUE;
}

gboolean crashcar_singlefar_compute_llr(double rho,
                                        double chisq,
                                        double a_eff,
                                        double dof,
                                        double *llr_out) {
    if (!llr_out) return FALSE;
    *llr_out = NAN;
    if (!(rho >= CRASHCAR_MIN_SNR) || !isfinite(rho) ||
        !(chisq > 0.0) || !isfinite(chisq) ||
        !(a_eff > 0.0) || !isfinite(a_eff) ||
        (dof != 120.0 && dof != 600.0)) return FALSE;

    const int nu = dof == 120.0 ? 120 : 600;
    const double nu_double = (double)nu;
    const double rho2 = rho * rho;
    const double x = nu_double * chisq;
    const double lambda0 = rho2 * a_eff;
    const double mu_noise = nu_double + lambda0;
    const double tmp_noise = 2.0 * lambda0;
    const double inner_noise = nu_double + tmp_noise;
    const double variance_noise = 2.0 * inner_noise;
    const double rho2_half = rho2 / 2.0;
    if (!isfinite(rho2) || !isfinite(x) ||
        !(lambda0 > 0.0) || !isfinite(lambda0) ||
        !isfinite(mu_noise) || !isfinite(tmp_noise) ||
        !isfinite(inner_noise) || !(variance_noise > 0.0) ||
        !isfinite(variance_noise) || !isfinite(rho2_half)) return FALSE;

    double log_p_noise = NAN;
    if (!crashcar_gaussian_log_term(x, mu_noise, variance_noise,
                                    &log_p_noise)) return FALSE;

    double components[CRASHCAR_BETA_GRID_SIZE];
    for (guint j = 0; j < CRASHCAR_BETA_GRID_SIZE; ++j) {
        double beta = NAN;
        if (!crashcar_singlefar_beta_at(j, &beta)) return FALSE;
        const double beta2 = beta * beta;
        const double lambda1 = beta2 * lambda0;
        const double mu1 = nu_double + lambda1;
        const double tmp1 = 2.0 * lambda1;
        const double inner1 = nu_double + tmp1;
        const double variance1 = 2.0 * inner1;
        if (!isfinite(beta) || !isfinite(beta2) ||
            !(lambda1 > 0.0) || !isfinite(lambda1) || !isfinite(mu1) ||
            !isfinite(tmp1) || !isfinite(inner1) ||
            !(variance1 > 0.0) || !isfinite(variance1) ||
            !crashcar_gaussian_log_term(x, mu1, variance1,
                                        &components[j])) return FALSE;
    }

    double maximum = components[0];
    for (guint j = 1; j < CRASHCAR_BETA_GRID_SIZE; ++j) {
        if (components[j] > maximum) maximum = components[j];
    }
    double sum = 0.0;
    for (guint j = 0; j < CRASHCAR_BETA_GRID_SIZE; ++j) {
        const double shifted = components[j] - maximum;
        const double term = exp(shifted);
        sum = sum + term;
        if (!isfinite(shifted) || !isfinite(term) || !isfinite(sum)) {
            return FALSE;
        }
    }
    if (!(sum >= 1.0)) return FALSE;
    const double log_sum = log(sum);
    const double log_p_signal_unweighted = maximum + log_sum;
    const double log_p_signal = log_p_signal_unweighted - CRASHCAR_LOG_64;
    const double log_ratio = log_p_signal - log_p_noise;
    const double llr = log_ratio + rho2_half;
    if (!isfinite(log_sum) || !isfinite(log_p_signal_unweighted) ||
        !isfinite(log_p_signal) || !isfinite(log_ratio) || !isfinite(llr)) {
        return FALSE;
    }
    *llr_out = llr;
    return TRUE;
}

static gboolean crashcar_singlefar_open_detail(CrashcarSingleFarEngine *element) {
    if (element->detail_output_file) return TRUE;
    if (!element->detail_output_fname || !element->detail_output_fname[0]) {
        return FALSE;
    }

    g_mutex_lock(&crashcar_detail_file_mutex);
    if (!element->detail_output_file) {
        element->detail_output_file = fopen(element->detail_output_fname, "a");
        if (!element->detail_output_file) {
            GST_WARNING_OBJECT(element->owner, "failed to open crashcar detail output %s",
                               element->detail_output_fname);
            g_mutex_unlock(&crashcar_detail_file_mutex);
            return FALSE;
        }

        if (fseek(element->detail_output_file, 0, SEEK_END) == 0 &&
            ftell(element->detail_output_file) == 0) {
            fprintf(element->detail_output_file,
                    "event_id,bankid,tmplt_idx,end_time,end_time_ns,ifo_id,"
                    "is_background,snglsnr,chisq,llr,far_calculated_exact,"
                    "far_calculated_valid,far_calculated_support_count,"
                    "bg_livetime,cohsnr,cmbchisq,far_multi,"
                    "far_1w_sngl,far_1d_sngl,far_2h_sngl,far_sngl_legacy,"
                    "far_assigned_exact,far_assigned_valid,far_assigned_source,"
                    "far_assigned_status,a_eff,dof,has_snr_series,bg_start,bg_end,"
                    "window_count,total_window_count,single_bg_authority_valid,"
                    "single_bg_authority_version,single_bg_authority_epoch_gps_ns,"
                    "single_bg_authority_provenance_sha256,code_version,feature_gps,"
                    "assignment_gps,assignment_unix,single_bg_path,"
                    "single_bg_refresh_status,single_bg_refresh_reject_count,"
                    "single_bg_last_candidate_version,"
                    "single_bg_last_candidate_coverage_gps_ns,"
                    "single_bg_last_candidate_sha256,single_bg_lkg_reused\n");
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

gboolean crashcar_singlefar_evaluate_far_with_tail(
    const double *input_ranks,
    guint n_all,
    double livetime,
    double rank,
    double tail_log10_far,
    CrashcarSingleFarEvaluation *evaluation) {
    if (!evaluation) return FALSE;
    evaluation->calculated_far = NAN;
    evaluation->assigned_far = NAN;
    evaluation->r_tail = NAN;
    evaluation->tail_slope = NAN;
    evaluation->tail_intercept = NAN;
    evaluation->used_tail_fit = FALSE;
    if (!input_ranks || n_all == 0 || !(livetime > 0.0) ||
        !isfinite(livetime) || !isfinite(rank) ||
        !isfinite(tail_log10_far) || !(tail_log10_far < 0.0)) {
        return FALSE;
    }

    double *sorted = g_new(double, n_all);
    guint query_count_ge = 0;
    for (guint i = 0; i < n_all; ++i) {
        if (!isfinite(input_ranks[i])) {
            g_free(sorted);
            return FALSE;
        }
        sorted[i] = input_ranks[i];
        if (input_ranks[i] >= rank) ++query_count_ge;
    }
    qsort(sorted, n_all, sizeof(double), crashcar_compare_double);

    evaluation->calculated_far =
      MAX((double)query_count_ge, 1.0) / livetime;
    if (!crashcar_far_double_is_valid(evaluation->calculated_far)) {
        g_free(sorted);
        return FALSE;
    }

    double *raw_xs = g_new(double, n_all);
    double *raw_log_fars = g_new(double, n_all);
    guint n_raw = 0;
    for (guint i = 0; i < n_all;) {
        const double llr = sorted[i];
        guint j = i + 1;
        while (j < n_all && sorted[j] == llr) ++j;
        const double count_ge = (double)(n_all - i);
        const double far = MAX(count_ge, 1.0) / livetime;
        if (!crashcar_far_double_is_valid(far)) {
            g_free(sorted);
            g_free(raw_xs);
            g_free(raw_log_fars);
            return FALSE;
        }
        raw_xs[n_raw] = llr;
        raw_log_fars[n_raw] = log10(far);
        ++n_raw;
        i = j;
    }
    g_free(sorted);

    guint tail_idx = 0;
    double best_dist = fabs(raw_log_fars[0] - tail_log10_far);
    for (guint i = 1; i < n_raw; ++i) {
        const double dist = fabs(raw_log_fars[i] - tail_log10_far);
        if (dist < best_dist) {
            best_dist = dist;
            tail_idx = i;
        }
    }
    evaluation->r_tail = raw_xs[tail_idx];

    const guint n_tail = n_raw - tail_idx;
    if (n_tail >= 2) {
        double slope = NAN;
        double intercept = NAN;
        if (crashcar_fit_line_through_fixed_point(raw_xs + tail_idx,
                                                  raw_log_fars + tail_idx,
                                                  n_tail,
                                                  evaluation->r_tail,
                                                  tail_log10_far,
                                                  &slope,
                                                  &intercept)) {
            evaluation->tail_slope = slope;
            evaluation->tail_intercept = intercept;
        }
    }

    if (rank <= evaluation->r_tail) {
        evaluation->assigned_far = evaluation->calculated_far;
    } else if (isfinite(evaluation->tail_slope) &&
               evaluation->tail_slope < 0.0 &&
               isfinite(evaluation->tail_intercept)) {
        evaluation->assigned_far = pow(
          10.0,
          tail_log10_far +
            evaluation->tail_slope * (rank - evaluation->r_tail));
        evaluation->used_tail_fit = TRUE;
    }

    g_free(raw_xs);
    g_free(raw_log_fars);
    return crashcar_far_double_is_valid(evaluation->assigned_far);
}

gboolean crashcar_singlefar_evaluate_far(
    const double *input_ranks,
    guint n_all,
    double livetime,
    double rank,
    CrashcarSingleFarEvaluation *evaluation) {
    return crashcar_singlefar_evaluate_far_with_tail(
      input_ranks, n_all, livetime, rank, -2.0, evaluation);
}

static gint64 crashcar_window_ifo_livetime_ns(
  const CrashcarSingleFarEngine *element,
  int ifo_id,
  gint64 start_ns,
  gint64 end_ns) {
    if (!element || ifo_id < 0 || ifo_id >= MAX_NIFO ||
        !element->segment_livetime_binding_valid ||
        !element->have_livetime_segments ||
        end_ns <= start_ns) {
        return 0;
    }

    gint64 livetime_ns = 0;
    GArray *segments = element->livetime_segments[ifo_id];
    if (!segments) return 0;
    for (guint i = 0; i < segments->len; ++i) {
        CrashcarLivetimeSegment segment =
          g_array_index(segments, CrashcarLivetimeSegment, i);
        const gint64 overlap_start_ns =
          MAX(start_ns, segment.start_gps_ns);
        const gint64 overlap_end_ns =
          MIN(end_ns, segment.end_gps_ns);
        if (overlap_end_ns > overlap_start_ns) {
            guint64 duration_u64 = 0;
            if (!crashcar_ordered_distance_u64(
                  overlap_start_ns, overlap_end_ns, &duration_u64) ||
                duration_u64 > (guint64)G_MAXINT64) {
                return 0;
            }
            const gint64 duration_ns = (gint64)duration_u64;
            if (livetime_ns > G_MAXINT64 - duration_ns) return 0;
            livetime_ns += duration_ns;
        }
    }
    return livetime_ns;
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

static gboolean crashcar_authority_tail_metrics(
  const double *input_ranks,
  guint rank_count,
  double livetime,
  double tail_log10_far,
  double *r_tail_out,
  double *slope_out,
  guint *fit_unique_rank_count_out) {
    if (!input_ranks || rank_count < 2 || !(livetime > 0.0) ||
        !isfinite(livetime) || !isfinite(tail_log10_far) ||
        !(tail_log10_far < 0.0) || !r_tail_out || !slope_out ||
        !fit_unique_rank_count_out) {
        return FALSE;
    }
    double *sorted = g_new(double, rank_count);
    for (guint index = 0; index < rank_count; ++index) {
        if (!isfinite(input_ranks[index])) {
            g_free(sorted);
            return FALSE;
        }
        sorted[index] = input_ranks[index];
    }
    qsort(sorted, rank_count, sizeof(double), crashcar_compare_double);

    double *unique_ranks = g_new(double, rank_count);
    double *unique_log_fars = g_new(double, rank_count);
    guint unique_count = 0;
    for (guint begin = 0; begin < rank_count;) {
        guint end = begin + 1;
        while (end < rank_count && sorted[end] == sorted[begin]) ++end;
        const double far = (double)(rank_count - begin) / livetime;
        if (!crashcar_far_double_is_valid(far)) {
            g_free(unique_log_fars);
            g_free(unique_ranks);
            g_free(sorted);
            return FALSE;
        }
        unique_ranks[unique_count] = sorted[begin];
        unique_log_fars[unique_count] = log10(far);
        ++unique_count;
        begin = end;
    }
    g_free(sorted);

    guint tail_index = 0;
    double best_distance = fabs(unique_log_fars[0] - tail_log10_far);
    for (guint index = 1; index < unique_count; ++index) {
        const double distance = fabs(
          unique_log_fars[index] - tail_log10_far);
        if (distance < best_distance) {
            best_distance = distance;
            tail_index = index;
        }
    }
    const guint fit_count = unique_count - tail_index;
    double slope = NAN;
    double intercept = NAN;
    const gboolean valid =
      fit_count >= 2 &&
      crashcar_fit_line_through_fixed_point(
        unique_ranks + tail_index, unique_log_fars + tail_index,
        fit_count, unique_ranks[tail_index], tail_log10_far,
        &slope, &intercept) &&
      isfinite(slope) && slope < 0.0 && isfinite(intercept);
    if (valid) {
        *r_tail_out = unique_ranks[tail_index];
        *slope_out = slope;
        *fit_unique_rank_count_out = fit_count;
    }
    g_free(unique_log_fars);
    g_free(unique_ranks);
    return valid;
}

static void crashcar_parsed_background_clear(
  CrashcarParsedBackground *background) {
    if (!background) return;
    for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
        if (background->points[ifo_id]) {
            g_array_free(background->points[ifo_id], TRUE);
            background->points[ifo_id] = NULL;
        }
        if (background->ranks[ifo_id]) {
            g_array_free(background->ranks[ifo_id], TRUE);
            background->ranks[ifo_id] = NULL;
        }
    }
    memset(background, 0, sizeof(*background));
}

static gboolean crashcar_binary64_bits_equal(double left, double right) {
    guint64 left_bits = 0;
    guint64 right_bits = 0;
    memcpy(&left_bits, &left, sizeof(left_bits));
    memcpy(&right_bits, &right, sizeof(right_bits));
    return left_bits == right_bits;
}

static gboolean crashcar_format_canonical_binary64(
  double value,
  char output[64]) {
    if (!output || !isfinite(value) || (value == 0.0 && signbit(value))) {
        return FALSE;
    }
    g_snprintf(output, 64, "%.13a", value);
    return output[0] != '\0' && strlen(output) < 64;
}

static gboolean crashcar_format_canonical_json_double(
  double value,
  char output[G_ASCII_DTOSTR_BUF_SIZE]) {
    if (!output || !isfinite(value) || (value == 0.0 && signbit(value))) {
        return FALSE;
    }
    g_ascii_dtostr(output, G_ASCII_DTOSTR_BUF_SIZE, value);
    return output[0] != '\0' &&
      strlen(output) < G_ASCII_DTOSTR_BUF_SIZE;
}

static gboolean crashcar_json_parse_canonical_double_number(
  CrashcarJsonCursor *input,
  double *value_out) {
    if (!input || !value_out || input->cursor >= input->end) {
        return crashcar_json_fail(input, "missing canonical JSON number");
    }
    const char *begin = input->cursor;
    const char *cursor = begin;
    while (cursor < input->end &&
           (g_ascii_isdigit(*cursor) || *cursor == '-' || *cursor == '+' ||
            *cursor == '.' || *cursor == 'e' || *cursor == 'E')) {
        ++cursor;
    }
    const size_t length = (size_t)(cursor - begin);
    if (length == 0 || length >= G_ASCII_DTOSTR_BUF_SIZE) {
        return crashcar_json_fail(input, "canonical JSON number is malformed");
    }
    char token[G_ASCII_DTOSTR_BUF_SIZE];
    memcpy(token, begin, length);
    token[length] = '\0';
    errno = 0;
    char *end = NULL;
    const double value = g_ascii_strtod(token, &end);
    char canonical[G_ASCII_DTOSTR_BUF_SIZE];
    if (!end || *end != '\0' || !isfinite(value) ||
        (value == 0.0 && signbit(value)) ||
        !crashcar_format_canonical_json_double(value, canonical) ||
        strcmp(token, canonical) != 0) {
        return crashcar_json_fail(
          input, "JSON number is not unique canonical binary64");
    }
    input->cursor = cursor;
    *value_out = value;
    return TRUE;
}

static gboolean crashcar_json_parse_binary64(
  CrashcarJsonCursor *input,
  double *value_out) {
    if (!input || !value_out || !crashcar_json_expect(input, "\"")) {
        return FALSE;
    }
    const char *begin = input->cursor;
    const char *quote = memchr(begin, '"', (size_t)(input->end - begin));
    if (!quote || quote == begin || quote - begin >= 64) {
        return crashcar_json_fail(input, "binary64 JSON string is malformed");
    }
    char token[64];
    const size_t length = (size_t)(quote - begin);
    memcpy(token, begin, length);
    token[length] = '\0';

    size_t offset = token[0] == '-' ? 1u : 0u;
    if (length < offset + 20u || token[offset] != '0' ||
        token[offset + 1] != 'x' ||
        (token[offset + 2] != '0' && token[offset + 2] != '1') ||
        token[offset + 3] != '.') {
        return crashcar_json_fail(input, "binary64 string is not canonical hex");
    }
    for (size_t index = offset + 4; index < offset + 17; ++index) {
        if (!((token[index] >= '0' && token[index] <= '9') ||
              (token[index] >= 'a' && token[index] <= 'f'))) {
            return crashcar_json_fail(
              input, "binary64 fraction is not lowercase hex");
        }
    }
    if (token[offset + 17] != 'p' ||
        (token[offset + 18] != '+' && token[offset + 18] != '-')) {
        return crashcar_json_fail(input, "binary64 exponent is malformed");
    }
    const size_t exponent = offset + 19;
    if (exponent >= length ||
        (token[exponent] == '0' && exponent + 1 < length)) {
        return crashcar_json_fail(input, "binary64 exponent is not canonical");
    }
    for (size_t index = exponent; index < length; ++index) {
        if (token[index] < '0' || token[index] > '9') {
            return crashcar_json_fail(input, "binary64 exponent requires digits");
        }
    }

    errno = 0;
    char *end = NULL;
    const double value = g_ascii_strtod(token, &end);
    char canonical[64];
    /* ERANGE is permitted for an exactly represented subnormal; the
     * canonical round-trip below still rejects underflow-to-zero. */
    if (!end || *end != '\0' || !isfinite(value) ||
        (value == 0.0 && signbit(value)) ||
        !crashcar_format_canonical_binary64(value, canonical) ||
        strcmp(token, canonical) != 0) {
        return crashcar_json_fail(input, "binary64 value is not unique canonical");
    }
    input->cursor = quote + 1;
    *value_out = value;
    return TRUE;
}

static gboolean crashcar_split_normalized_ns(
  gint64 value_ns,
  gint64 *seconds_out,
  gint64 *nanoseconds_out) {
    if (!seconds_out || !nanoseconds_out) return FALSE;
    gint64 seconds = value_ns / CRASHCAR_NS_PER_SECOND;
    gint64 nanoseconds = value_ns % CRASHCAR_NS_PER_SECOND;
    if (nanoseconds < 0) {
        --seconds;
        nanoseconds += CRASHCAR_NS_PER_SECOND;
    }
    *seconds_out = seconds;
    *nanoseconds_out = nanoseconds;
    return TRUE;
}

static gboolean crashcar_append_gps_json(GString *output, gint64 gps_ns) {
    gint64 seconds = 0;
    gint64 nanoseconds = 0;
    if (!output || !crashcar_split_normalized_ns(
          gps_ns, &seconds, &nanoseconds)) {
        return FALSE;
    }
    g_string_append_printf(
      output,
      "{\"seconds\":%" G_GINT64_FORMAT
      ",\"nanoseconds\":%" G_GINT64_FORMAT "}",
      seconds, nanoseconds);
    return TRUE;
}

static gboolean crashcar_append_duration_json(
  GString *output,
  gint64 duration_ns) {
    if (!output || duration_ns < 0) return FALSE;
    return crashcar_append_gps_json(output, duration_ns);
}

static gboolean crashcar_json_parse_duration_ns(
  CrashcarJsonCursor *input,
  gint64 *duration_ns_out) {
    gint64 duration_ns = 0;
    if (!crashcar_json_parse_gps_ns(input, &duration_ns) || duration_ns < 0) {
        return crashcar_json_fail(input, "duration must be nonnegative");
    }
    *duration_ns_out = duration_ns;
    return TRUE;
}

static gint crashcar_compare_background_point(
  gconstpointer left_raw,
  gconstpointer right_raw) {
    const CrashcarBackgroundPoint *left = left_raw;
    const CrashcarBackgroundPoint *right = right_raw;
    if (left->llr < right->llr) return -1;
    if (left->llr > right->llr) return 1;
    if (left->gps_ns < right->gps_ns) return -1;
    if (left->gps_ns > right->gps_ns) return 1;
    guint64 left_far_bits = 0;
    guint64 right_far_bits = 0;
    memcpy(&left_far_bits, &left->far, sizeof(left_far_bits));
    memcpy(&right_far_bits, &right->far, sizeof(right_far_bits));
    return (left_far_bits > right_far_bits) -
           (left_far_bits < right_far_bits);
}

static GArray *crashcar_build_background_points(
  const GArray *support_points,
  gint64 livetime_ns,
  gchar **failure) {
    if (!support_points || support_points->len == 0 ||
        support_points->len > CRASHCAR_BACKGROUND_MAX_POINTS_PER_IFO ||
        livetime_ns <= 0 ||
        livetime_ns >= CRASHCAR_BINARY64_EXACT_INTEGER_LIMIT) {
        crashcar_set_failure(failure, "schema4 point/livetime bounds invalid");
        return NULL;
    }
    GArray *points = g_array_sized_new(
      FALSE, FALSE, sizeof(CrashcarBackgroundPoint), support_points->len);
    for (guint index = 0; index < support_points->len; ++index) {
        const CrashcarSupportPoint support =
          g_array_index((GArray *)support_points, CrashcarSupportPoint, index);
        if (!isfinite(support.rank)) {
            g_array_free(points, TRUE);
            crashcar_set_failure(failure, "schema4 support rank is nonfinite");
            return NULL;
        }
        const CrashcarBackgroundPoint point = {
          support.gps_ns, support.rank, 0.0
        };
        g_array_append_val(points, point);
    }
    g_array_sort(points, crashcar_compare_background_point);
    const double livetime_seconds =
      (double)livetime_ns / (double)CRASHCAR_NS_PER_SECOND;
    for (guint begin = 0; begin < points->len;) {
        const double llr =
          g_array_index(points, CrashcarBackgroundPoint, begin).llr;
        guint end = begin + 1;
        while (end < points->len &&
               g_array_index(points, CrashcarBackgroundPoint, end).llr == llr) {
            ++end;
        }
        const double far =
          (double)(points->len - begin) / livetime_seconds;
        if (!crashcar_far_double_is_valid(far)) {
            g_array_free(points, TRUE);
            crashcar_set_failure(failure, "schema4 Calculated FAR is invalid");
            return NULL;
        }
        for (guint index = begin; index < end; ++index) {
            g_array_index(points, CrashcarBackgroundPoint, index).far = far;
        }
        begin = end;
    }
    g_array_sort(points, crashcar_compare_background_point);
    return points;
}

static GString *crashcar_build_schema4_bytes(
  const CrashcarSingleFarEngine *element,
  guint64 version,
  gint64 window_start_ns,
  gint64 window_end_ns,
  const gint64 livetime_ns[2],
  GArray *const support_points[2],
  const double r_tail[2],
  const double tail_slope[2],
  const guint fit_unique_rank_count[2],
  gchar **failure) {
    if (!element || !element->background_binding_valid || version == 0 ||
        version > (guint64)G_MAXINT64 || window_start_ns >= window_end_ns ||
        !isfinite(element->tail_log10_far) ||
        !(element->tail_log10_far < 0.0) ||
        !support_points[0] || !support_points[1]) {
        crashcar_set_failure(failure, "schema4 candidate binding is invalid");
        return NULL;
    }
    GArray *points[2] = { NULL, NULL };
    for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
        points[ifo_id] = crashcar_build_background_points(
          support_points[ifo_id], livetime_ns[ifo_id], failure);
        if (!points[ifo_id]) {
            if (points[0]) g_array_free(points[0], TRUE);
            return NULL;
        }
    }
    if ((guint64)points[0]->len + (guint64)points[1]->len >
        CRASHCAR_BACKGROUND_MAX_POINTS_TOTAL) {
        g_array_free(points[0], TRUE);
        g_array_free(points[1], TRUE);
        crashcar_set_failure(failure, "schema4 total support bound exceeded");
        return NULL;
    }

    GString *output = g_string_sized_new(
      2048u + 192u * (points[0]->len + points[1]->len));
    g_string_append_printf(
      output,
      "{\"schema_version\":4,\"background_kind\":\"no_injection\""
      ",\"run_namespace_sha256\":\"%s\""
      ",\"source_manifest_sha256\":\"%s\""
      ",\"runtime_manifest_sha256\":\"%s\""
      ",\"config_sha256\":\"%s\""
      ",\"segment_xml_sha256\":\"%s\""
      ",\"segment_canonical_sha256\":\"%s\""
      ",\"template_shape_map_sha256\":\"%s\""
      ",\"worker_id\":%d,\"worker_count\":%d"
      ",\"worker_bank_ids\":[",
      element->run_namespace_sha256,
      element->source_manifest_sha256,
      element->runtime_manifest_sha256,
      element->config_sha256,
      element->background_segment_xml_sha256,
      element->background_segment_canonical_sha256,
      element->template_shape_map_sha256,
      element->worker_id, element->background_worker_count);
    for (guint index = 0; index < element->worker_bank_id_values->len; ++index) {
        if (index > 0) g_string_append_c(output, ',');
        g_string_append_printf(
          output, "%d",
          g_array_index(element->worker_bank_id_values, int, index));
    }
    g_string_append_printf(
      output,
      "],\"accepted_version\":%" G_GUINT64_FORMAT
      ",\"epoch_gps\":",
      version);
    crashcar_append_gps_json(output, window_end_ns);
    g_string_append(output, ",\"window_start_gps\":");
    crashcar_append_gps_json(output, window_start_ns);
    g_string_append(output, ",\"window_end_gps\":");
    crashcar_append_gps_json(output, window_end_ns);
    g_string_append(output, ",\"window_duration\":");
    crashcar_append_duration_json(output, element->background_window_ns);
    g_string_append(output, ",\"update_period\":");
    crashcar_append_duration_json(output, element->background_update_ns);
    char tail_log10_far_text[G_ASCII_DTOSTR_BUF_SIZE];
    if (!crashcar_format_canonical_json_double(
          element->tail_log10_far, tail_log10_far_text)) {
        g_array_free(points[0], TRUE);
        g_array_free(points[1], TRUE);
        g_string_free(output, TRUE);
        crashcar_set_failure(
          failure, "schema4 tail anchor is noncanonical");
        return NULL;
    }
    g_string_append_printf(
      output,
      ",\"far_floor_count\":1,\"tail_log10_far\":%s"
      ",\"backgrounds\":{",
      tail_log10_far_text);

    for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
        if (ifo_id > 0) g_string_append_c(output, ',');
        g_string_append_printf(
          output, "\"%s\":{\"livetime\":", ifo_id == 0 ? "H1" : "L1");
        crashcar_append_duration_json(output, livetime_ns[ifo_id]);
        g_string_append_printf(
          output,
          ",\"support_count\":%u,\"tail_fit\":{"
          "\"method\":\"anchored_ols_all_unique_ranks_ge_r_tail\""
          ",\"r_tail\":\"",
          points[ifo_id]->len);
        char r_tail_text[64];
        char slope_text[64];
        if (!crashcar_format_canonical_binary64(
              r_tail[ifo_id], r_tail_text) ||
            !crashcar_format_canonical_binary64(
              tail_slope[ifo_id], slope_text)) {
            g_array_free(points[0], TRUE);
            g_array_free(points[1], TRUE);
            g_string_free(output, TRUE);
            crashcar_set_failure(failure, "schema4 tail values are noncanonical");
            return NULL;
        }
        g_string_append_printf(
          output,
          "%s\",\"slope\":\"%s\",\"fit_unique_rank_count\":%u}"
          ",\"far_llr_points\":[",
          r_tail_text, slope_text, fit_unique_rank_count[ifo_id]);
        for (guint index = 0; index < points[ifo_id]->len; ++index) {
            const CrashcarBackgroundPoint point =
              g_array_index(points[ifo_id], CrashcarBackgroundPoint, index);
            char llr_text[64];
            char far_text[64];
            if (!crashcar_format_canonical_binary64(point.llr, llr_text) ||
                !crashcar_format_canonical_binary64(point.far, far_text)) {
                g_array_free(points[0], TRUE);
                g_array_free(points[1], TRUE);
                g_string_free(output, TRUE);
                crashcar_set_failure(
                  failure, "schema4 point values are noncanonical");
                return NULL;
            }
            if (index > 0) g_string_append_c(output, ',');
            g_string_append(output, "{\"gps\":");
            crashcar_append_gps_json(output, point.gps_ns);
            g_string_append_printf(
              output,
              ",\"llr\":\"%s\",\"far\":\"%s\"}",
              llr_text, far_text);
        }
        g_string_append(output, "]}");
    }
    g_string_append(output, "}}\n");
    g_array_free(points[0], TRUE);
    g_array_free(points[1], TRUE);
    if (output->len > CRASHCAR_BACKGROUND_JSON_MAX_BYTES) {
        g_string_free(output, TRUE);
        crashcar_set_failure(failure, "schema4 canonical bytes exceed limit");
        return NULL;
    }
    return output;
}

static gboolean crashcar_parse_schema4_worker_banks(
  CrashcarJsonCursor *input,
  const CrashcarSingleFarEngine *element) {
    if (!input || !element || !element->worker_bank_id_values ||
        element->worker_bank_id_values->len == 0 ||
        !crashcar_json_expect(input, "[")) {
        return FALSE;
    }
    for (guint index = 0;
         index < element->worker_bank_id_values->len;
         ++index) {
        if (index > 0 && !crashcar_json_expect(input, ",")) return FALSE;
        gint64 bank_id = -1;
        if (!crashcar_parse_canonical_nonnegative_int64(input, &bank_id) ||
            bank_id != g_array_index(
              element->worker_bank_id_values, int, index)) {
            return crashcar_json_fail(
              input, "schema4 worker bank roster mismatch");
        }
    }
    return crashcar_json_expect(input, "]");
}

static gboolean crashcar_parse_schema4_ifo(
  CrashcarJsonCursor *input,
  gint64 window_start_ns,
  gint64 window_end_ns,
  gint64 window_duration_ns,
  double tail_log10_far,
  CrashcarParsedBackground *background,
  int ifo_id) {
    gint64 livetime_ns = 0;
    gint64 support_count = 0;
    gint64 fit_count = 0;
    double stored_r_tail = NAN;
    double stored_slope = NAN;
    if (!input || !background || ifo_id < 0 || ifo_id > 1 ||
        !crashcar_json_expect(input, "{\"livetime\":") ||
        !crashcar_json_parse_duration_ns(input, &livetime_ns) ||
        !crashcar_json_expect(input, ",\"support_count\":") ||
        !crashcar_parse_canonical_nonnegative_int64(
          input, &support_count) ||
        support_count > CRASHCAR_BACKGROUND_MAX_POINTS_PER_IFO ||
        !crashcar_json_expect(
          input,
          ",\"tail_fit\":{\"method\":"
          "\"anchored_ols_all_unique_ranks_ge_r_tail\""
          ",\"r_tail\":") ||
        !crashcar_json_parse_binary64(input, &stored_r_tail) ||
        !crashcar_json_expect(input, ",\"slope\":") ||
        !crashcar_json_parse_binary64(input, &stored_slope) ||
        !(stored_slope < 0.0) ||
        !crashcar_json_expect(
          input, ",\"fit_unique_rank_count\":") ||
        !crashcar_parse_canonical_nonnegative_int64(input, &fit_count) ||
        fit_count < 2 || fit_count > support_count ||
        !crashcar_json_expect(input, "},\"far_llr_points\":[")) {
        return FALSE;
    }
    if (livetime_ns <= 0 || livetime_ns > window_duration_ns ||
        livetime_ns >= CRASHCAR_BINARY64_EXACT_INTEGER_LIMIT ||
        (guint64)livetime_ns * 5u <= (guint64)window_duration_ns ||
        support_count <= 0) {
        return crashcar_json_fail(
          input, "schema4 detector occupancy/support is invalid");
    }

    GArray *points = g_array_sized_new(
      FALSE, FALSE, sizeof(CrashcarBackgroundPoint), (guint)support_count);
    for (gint64 index = 0; index < support_count; ++index) {
        if (index > 0 && !crashcar_json_expect(input, ",")) {
            g_array_free(points, TRUE);
            return FALSE;
        }
        CrashcarBackgroundPoint point = { 0, NAN, NAN };
        if (!crashcar_json_expect(input, "{\"gps\":") ||
            !crashcar_json_parse_gps_ns(input, &point.gps_ns) ||
            !crashcar_json_expect(input, ",\"llr\":") ||
            !crashcar_json_parse_binary64(input, &point.llr) ||
            !crashcar_json_expect(input, ",\"far\":") ||
            !crashcar_json_parse_binary64(input, &point.far) ||
            !crashcar_json_expect(input, "}")) {
            g_array_free(points, TRUE);
            return FALSE;
        }
        if (point.gps_ns < window_start_ns ||
            point.gps_ns >= window_end_ns ||
            !crashcar_far_double_is_valid(point.far)) {
            g_array_free(points, TRUE);
            return crashcar_json_fail(
              input, "schema4 point GPS/FAR is invalid");
        }
        if (points->len > 0) {
            const CrashcarBackgroundPoint previous =
              g_array_index(points, CrashcarBackgroundPoint, points->len - 1);
            if (crashcar_compare_background_point(&previous, &point) > 0) {
                g_array_free(points, TRUE);
                return crashcar_json_fail(
                  input, "schema4 points are not canonically sorted");
            }
        }
        g_array_append_val(points, point);
    }
    if (!crashcar_json_expect(input, "]}")) {
        g_array_free(points, TRUE);
        return FALSE;
    }

    const double livetime_seconds =
      (double)livetime_ns / (double)CRASHCAR_NS_PER_SECOND;
    GArray *ranks = g_array_sized_new(
      FALSE, FALSE, sizeof(double), points->len);
    double previous_far = INFINITY;
    for (guint begin = 0; begin < points->len;) {
        const double llr =
          g_array_index(points, CrashcarBackgroundPoint, begin).llr;
        guint end = begin + 1;
        while (end < points->len &&
               g_array_index(points, CrashcarBackgroundPoint, end).llr == llr) {
            ++end;
        }
        const double expected_far =
          (double)(points->len - begin) / livetime_seconds;
        if (!crashcar_far_double_is_valid(expected_far) ||
            expected_far > previous_far) {
            g_array_free(ranks, TRUE);
            g_array_free(points, TRUE);
            return crashcar_json_fail(
              input, "schema4 Calculated FAR monotonicity is invalid");
        }
        for (guint index = begin; index < end; ++index) {
            const CrashcarBackgroundPoint point =
              g_array_index(points, CrashcarBackgroundPoint, index);
            if (!crashcar_binary64_bits_equal(point.far, expected_far)) {
                g_array_free(ranks, TRUE);
                g_array_free(points, TRUE);
                return crashcar_json_fail(
                  input, "schema4 Calculated FAR bit mismatch");
            }
            g_array_append_val(ranks, point.llr);
        }
        previous_far = expected_far;
        begin = end;
    }

    double recomputed_r_tail = NAN;
    double recomputed_slope = NAN;
    guint recomputed_fit_count = 0;
    if (!crashcar_authority_tail_metrics(
          (const double *)ranks->data, ranks->len,
          livetime_seconds, tail_log10_far,
          &recomputed_r_tail, &recomputed_slope,
          &recomputed_fit_count) ||
        !crashcar_binary64_bits_equal(stored_r_tail, recomputed_r_tail) ||
        !crashcar_binary64_bits_equal(stored_slope, recomputed_slope) ||
        (guint)fit_count != recomputed_fit_count) {
        g_array_free(ranks, TRUE);
        g_array_free(points, TRUE);
        return crashcar_json_fail(input, "schema4 tail fit bit mismatch");
    }

    background->livetime_ns[ifo_id] = livetime_ns;
    background->points[ifo_id] = points;
    background->ranks[ifo_id] = ranks;
    background->r_tail[ifo_id] = stored_r_tail;
    background->tail_slope[ifo_id] = stored_slope;
    background->fit_unique_rank_count[ifo_id] = (guint)fit_count;
    return TRUE;
}

static gboolean crashcar_parse_schema4_background(
  const CrashcarSingleFarEngine *element,
  const gchar *bytes,
  gsize size,
  CrashcarParsedBackground *background,
  gchar **failure) {
    if (!element || !bytes || !background || size < 3 ||
        size > CRASHCAR_BACKGROUND_JSON_MAX_BYTES ||
        bytes[size - 1] != '\n' ||
        memchr(bytes, '\0', size) != NULL ||
        memchr(bytes, '\r', size) != NULL ||
        memchr(bytes, '\n', size - 1) != NULL ||
        !g_utf8_validate(bytes, size, NULL)) {
        return crashcar_set_failure(
          failure, "schema4 bytes/newline/UTF-8 bounds invalid");
    }
    memset(background, 0, sizeof(*background));
    CrashcarJsonCursor input = { bytes, bytes + size - 1, NULL };
    gint64 schema_version = 0;
    char run_namespace[CRASHCAR_SHA256_HEX_LENGTH + 1] = { 0 };
    char source_manifest[CRASHCAR_SHA256_HEX_LENGTH + 1] = { 0 };
    char runtime_manifest[CRASHCAR_SHA256_HEX_LENGTH + 1] = { 0 };
    char config[CRASHCAR_SHA256_HEX_LENGTH + 1] = { 0 };
    char segment_xml[CRASHCAR_SHA256_HEX_LENGTH + 1] = { 0 };
    char segment_canonical[CRASHCAR_SHA256_HEX_LENGTH + 1] = { 0 };
    char template_map[CRASHCAR_SHA256_HEX_LENGTH + 1] = { 0 };
    gint64 worker_id = -1;
    gint64 worker_count = 0;
    gint64 accepted_version = 0;
    gint64 epoch_ns = 0;
    gint64 window_start_ns = 0;
    gint64 window_end_ns = 0;
    gint64 window_duration_ns = 0;
    gint64 update_period_ns = 0;
    gint64 floor_count = 0;
    double tail_log_far = NAN;

    gboolean valid =
      crashcar_json_expect(&input, "{\"schema_version\":") &&
      crashcar_parse_canonical_nonnegative_int64(
        &input, &schema_version) &&
      schema_version == CRASHCAR_BACKGROUND_JSON_SCHEMA_VERSION &&
      crashcar_json_expect(
        &input, ",\"background_kind\":\"no_injection\""
                ",\"run_namespace_sha256\":") &&
      crashcar_json_parse_sha256(&input, run_namespace) &&
      crashcar_json_expect(&input, ",\"source_manifest_sha256\":") &&
      crashcar_json_parse_sha256(&input, source_manifest) &&
      crashcar_json_expect(&input, ",\"runtime_manifest_sha256\":") &&
      crashcar_json_parse_sha256(&input, runtime_manifest) &&
      crashcar_json_expect(&input, ",\"config_sha256\":") &&
      crashcar_json_parse_sha256(&input, config) &&
      crashcar_json_expect(&input, ",\"segment_xml_sha256\":") &&
      crashcar_json_parse_sha256(&input, segment_xml) &&
      crashcar_json_expect(&input, ",\"segment_canonical_sha256\":") &&
      crashcar_json_parse_sha256(&input, segment_canonical) &&
      crashcar_json_expect(&input, ",\"template_shape_map_sha256\":") &&
      crashcar_json_parse_sha256(&input, template_map) &&
      crashcar_json_expect(&input, ",\"worker_id\":") &&
      crashcar_parse_canonical_nonnegative_int64(&input, &worker_id) &&
      crashcar_json_expect(&input, ",\"worker_count\":") &&
      crashcar_parse_canonical_nonnegative_int64(&input, &worker_count) &&
      crashcar_json_expect(&input, ",\"worker_bank_ids\":") &&
      crashcar_parse_schema4_worker_banks(&input, element) &&
      crashcar_json_expect(&input, ",\"accepted_version\":") &&
      crashcar_parse_canonical_nonnegative_int64(
        &input, &accepted_version) &&
      crashcar_json_expect(&input, ",\"epoch_gps\":") &&
      crashcar_json_parse_gps_ns(&input, &epoch_ns) &&
      crashcar_json_expect(&input, ",\"window_start_gps\":") &&
      crashcar_json_parse_gps_ns(&input, &window_start_ns) &&
      crashcar_json_expect(&input, ",\"window_end_gps\":") &&
      crashcar_json_parse_gps_ns(&input, &window_end_ns) &&
      crashcar_json_expect(&input, ",\"window_duration\":") &&
      crashcar_json_parse_duration_ns(&input, &window_duration_ns) &&
      crashcar_json_expect(&input, ",\"update_period\":") &&
      crashcar_json_parse_duration_ns(&input, &update_period_ns) &&
      crashcar_json_expect(&input, ",\"far_floor_count\":") &&
      crashcar_parse_canonical_nonnegative_int64(&input, &floor_count) &&
      crashcar_json_expect(&input, ",\"tail_log10_far\":") &&
      crashcar_json_parse_canonical_double_number(
        &input, &tail_log_far) &&
      crashcar_json_expect(&input, ",\"backgrounds\":{\"H1\":") &&
      crashcar_parse_schema4_ifo(
        &input, window_start_ns, window_end_ns, window_duration_ns,
        tail_log_far, background, 0) &&
      crashcar_json_expect(&input, ",\"L1\":") &&
      crashcar_parse_schema4_ifo(
        &input, window_start_ns, window_end_ns, window_duration_ns,
        tail_log_far, background, 1) &&
      crashcar_json_expect(&input, "}}") && input.cursor == input.end;

    guint64 window_span_ns = 0;
    gint64 first_epoch_ns = 0;
    guint64 epoch_offset_ns = 0;
    valid = valid &&
      strcmp(run_namespace, element->run_namespace_sha256) == 0 &&
      strcmp(source_manifest, element->source_manifest_sha256) == 0 &&
      strcmp(runtime_manifest, element->runtime_manifest_sha256) == 0 &&
      strcmp(config, element->config_sha256) == 0 &&
      strcmp(segment_xml, element->background_segment_xml_sha256) == 0 &&
      strcmp(segment_canonical,
             element->background_segment_canonical_sha256) == 0 &&
      strcmp(template_map, element->template_shape_map_sha256) == 0 &&
      worker_id == element->worker_id &&
      worker_count == element->background_worker_count &&
      accepted_version >= 1 && accepted_version <= G_MAXINT64 &&
      epoch_ns == window_end_ns &&
      window_duration_ns == element->background_window_ns &&
      update_period_ns == element->background_update_ns &&
      window_duration_ns > 0 && update_period_ns > 0 &&
      floor_count == 1 && isfinite(tail_log_far) &&
      tail_log_far < 0.0 &&
      crashcar_binary64_bits_equal(
        tail_log_far, element->tail_log10_far) &&
      crashcar_ordered_distance_u64(
        window_start_ns, window_end_ns, &window_span_ns) &&
      window_span_ns == (guint64)window_duration_ns &&
      crashcar_add_nonnegative_offset(
        element->background_origin_gps_ns,
        (guint64)window_duration_ns, &first_epoch_ns) &&
      epoch_ns >= first_epoch_ns &&
      crashcar_ordered_distance_u64(
        first_epoch_ns, epoch_ns, &epoch_offset_ns) &&
      epoch_offset_ns % (guint64)update_period_ns == 0 &&
      background->points[0] && background->points[1] &&
      (guint64)background->points[0]->len +
        (guint64)background->points[1]->len <=
          CRASHCAR_BACKGROUND_MAX_POINTS_TOTAL;

    if (!valid) {
        if (failure && !*failure) {
            *failure = input.failure
              ? g_strdup(input.failure)
              : g_strdup("schema4 binding/invariant mismatch");
        }
        g_free(input.failure);
        crashcar_parsed_background_clear(background);
        return FALSE;
    }
    g_free(input.failure);
    background->version = (guint64)accepted_version;
    background->tail_log10_far = tail_log_far;
    background->epoch_gps_ns = epoch_ns;
    background->window_start_gps_ns = window_start_ns;
    background->window_end_gps_ns = window_end_ns;
    return TRUE;
}

static gboolean crashcar_write_all_fd(
  int fd,
  const gchar *bytes,
  gsize size,
  gchar **failure) {
    gsize offset = 0;
    while (offset < size) {
        const ssize_t amount = write(fd, bytes + offset, size - offset);
        if (amount < 0 && errno == EINTR) continue;
        if (amount <= 0) {
            return crashcar_set_failure(
              failure, "schema4 atomic write was incomplete");
        }
        offset += (gsize)amount;
    }
    return TRUE;
}

static gboolean crashcar_stat_snapshot_equal(
  const struct stat *left,
  const struct stat *right) {
    return left && right &&
      left->st_dev == right->st_dev &&
      left->st_ino == right->st_ino &&
      left->st_mode == right->st_mode &&
      left->st_size == right->st_size &&
      left->st_mtim.tv_sec == right->st_mtim.tv_sec &&
      left->st_mtim.tv_nsec == right->st_mtim.tv_nsec;
}

static gboolean crashcar_read_schema4_file(
  const char *path,
  const char *expected_sha256,
  gchar **bytes_out,
  gsize *size_out,
  char actual_sha256[CRASHCAR_SHA256_HEX_LENGTH + 1],
  gchar **failure) {
    if (!path || !bytes_out || !size_out || !actual_sha256) {
        return crashcar_set_failure(failure, "schema4 read arguments invalid");
    }
    *bytes_out = NULL;
    *size_out = 0;
    actual_sha256[0] = 0;

    struct stat path_before;
    struct stat fd_before;
    struct stat fd_after;
    struct stat path_after;
    if (lstat(path, &path_before) != 0 ||
        !S_ISREG(path_before.st_mode) ||
        (path_before.st_mode & 0777) != 0444 ||
        path_before.st_size <= 0 ||
        (guint64)path_before.st_size > CRASHCAR_BACKGROUND_JSON_MAX_BYTES) {
        return crashcar_set_failure(
          failure,
          "schema4 path is not a bounded mode-0444 regular non-symlink file");
    }

    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) {
        return crashcar_set_failure(
          failure, "cannot open schema4 background with O_NOFOLLOW");
    }
    if (fstat(fd, &fd_before) != 0 ||
        !crashcar_stat_snapshot_equal(&path_before, &fd_before)) {
        close(fd);
        return crashcar_set_failure(
          failure, "schema4 path changed between lstat and open");
    }

    const gsize size = (gsize)fd_before.st_size;
    gchar *bytes = g_malloc(size + 1);
    gsize offset = 0;
    while (offset < size) {
        const ssize_t amount = read(fd, bytes + offset, size - offset);
        if (amount < 0 && errno == EINTR) continue;
        if (amount <= 0) {
            g_free(bytes);
            close(fd);
            return crashcar_set_failure(
              failure, "schema4 background read was incomplete");
        }
        offset += (gsize)amount;
    }
    const int after_status = fstat(fd, &fd_after);
    const int close_status = close(fd);
    const int path_after_status = lstat(path, &path_after);
    if (after_status != 0 || close_status != 0 ||
        path_after_status != 0 ||
        !crashcar_stat_snapshot_equal(&fd_before, &fd_after) ||
        !crashcar_stat_snapshot_equal(&path_before, &path_after) ||
        !S_ISREG(path_after.st_mode)) {
        g_free(bytes);
        return crashcar_set_failure(
          failure,
          "schema4 background inode/size/mtime changed during stable read");
    }

    bytes[size] = 0;
    gchar *digest = g_compute_checksum_for_data(
      G_CHECKSUM_SHA256, (const guchar *)bytes, size);
    if (!crashcar_sha256_is_lowercase64(digest) ||
        (expected_sha256 &&
         (!crashcar_sha256_is_lowercase64(expected_sha256) ||
          strcmp(digest, expected_sha256) != 0))) {
        g_free(digest);
        g_free(bytes);
        return crashcar_set_failure(
          failure, "schema4 background file sha256 mismatch");
    }
    g_strlcpy(actual_sha256, digest, CRASHCAR_SHA256_HEX_LENGTH + 1);
    g_free(digest);
    *bytes_out = bytes;
    *size_out = size;
    return TRUE;
}

static gboolean crashcar_publish_schema4_candidate(
  CrashcarSingleFarEngine *element,
  guint64 version,
  gint64 window_start_ns,
  gint64 window_end_ns,
  const gint64 livetime_ns[2],
  GArray *const support_points[2],
  const double r_tail[2],
  const double tail_slope[2],
  const guint fit_unique_rank_count[2],
  CrashcarParsedBackground *published,
  gboolean *unrecoverable_out,
  gchar **failure) {
    if (unrecoverable_out) *unrecoverable_out = FALSE;
    if (!element || element->live_single_background_readonly ||
        !element->background_json_fname || !published ||
        !unrecoverable_out) {
        return crashcar_set_failure(
          failure, "schema4 publication is disabled for this mode");
    }
    GString *canonical = crashcar_build_schema4_bytes(
      element, version, window_start_ns, window_end_ns,
      livetime_ns, support_points, r_tail, tail_slope,
      fit_unique_rank_count, failure);
    if (!canonical) return FALSE;

    CrashcarParsedBackground prevalidated = { 0 };
    if (!crashcar_parse_schema4_background(
          element, canonical->str, canonical->len,
          &prevalidated, failure) ||
        prevalidated.version != version ||
        prevalidated.window_start_gps_ns != window_start_ns ||
        prevalidated.window_end_gps_ns != window_end_ns) {
        crashcar_parsed_background_clear(&prevalidated);
        g_string_free(canonical, TRUE);
        if (failure && !*failure) {
            *failure = g_strdup("schema4 prepublication validation mismatch");
        }
        return FALSE;
    }
    crashcar_parsed_background_clear(&prevalidated);

    gchar *directory = g_path_get_dirname(element->background_json_fname);
    gchar *basename = g_path_get_basename(element->background_json_fname);
    if (!directory || !basename || basename[0] == '\0' ||
        strcmp(basename, ".") == 0 || strcmp(basename, "..") == 0 ||
        strlen(basename) > 128) {
        g_free(directory);
        g_free(basename);
        g_string_free(canonical, TRUE);
        return crashcar_set_failure(
          failure, "schema4 publication path is invalid");
    }
    int directory_fd = open(
      directory, O_RDONLY | O_CLOEXEC | O_DIRECTORY | O_NOFOLLOW);
    if (directory_fd < 0) {
        g_free(directory);
        g_free(basename);
        g_string_free(canonical, TRUE);
        return crashcar_set_failure(
          failure, "schema4 publication directory cannot be opened safely");
    }

    static guint64 publication_counter = 0;
    ++publication_counter;
    char temporary[256];
    char backup[256];
    g_snprintf(
      temporary, sizeof(temporary), ".%s.tmp.%ld.%" G_GUINT64_FORMAT,
      basename, (long)getpid(), publication_counter);
    g_snprintf(
      backup, sizeof(backup), ".%s.old.%ld.%" G_GUINT64_FORMAT,
      basename, (long)getpid(), publication_counter);
    gboolean valid = strlen(temporary) < sizeof(temporary) - 1 &&
      strlen(backup) < sizeof(backup) - 1;
    gboolean temporary_exists = FALSE;
    gboolean backup_exists = FALSE;
    gboolean renamed = FALSE;
    int temporary_fd = -1;

    if (valid) {
        temporary_fd = openat(
          directory_fd, temporary,
          O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC | O_NOFOLLOW, 0600);
        valid = temporary_fd >= 0;
        temporary_exists = valid;
    }
    if (valid) {
        valid = crashcar_write_all_fd(
          temporary_fd, canonical->str, canonical->len, failure) &&
          fchmod(temporary_fd, 0444) == 0 &&
          fsync(temporary_fd) == 0 && close(temporary_fd) == 0;
        temporary_fd = -1;
    }

    struct stat old_status;
    if (valid && fstatat(
          directory_fd, basename, &old_status, AT_SYMLINK_NOFOLLOW) == 0) {
        valid = S_ISREG(old_status.st_mode) &&
          (old_status.st_mode & 0777) == 0444 &&
          linkat(directory_fd, basename, directory_fd, backup, 0) == 0;
        backup_exists = valid;
    } else if (valid && errno != ENOENT) {
        valid = FALSE;
    }
    if (valid) {
        valid = renameat(
          directory_fd, temporary, directory_fd, basename) == 0;
        if (valid) {
            temporary_exists = FALSE;
            renamed = TRUE;
            valid = fsync(directory_fd) == 0;
        }
    }

    gchar *reopened_bytes = NULL;
    gsize reopened_size = 0;
    char reopened_sha[CRASHCAR_SHA256_HEX_LENGTH + 1] = { 0 };
    if (valid) {
        valid = crashcar_read_schema4_file(
          element->background_json_fname, NULL,
          &reopened_bytes, &reopened_size, reopened_sha, failure) &&
          reopened_size == canonical->len &&
          memcmp(reopened_bytes, canonical->str, canonical->len) == 0 &&
          crashcar_parse_schema4_background(
            element, reopened_bytes, reopened_size, published, failure) &&
          published->version == version &&
          published->window_start_gps_ns == window_start_ns &&
          published->window_end_gps_ns == window_end_ns;
    }
    if (valid) {
        g_strlcpy(
          published->file_sha256, reopened_sha,
          sizeof(published->file_sha256));
        /* Durable target revalidation is the commit point.  Backup cleanup
         * is best-effort after commit: it must never turn a valid new file
         * into a failure after the old hard link has been removed. */
        if (backup_exists &&
            unlinkat(directory_fd, backup, 0) == 0) {
            backup_exists = FALSE;
            (void)fsync(directory_fd);
        }
    }

    if (!valid) {
        crashcar_parsed_background_clear(published);
        if (renamed) {
            gboolean restored = FALSE;
            if (backup_exists) {
                if (renameat(
                      directory_fd, backup, directory_fd, basename) == 0) {
                    backup_exists = FALSE;
                    restored = fsync(directory_fd) == 0;
                }
            } else if (unlinkat(directory_fd, basename, 0) == 0) {
                restored = fsync(directory_fd) == 0;
            }
            if (restored) {
                renamed = FALSE;
            } else {
                *unrecoverable_out = TRUE;
            }
        }
        if (failure && !*failure) {
            *failure = g_strdup(
              "schema4 atomic publication or durable revalidation failed");
        }
    }
    if (temporary_fd >= 0) close(temporary_fd);
    if (temporary_exists) unlinkat(directory_fd, temporary, 0);
    /* A failed rollback intentionally leaves the hard-link backup for
     * operator recovery instead of deleting the prior valid authority. */
    if (backup_exists && !renamed) unlinkat(directory_fd, backup, 0);
    g_free(reopened_bytes);
    close(directory_fd);
    g_free(directory);
    g_free(basename);
    g_string_free(canonical, TRUE);
    return valid;
}

static void crashcar_live_record_refresh(
  CrashcarSingleFarEngine *element,
  CrashcarLiveRefreshStatus status,
  const char *reason,
  guint64 candidate_version,
  gint64 candidate_coverage_gps_ns,
  const char *candidate_sha256) {
    if (!element) return;
    element->live_last_refresh_status = status;
    if (status == CRASHCAR_LIVE_REFRESH_REJECTED_READ ||
        status == CRASHCAR_LIVE_REFRESH_REJECTED_SCHEMA ||
        status == CRASHCAR_LIVE_REFRESH_REJECTED_VERSION ||
        status == CRASHCAR_LIVE_REFRESH_REJECTED_FUTURE) {
        if (element->live_refresh_reject_count < G_MAXUINT64) {
            ++element->live_refresh_reject_count;
        }
    }
    g_strlcpy(
      element->live_last_reject_reason,
      reason ? reason : "",
      sizeof(element->live_last_reject_reason));
    element->live_last_candidate_version = candidate_version;
    element->live_last_candidate_coverage_gps_ns =
      candidate_coverage_gps_ns;
    if (crashcar_sha256_is_lowercase64(candidate_sha256)) {
        g_strlcpy(
          element->live_last_candidate_sha256,
          candidate_sha256,
          sizeof(element->live_last_candidate_sha256));
    } else {
        element->live_last_candidate_sha256[0] = 0;
    }
}

static gboolean crashcar_live_refresh_due(
  const CrashcarSingleFarEngine *element,
  gint64 event_gps_ns) {
    if (!element || !element->live_single_background_readonly ||
        event_gps_ns <= 0 || element->background_update_ns <= 0) {
        return FALSE;
    }
    if (element->live_last_refresh_attempt_gps_ns == G_MININT64) {
        return TRUE;
    }
    if (event_gps_ns <= element->live_last_refresh_attempt_gps_ns) {
        return FALSE;
    }
    guint64 elapsed_ns = 0;
    return crashcar_ordered_distance_u64(
             element->live_last_refresh_attempt_gps_ns,
             event_gps_ns, &elapsed_ns) &&
      elapsed_ns >= (guint64)element->background_update_ns;
}

static gboolean crashcar_live_candidate_valid(
  const CrashcarParsedBackground *candidate) {
    if (!candidate || candidate->version == 0 ||
        candidate->version > (guint64)G_MAXINT64 ||
        !isfinite(candidate->tail_log10_far) ||
        !(candidate->tail_log10_far < 0.0) ||
        candidate->epoch_gps_ns <= 0 ||
        candidate->epoch_gps_ns != candidate->window_end_gps_ns ||
        candidate->window_start_gps_ns >= candidate->window_end_gps_ns ||
        !crashcar_sha256_is_lowercase64(candidate->file_sha256)) {
        return FALSE;
    }
    for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
        if (!candidate->points[ifo_id] || !candidate->ranks[ifo_id] ||
            candidate->points[ifo_id]->len == 0 ||
            candidate->points[ifo_id]->len !=
              candidate->ranks[ifo_id]->len ||
            candidate->ranks[ifo_id]->len >
              CRASHCAR_BACKGROUND_MAX_POINTS_PER_IFO ||
            candidate->livetime_ns[ifo_id] <= 0 ||
            candidate->livetime_ns[ifo_id] >=
              CRASHCAR_BINARY64_EXACT_INTEGER_LIMIT ||
            !isfinite(candidate->r_tail[ifo_id]) ||
            !isfinite(candidate->tail_slope[ifo_id]) ||
            candidate->tail_slope[ifo_id] >= 0.0 ||
            candidate->fit_unique_rank_count[ifo_id] < 2) {
            return FALSE;
        }
    }
    return TRUE;
}

static gboolean crashcar_live_adopt_candidate(
  CrashcarSingleFarEngine *element,
  const CrashcarParsedBackground *candidate,
  gint64 event_gps_ns) {
    if (!element || !candidate ||
        !crashcar_live_candidate_valid(candidate) ||
        !crashcar_live_coverage_is_eligible(
          element, candidate->window_end_gps_ns, event_gps_ns)) {
        return FALSE;
    }
    for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
        CrashcarCompletedAuthorityIfo *snapshot =
          &element->completed_authority[ifo_id];
        if (!snapshot->ranks) return FALSE;
    }

    /*
     * Candidate bytes and every scientific field were validated in temporary
     * storage.  This element's transform callback is serialized, so filling
     * both detector arrays and publishing live_lkg_valid last is one
     * unobservable state transition for row scoring.
     */
    for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
        CrashcarCompletedAuthorityIfo *snapshot =
          &element->completed_authority[ifo_id];
        g_array_set_size(snapshot->ranks, 0);
        g_array_append_vals(
          snapshot->ranks,
          candidate->ranks[ifo_id]->data,
          candidate->ranks[ifo_id]->len);
        snapshot->valid = TRUE;
        snapshot->version = candidate->version;
        snapshot->epoch_gps_ns = candidate->epoch_gps_ns;
        snapshot->window_start_gps_ns =
          candidate->window_start_gps_ns;
        snapshot->window_end_gps_ns = candidate->window_end_gps_ns;
        snapshot->livetime_ns = candidate->livetime_ns[ifo_id];
    }
    element->live_lkg_version = candidate->version;
    element->live_lkg_epoch_gps_ns = candidate->epoch_gps_ns;
    element->live_lkg_window_start_gps_ns =
      candidate->window_start_gps_ns;
    element->live_lkg_window_end_gps_ns =
      candidate->window_end_gps_ns;
    element->live_last_refresh_success_gps_ns = event_gps_ns;
    g_strlcpy(
      element->background_file_sha256,
      candidate->file_sha256,
      sizeof(element->background_file_sha256));
    element->live_lkg_valid = TRUE;
    return TRUE;
}

static void crashcar_try_refresh_live_authority(
  CrashcarSingleFarEngine *element,
  gint64 event_gps_ns) {
    if (!crashcar_live_refresh_due(element, event_gps_ns)) return;
    element->live_last_refresh_attempt_gps_ns = event_gps_ns;

    gchar *bytes = NULL;
    gsize size = 0;
    char file_sha[CRASHCAR_SHA256_HEX_LENGTH + 1] = { 0 };
    gchar *failure = NULL;
    if (!crashcar_read_schema4_file(
          element->background_json_fname, NULL,
          &bytes, &size, file_sha, &failure)) {
        crashcar_live_record_refresh(
          element, CRASHCAR_LIVE_REFRESH_REJECTED_READ,
          failure ? failure : "stable schema4 read failed",
          0, 0, NULL);
        GST_WARNING_OBJECT(element->owner,
          "live single refresh rejected read worker=%d event_gps_ns=%"
          G_GINT64_FORMAT " lkg_version=%" G_GUINT64_FORMAT
          " reason=%s",
          element->worker_id, event_gps_ns, element->live_lkg_version,
          failure ? failure : "stable schema4 read failed");
        g_free(failure);
        return;
    }

    CrashcarParsedBackground candidate = { 0 };
    if (!crashcar_parse_schema4_background(
          element, bytes, size, &candidate, &failure)) {
        crashcar_live_record_refresh(
          element, CRASHCAR_LIVE_REFRESH_REJECTED_SCHEMA,
          failure ? failure : "schema4 candidate validation failed",
          0, 0, file_sha);
        GST_WARNING_OBJECT(element->owner,
          "live single refresh rejected schema worker=%d event_gps_ns=%"
          G_GINT64_FORMAT " candidate_sha=%s lkg_version=%"
          G_GUINT64_FORMAT " reason=%s",
          element->worker_id, event_gps_ns, file_sha,
          element->live_lkg_version,
          failure ? failure : "schema4 candidate validation failed");
        g_free(failure);
        g_free(bytes);
        return;
    }
    g_free(bytes);
    g_strlcpy(
      candidate.file_sha256, file_sha,
      sizeof(candidate.file_sha256));

    if (!crashcar_live_candidate_valid(&candidate)) {
        crashcar_live_record_refresh(
          element, CRASHCAR_LIVE_REFRESH_REJECTED_SCHEMA,
          "schema4 candidate scientific state is incomplete",
          candidate.version, candidate.window_end_gps_ns, file_sha);
        crashcar_parsed_background_clear(&candidate);
        return;
    }

    if (element->live_lkg_valid &&
        candidate.version < element->live_lkg_version) {
        crashcar_live_record_refresh(
          element, CRASHCAR_LIVE_REFRESH_REJECTED_VERSION,
          "schema4 candidate version rollback",
          candidate.version, candidate.window_end_gps_ns, file_sha);
        GST_WARNING_OBJECT(element->owner,
          "live single refresh rejected rollback worker=%d candidate=%"
          G_GUINT64_FORMAT " lkg=%" G_GUINT64_FORMAT,
          element->worker_id, candidate.version,
          element->live_lkg_version);
        crashcar_parsed_background_clear(&candidate);
        return;
    }
    if (element->live_lkg_valid &&
        candidate.version == element->live_lkg_version) {
        if (strcmp(
              candidate.file_sha256,
              element->background_file_sha256) != 0) {
            crashcar_live_record_refresh(
              element, CRASHCAR_LIVE_REFRESH_REJECTED_VERSION,
              "same schema4 version has different bytes",
              candidate.version, candidate.window_end_gps_ns, file_sha);
        } else {
            crashcar_live_record_refresh(
              element, CRASHCAR_LIVE_REFRESH_UNCHANGED,
              "", candidate.version,
              candidate.window_end_gps_ns, file_sha);
        }
        crashcar_parsed_background_clear(&candidate);
        return;
    }
    if (element->live_lkg_valid &&
        candidate.window_end_gps_ns <
          element->live_lkg_window_end_gps_ns) {
        crashcar_live_record_refresh(
          element, CRASHCAR_LIVE_REFRESH_REJECTED_VERSION,
          "schema4 candidate coverage rollback",
          candidate.version, candidate.window_end_gps_ns, file_sha);
        GST_WARNING_OBJECT(element->owner,
          "live single refresh rejected coverage rollback worker=%d "
          "candidate_version=%" G_GUINT64_FORMAT
          " candidate_coverage=%" G_GINT64_FORMAT
          " lkg_version=%" G_GUINT64_FORMAT
          " lkg_coverage=%" G_GINT64_FORMAT,
          element->worker_id, candidate.version,
          candidate.window_end_gps_ns, element->live_lkg_version,
          element->live_lkg_window_end_gps_ns);
        crashcar_parsed_background_clear(&candidate);
        return;
    }
    if (!crashcar_live_coverage_is_eligible(
          element, candidate.window_end_gps_ns, event_gps_ns)) {
        crashcar_live_record_refresh(
          element, CRASHCAR_LIVE_REFRESH_REJECTED_FUTURE,
          "schema4 coverage endpoint is later than event GPS",
          candidate.version, candidate.window_end_gps_ns, file_sha);
        GST_INFO_OBJECT(element->owner,
          "live single candidate not yet event-eligible worker=%d version=%"
          G_GUINT64_FORMAT " coverage=%" G_GINT64_FORMAT
          " event=%" G_GINT64_FORMAT " lkg_version=%"
          G_GUINT64_FORMAT,
          element->worker_id, candidate.version,
          candidate.window_end_gps_ns, event_gps_ns,
          element->live_lkg_version);
        crashcar_parsed_background_clear(&candidate);
        return;
    }
    if (!crashcar_live_adopt_candidate(
          element, &candidate, event_gps_ns)) {
        crashcar_live_record_refresh(
          element, CRASHCAR_LIVE_REFRESH_REJECTED_SCHEMA,
          "schema4 candidate could not be atomically adopted",
          candidate.version, candidate.window_end_gps_ns, file_sha);
        crashcar_parsed_background_clear(&candidate);
        return;
    }

    crashcar_live_record_refresh(
      element, CRASHCAR_LIVE_REFRESH_ADOPTED, "",
      candidate.version, candidate.window_end_gps_ns, file_sha);
    GST_INFO_OBJECT(element->owner,
      "live single authority adopted worker=%d version=%"
      G_GUINT64_FORMAT " coverage=%" G_GINT64_FORMAT
      " file_sha256=%s",
      element->worker_id, candidate.version,
      candidate.window_end_gps_ns, file_sha);
    crashcar_parsed_background_clear(&candidate);
}

static CrashcarAuthoritySelection
crashcar_snapshot_live_authority(
  CrashcarSingleFarEngine *element,
  gint64 event_gps_ns,
  guint64 *version_out,
  gint64 *epoch_out,
  char provenance_out[CRASHCAR_SHA256_HEX_LENGTH + 1]) {
    if (version_out) *version_out = 0;
    if (epoch_out) *epoch_out = 0;
    if (provenance_out) provenance_out[0] = 0;
    if (!element || !element->live_single_background_readonly ||
        !version_out || !epoch_out || !provenance_out ||
        event_gps_ns <= 0) {
        return CRASHCAR_AUTHORITY_SELECTION_INVALID;
    }
    if (!element->live_lkg_valid) {
        return CRASHCAR_AUTHORITY_SELECTION_NONE;
    }
    if (!crashcar_live_coverage_is_eligible(
          element, element->live_lkg_window_end_gps_ns, event_gps_ns)) {
        return CRASHCAR_AUTHORITY_SELECTION_NONE;
    }

    gboolean valid =
      element->live_lkg_version > 0 &&
      element->live_lkg_version <= (guint64)G_MAXINT64 &&
      element->live_lkg_epoch_gps_ns ==
        element->live_lkg_window_end_gps_ns &&
      element->live_lkg_window_start_gps_ns <
        element->live_lkg_window_end_gps_ns &&
      crashcar_sha256_is_lowercase64(
        element->background_file_sha256);
    for (int ifo_id = 0; ifo_id < 2 && valid; ++ifo_id) {
        const CrashcarCompletedAuthorityIfo *snapshot =
          &element->completed_authority[ifo_id];
        valid = snapshot->valid &&
          snapshot->version == element->live_lkg_version &&
          snapshot->epoch_gps_ns == element->live_lkg_epoch_gps_ns &&
          snapshot->window_start_gps_ns ==
            element->live_lkg_window_start_gps_ns &&
          snapshot->window_end_gps_ns ==
            element->live_lkg_window_end_gps_ns &&
          snapshot->livetime_ns > 0 &&
          snapshot->ranks && snapshot->ranks->len > 0 &&
          snapshot->ranks->len <=
            CRASHCAR_BACKGROUND_MAX_POINTS_PER_IFO;
    }
    if (!valid) {
        return CRASHCAR_AUTHORITY_SELECTION_INVALID;
    }
    *version_out = element->live_lkg_version;
    *epoch_out = element->live_lkg_epoch_gps_ns;
    g_strlcpy(
      provenance_out,
      element->background_file_sha256,
      CRASHCAR_SHA256_HEX_LENGTH + 1);
    return CRASHCAR_AUTHORITY_SELECTION_VALID;
}

static gboolean crashcar_bind_worker_authority(
  CrashcarSingleFarEngine *element,
  gchar **failure) {
    if (!element || element->worker_id < 0) {
        return crashcar_set_failure(
          failure, "worker id is not available for paired authority");
    }
    g_mutex_lock(&crashcar_support_mutex);
    if (!crashcar_worker_authority.worker_bound) {
        crashcar_worker_authority.worker_bound = TRUE;
        crashcar_worker_authority.worker_id = element->worker_id;
        crashcar_worker_authority.tail_log10_far =
          element->tail_log10_far;
        for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
            crashcar_worker_authority.points[ifo_id] =
              g_array_new(FALSE, FALSE, sizeof(CrashcarSupportPoint));
            crashcar_worker_authority.ranks[ifo_id] =
              g_array_new(FALSE, FALSE, sizeof(double));
        }
    }
    const gboolean valid =
      crashcar_worker_authority.worker_id == element->worker_id &&
      crashcar_binary64_bits_equal(
        crashcar_worker_authority.tail_log10_far,
        element->tail_log10_far) &&
      crashcar_worker_authority.points[0] != NULL &&
      crashcar_worker_authority.points[1] != NULL &&
      crashcar_worker_authority.ranks[0] != NULL &&
      crashcar_worker_authority.ranks[1] != NULL;
    g_mutex_unlock(&crashcar_support_mutex);
    if (!valid) {
        return crashcar_set_failure(
          failure, "process contains inconsistent worker authority ids");
    }
    return TRUE;
}

static gboolean crashcar_try_complete_paired_authority_locked(
  CrashcarSingleFarEngine *element,
  gint64 window_start_ns,
  gint64 window_end_ns,
  gint64 available_after_gps_ns,
  gboolean candidate_window_ready) {
    if (!element || element->live_single_background_readonly ||
        !candidate_window_ready || window_start_ns >= window_end_ns ||
        available_after_gps_ns <= 0) {
        return TRUE;
    }

    CrashcarWorkerAuthority *authority = &crashcar_worker_authority;
    CrashcarPendingAuthority *pending = &crashcar_pending_authority;
    if (!authority->worker_bound ||
        authority->worker_id != element->worker_id ||
        window_end_ns <= authority->last_candidate_epoch_ns) {
        return TRUE;
    }
    authority->last_candidate_epoch_ns = window_end_ns;

    guint64 span_ns = 0;
    gint64 candidate_livetime_ns[2] = { 0, 0 };
    GArray *candidate_points[2] = { NULL, NULL };
    GArray *candidate_ranks[2] = { NULL, NULL };
    double candidate_r_tail[2] = { NAN, NAN };
    double candidate_tail_slope[2] = { NAN, NAN };
    guint candidate_fit_count[2] = { 0, 0 };
    gboolean candidate_valid =
      crashcar_ordered_distance_u64(
        window_start_ns, window_end_ns, &span_ns) && span_ns > 0;

    for (int ifo_id = 0; ifo_id < 2 && candidate_valid; ++ifo_id) {
        candidate_livetime_ns[ifo_id] = crashcar_window_ifo_livetime_ns(
          element, ifo_id, window_start_ns, window_end_ns);
        candidate_valid =
          candidate_livetime_ns[ifo_id] > 0 &&
          candidate_livetime_ns[ifo_id] <
            CRASHCAR_BINARY64_EXACT_INTEGER_LIMIT &&
          (guint64)candidate_livetime_ns[ifo_id] > span_ns / 5;
        if (!candidate_valid) break;

        candidate_points[ifo_id] = g_array_new(
          FALSE, FALSE, sizeof(CrashcarSupportPoint));
        candidate_ranks[ifo_id] = g_array_new(
          FALSE, FALSE, sizeof(double));
        GArray *global_points = crashcar_support_array_locked(ifo_id);
        for (guint index = 0; index < global_points->len; ++index) {
            const CrashcarSupportPoint point =
              g_array_index(global_points, CrashcarSupportPoint, index);
            if (point.gps_ns < window_start_ns ||
                point.gps_ns >= window_end_ns ||
                point.available_after_gps_ns >= available_after_gps_ns) {
                continue;
            }
            g_array_append_val(candidate_points[ifo_id], point);
            g_array_append_val(candidate_ranks[ifo_id], point.rank);
        }
        candidate_valid = candidate_points[ifo_id]->len > 0 &&
          candidate_points[ifo_id]->len <=
            CRASHCAR_BACKGROUND_MAX_POINTS_PER_IFO;
        if (!candidate_valid) break;
        const double livetime_seconds =
          (double)candidate_livetime_ns[ifo_id] /
          (double)CRASHCAR_NS_PER_SECOND;
        candidate_valid = crashcar_authority_tail_metrics(
          (const double *)candidate_ranks[ifo_id]->data,
          candidate_ranks[ifo_id]->len, livetime_seconds,
          element->tail_log10_far,
          &candidate_r_tail[ifo_id], &candidate_tail_slope[ifo_id],
          &candidate_fit_count[ifo_id]);
    }

    guint64 latest_version = authority->version;
    if (pending->valid && pending->parsed.version > latest_version) {
        latest_version = pending->parsed.version;
    }
    candidate_valid = candidate_valid &&
      candidate_points[0] && candidate_points[1] &&
      (guint64)candidate_points[0]->len +
        (guint64)candidate_points[1]->len <=
          CRASHCAR_BACKGROUND_MAX_POINTS_TOTAL &&
      latest_version < (guint64)G_MAXINT64;

    CrashcarParsedBackground published = { 0 };
    gchar *publication_failure = NULL;
    gboolean unrecoverable_publication = FALSE;
    const guint64 candidate_version = latest_version + 1;
    if (candidate_valid) {
        candidate_valid = crashcar_publish_schema4_candidate(
          element, candidate_version, window_start_ns, window_end_ns,
          candidate_livetime_ns, candidate_points,
          candidate_r_tail, candidate_tail_slope, candidate_fit_count,
          &published, &unrecoverable_publication, &publication_failure);
    }

    /*
     * Durable revalidation is the publication linearization point, but a
     * published candidate is only pending scientific authority.  It becomes
     * active at the first strictly later shared GPS, so the publication row and
     * every other stream at that same GPS keep the same prior snapshot.
     */
    if (candidate_valid) {
        if (pending->valid) {
            crashcar_parsed_background_clear(&pending->parsed);
        }
        pending->parsed = published;
        memset(&published, 0, sizeof(published));
        pending->valid = TRUE;
        pending->available_after_gps_ns = available_after_gps_ns;
        g_strlcpy(
          element->background_file_sha256,
          pending->parsed.file_sha256,
          sizeof(element->background_file_sha256));
    } else if (publication_failure) {
        GST_WARNING_OBJECT(element->owner,
          "schema4 candidate rejected; prior paired authority retained: %s",
          publication_failure);
    }
    g_free(publication_failure);
    crashcar_parsed_background_clear(&published);
    for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
        if (candidate_points[ifo_id]) {
            g_array_free(candidate_points[ifo_id], TRUE);
        }
        if (candidate_ranks[ifo_id]) {
            g_array_free(candidate_ranks[ifo_id], TRUE);
        }
    }
    return !unrecoverable_publication;
}

static CrashcarAuthoritySelection
crashcar_snapshot_paired_authority(
  CrashcarSingleFarEngine *element,
  gint64 group_gps_ns,
  guint64 *version_out,
  gint64 *epoch_out,
  char provenance_out[CRASHCAR_SHA256_HEX_LENGTH + 1]) {
    if (version_out) *version_out = 0;
    if (epoch_out) *epoch_out = 0;
    if (provenance_out) provenance_out[0] = '\0';
    if (!element || !version_out || !epoch_out || !provenance_out ||
        group_gps_ns <= 0) {
        return CRASHCAR_AUTHORITY_SELECTION_INVALID;
    }

    g_mutex_lock(&crashcar_support_mutex);
    CrashcarWorkerAuthority *authority = &crashcar_worker_authority;
    CrashcarPendingAuthority *pending = &crashcar_pending_authority;
    if (!authority->worker_bound ||
        authority->worker_id != element->worker_id ||
        !authority->points[0] || !authority->points[1] ||
        !authority->ranks[0] || !authority->ranks[1]) {
        g_mutex_unlock(&crashcar_support_mutex);
        return CRASHCAR_AUTHORITY_SELECTION_INVALID;
    }
    if (pending->valid &&
        pending->available_after_gps_ns < group_gps_ns) {
        CrashcarParsedBackground *parsed = &pending->parsed;
        gboolean promotable =
          parsed->version > authority->version &&
          parsed->version <= (guint64)G_MAXINT64 &&
          parsed->epoch_gps_ns > 0 &&
          parsed->epoch_gps_ns <= pending->available_after_gps_ns &&
          parsed->window_start_gps_ns < parsed->window_end_gps_ns &&
          crashcar_binary64_bits_equal(
            parsed->tail_log10_far, authority->tail_log10_far) &&
          crashcar_sha256_is_lowercase64(parsed->file_sha256);
        for (int ifo_id = 0; ifo_id < 2 && promotable; ++ifo_id) {
            promotable = parsed->points[ifo_id] && parsed->ranks[ifo_id] &&
              parsed->points[ifo_id]->len > 0 &&
              parsed->points[ifo_id]->len == parsed->ranks[ifo_id]->len &&
              parsed->ranks[ifo_id]->len <=
                CRASHCAR_BACKGROUND_MAX_POINTS_PER_IFO &&
              parsed->livetime_ns[ifo_id] > 0 &&
              parsed->livetime_ns[ifo_id] <
                CRASHCAR_BINARY64_EXACT_INTEGER_LIMIT &&
              isfinite(parsed->r_tail[ifo_id]) &&
              isfinite(parsed->tail_slope[ifo_id]) &&
              parsed->tail_slope[ifo_id] < 0.0 &&
              parsed->fit_unique_rank_count[ifo_id] >= 2;
        }
        if (!promotable) {
            g_mutex_unlock(&crashcar_support_mutex);
            return CRASHCAR_AUTHORITY_SELECTION_INVALID;
        }

        for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
            g_array_set_size(authority->points[ifo_id], 0);
            for (guint index = 0;
                 index < parsed->points[ifo_id]->len;
                 ++index) {
                const CrashcarBackgroundPoint parsed_point =
                  g_array_index(
                    parsed->points[ifo_id], CrashcarBackgroundPoint, index);
                const CrashcarSupportPoint point = {
                  parsed_point.llr,
                  parsed_point.gps_ns,
                  pending->available_after_gps_ns
                };
                g_array_append_val(authority->points[ifo_id], point);
            }
            g_array_set_size(authority->ranks[ifo_id], 0);
            g_array_append_vals(
              authority->ranks[ifo_id],
              parsed->ranks[ifo_id]->data,
              parsed->ranks[ifo_id]->len);
            authority->livetime_ns[ifo_id] = parsed->livetime_ns[ifo_id];
            authority->r_tail[ifo_id] = parsed->r_tail[ifo_id];
            authority->tail_slope[ifo_id] = parsed->tail_slope[ifo_id];
            authority->fit_unique_rank_count[ifo_id] =
              parsed->fit_unique_rank_count[ifo_id];
        }
        authority->window_start_gps_ns = parsed->window_start_gps_ns;
        authority->window_end_gps_ns = parsed->window_end_gps_ns;
        authority->epoch_gps_ns = parsed->epoch_gps_ns;
        authority->version = parsed->version;
        authority->valid = TRUE;
        g_strlcpy(
          authority->provenance_sha256,
          parsed->file_sha256,
          sizeof(authority->provenance_sha256));
        crashcar_parsed_background_clear(parsed);
        pending->valid = FALSE;
        pending->available_after_gps_ns = 0;
    } else if (pending->valid &&
               pending->available_after_gps_ns > group_gps_ns) {
        GST_DEBUG_OBJECT(element->owner,
          "paired authority pending coverage is not event-eligible "
          "group_gps_ns=%" G_GINT64_FORMAT " available_after_ns=%"
          G_GINT64_FORMAT,
          group_gps_ns, pending->available_after_gps_ns);
    }

    if (!authority->valid && authority->version == 0) {
        g_mutex_unlock(&crashcar_support_mutex);
        return CRASHCAR_AUTHORITY_SELECTION_NONE;
    }
    gboolean valid = authority->valid && authority->version > 0 &&
      authority->version <= (guint64)G_MAXINT64 &&
      authority->epoch_gps_ns > 0 &&
      authority->epoch_gps_ns < group_gps_ns &&
      authority->window_end_gps_ns <= group_gps_ns &&
      authority->window_start_gps_ns < authority->window_end_gps_ns &&
      crashcar_sha256_is_lowercase64(authority->provenance_sha256);
    for (int ifo_id = 0; ifo_id < 2 && valid; ++ifo_id) {
        valid = authority->points[ifo_id]->len > 0 &&
          authority->points[ifo_id]->len == authority->ranks[ifo_id]->len &&
          authority->ranks[ifo_id]->len <=
            CRASHCAR_BACKGROUND_MAX_POINTS_PER_IFO &&
          authority->livetime_ns[ifo_id] > 0 &&
          authority->livetime_ns[ifo_id] <
            CRASHCAR_BINARY64_EXACT_INTEGER_LIMIT &&
          isfinite(authority->r_tail[ifo_id]) &&
          isfinite(authority->tail_slope[ifo_id]) &&
          authority->tail_slope[ifo_id] < 0.0 &&
          authority->fit_unique_rank_count[ifo_id] >= 2;
    }
    if (!valid) {
        const CrashcarAuthoritySelection result =
          authority->epoch_gps_ns >= group_gps_ns
            ? CRASHCAR_AUTHORITY_SELECTION_NONE
            : CRASHCAR_AUTHORITY_SELECTION_INVALID;
        g_mutex_unlock(&crashcar_support_mutex);
        return result;
    }

    for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
        CrashcarCompletedAuthorityIfo *snapshot =
          &element->completed_authority[ifo_id];
        g_array_set_size(snapshot->ranks, 0);
        g_array_append_vals(
          snapshot->ranks,
          authority->ranks[ifo_id]->data,
          authority->ranks[ifo_id]->len);
        snapshot->valid = TRUE;
        snapshot->version = authority->version;
        snapshot->epoch_gps_ns = authority->epoch_gps_ns;
        snapshot->window_start_gps_ns = authority->window_start_gps_ns;
        snapshot->window_end_gps_ns = authority->window_end_gps_ns;
        snapshot->livetime_ns = authority->livetime_ns[ifo_id];
    }
    *version_out = authority->version;
    *epoch_out = authority->epoch_gps_ns;
    g_strlcpy(
      provenance_out,
      authority->provenance_sha256,
      CRASHCAR_SHA256_HEX_LENGTH + 1);
    g_mutex_unlock(&crashcar_support_mutex);
    return CRASHCAR_AUTHORITY_SELECTION_VALID;
}

static guint crashcar_total_completed_authority_support(
  const CrashcarSingleFarEngine *element) {
    guint total = 0;
    if (!element) return 0;
    for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
        const CrashcarCompletedAuthorityIfo *authority =
          &element->completed_authority[ifo_id];
        if (!authority->valid || !authority->ranks) continue;
        if (G_MAXUINT - total < authority->ranks->len) return G_MAXUINT;
        total += authority->ranks->len;
    }
    return total;
}

static void crashcar_add_foreground_support_locked(CrashcarSingleFarEngine *element,
                                            int ifo_id,
                                            double rank,
                                            gint64 gps_ns,
                                            gint64 available_after_gps_ns) {
    if (ifo_id < 0 || ifo_id >= MAX_NIFO || !isfinite(rank) ||
        available_after_gps_ns <= 0) {
        return;
    }
    CrashcarSupportPoint point;
    point.rank = rank;
    point.gps_ns = gps_ns;
    point.available_after_gps_ns = available_after_gps_ns;

    GArray *points = crashcar_support_array_locked(ifo_id);
    g_array_append_val(points, point);

    if (element->background_window_ns <= 0) {
        return;
    }
    gint64 keep_after_ns = gps_ns;
    if (!crashcar_subtract_nonnegative(
          keep_after_ns, element->background_window_ns, &keep_after_ns) ||
        !crashcar_subtract_nonnegative(
          keep_after_ns, element->background_window_ns, &keep_after_ns) ||
        !crashcar_subtract_nonnegative(
          keep_after_ns, element->background_window_ns, &keep_after_ns) ||
        !crashcar_subtract_nonnegative(
          keep_after_ns, element->background_update_ns, &keep_after_ns) ||
        !crashcar_subtract_nonnegative(
          keep_after_ns, 60 * CRASHCAR_NS_PER_SECOND, &keep_after_ns)) {
        keep_after_ns = G_MININT64;
    }
    guint write = 0;
    for (guint read = 0; read < points->len; ++read) {
        CrashcarSupportPoint old = g_array_index(points, CrashcarSupportPoint, read);
        if (old.gps_ns >= keep_after_ns) {
            if (write != read) {
                g_array_index(points, CrashcarSupportPoint, write) = old;
            }
            ++write;
        }
    }
    if (write < points->len) {
        g_array_set_size(points, write);
    }
}


static gboolean crashcar_row_has_ifo(const CrashcarSingleFarEngine *element,
                                     const PostcohInspiralTable *table,
                                     int ifo_id) {
    if (!ifo_set__contains(element->enabled_ifos, ifo_id)) return FALSE;
    if (table->ifos[0] == '\0') return FALSE;
    ifo_set_type row_ifos;
    if (!ifo_set__try_parse(table->ifos, &row_ifos)) return FALSE;
    return ifo_set__contains(row_ifos, ifo_id);
}

CrashcarSingleFinalRoute crashcar_singlefar_final_route_from_ifos(
    const char *ifos) {
    if (g_strcmp0(ifos, "H1") == 0 ||
        g_strcmp0(ifos, "H1V1") == 0) {
        return CRASHCAR_SINGLE_FINAL_ROUTE_H1;
    }
    if (g_strcmp0(ifos, "L1") == 0 ||
        g_strcmp0(ifos, "L1V1") == 0) {
        return CRASHCAR_SINGLE_FINAL_ROUTE_L1;
    }
    if (g_strcmp0(ifos, "H1L1") == 0 ||
        g_strcmp0(ifos, "H1L1V1") == 0) {
        return CRASHCAR_SINGLE_FINAL_ROUTE_MULTI;
    }
    if (g_strcmp0(ifos, "V1") == 0) {
        return CRASHCAR_SINGLE_FINAL_ROUTE_V1_ONLY;
    }
    return CRASHCAR_SINGLE_FINAL_ROUTE_INVALID;
}

gboolean crashcar_singlefar_route_assigns_ifo(
    CrashcarSingleFinalRoute route,
    int ifo_id) {
    return (route == CRASHCAR_SINGLE_FINAL_ROUTE_H1 && ifo_id == 0) ||
           (route == CRASHCAR_SINGLE_FINAL_ROUTE_L1 && ifo_id == 1);
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

static void crashcar_write_detail(
  CrashcarSingleFarEngine *element,
  const PostcohInspiralTable *table,
  int ifo_id,
  double llr,
  double calculated_far,
  gboolean calculated_valid,
  double assigned_far,
  gboolean assigned_valid,
  CrashcarSingleFarSource assigned_source,
  CrashcarSingleFarStatus assigned_status,
  guint direct_far_count_ge,
  double bg_livetime,
  double bg_start,
  double bg_end,
  guint window_count,
  guint total_window_count,
  double feature_gps,
  double assignment_gps,
  double a_eff,
  double dof,
  gboolean authority_valid,
  guint64 authority_version,
  gint64 authority_epoch_gps_ns,
  const char *authority_provenance_sha256) {
    if (!crashcar_singlefar_open_detail(element)) return;

    const LIGOTimeGPS *detail_end_time =
      crashcar_component_end_time(table, ifo_id);
    const double assignment_unix = (double)g_get_real_time() / 1000000.0;
    const float far_multi = crashcar_best_multi_far(table);
    gchar calculated_far_text[G_ASCII_DTOSTR_BUF_SIZE] = "";
    gchar assigned_far_text[G_ASCII_DTOSTR_BUF_SIZE] = "";
    if (calculated_valid &&
        crashcar_far_double_is_valid(calculated_far)) {
        g_ascii_dtostr(
          calculated_far_text,
          sizeof(calculated_far_text),
          calculated_far);
    }
    if (assigned_valid &&
        crashcar_far_double_is_valid(assigned_far)) {
        g_ascii_dtostr(
          assigned_far_text,
          sizeof(assigned_far_text),
          assigned_far);
    }

    g_mutex_lock(&crashcar_detail_file_mutex);
    fprintf(element->detail_output_file,
            "%ld,%d,%d,%d,%d,%d,%d,"
            "%.9g,%.9g,%.17g,%s,%d,%u,"
            "%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,"
            "%s,%d,%d,%d,%.9g,%.9g,%d,"
            "%.17g,%.17g,%u,%u,%d,%" G_GINT64_FORMAT ","
            "%" G_GINT64_FORMAT ",%s,%s,%.17g,%.17g,%.9f,"
            "%s,%d,%" G_GUINT64_FORMAT ",%" G_GUINT64_FORMAT ","
            "%" G_GINT64_FORMAT ",%s,%d\n",
            table->event_id, table->bankid, table->tmplt_idx,
            detail_end_time->gpsSeconds, detail_end_time->gpsNanoSeconds,
            ifo_id, table->is_background, table->snglsnr[ifo_id],
            table->chisq[ifo_id], llr, calculated_far_text,
            calculated_valid ? 1 : 0,
            direct_far_count_ge, bg_livetime,
            table->cohsnr, table->cmbchisq, far_multi,
            table->far_1w_sngl[ifo_id], table->far_1d_sngl[ifo_id],
            table->far_2h_sngl[ifo_id], table->far_sngl[ifo_id],
            assigned_far_text, assigned_valid ? 1 : 0,
            (int)assigned_source, (int)assigned_status,
            a_eff, dof,
            table->snr_series_list[ifo_id] != NULL ? 1 : 0,
            bg_start, bg_end, window_count, total_window_count,
            authority_valid ? 1 : 0, (gint64)authority_version,
            authority_epoch_gps_ns,
            authority_provenance_sha256
              ? authority_provenance_sha256 : "",
            CRASHCAR_CODE_VERSION, feature_gps, assignment_gps,
            assignment_unix,
            element->background_json_fname
              ? element->background_json_fname : "",
            (int)element->live_last_refresh_status,
            element->live_refresh_reject_count,
            element->live_last_candidate_version,
            element->live_last_candidate_coverage_gps_ns,
            element->live_last_candidate_sha256,
            element->live_single_background_readonly &&
              authority_valid &&
              element->live_last_refresh_status !=
                CRASHCAR_LIVE_REFRESH_ADOPTED ? 1 : 0);
    fflush(element->detail_output_file);
    g_mutex_unlock(&crashcar_detail_file_mutex);
}

gboolean crashcar_singlefar_engine_start(CrashcarSingleFarEngine *element) {
    g_return_val_if_fail(element != NULL, FALSE);
    g_return_val_if_fail(element->owner != NULL, FALSE);
    if (!element->enabled) return TRUE;

    gchar *failure = NULL;
    if (!isfinite(element->tail_log10_far) ||
        !(element->tail_log10_far < 0.0) ||
        !crashcar_single_background_mode_is_valid() ||
        !crashcar_parse_worker_bank_roster(element, &failure) ||
        !crashcar_load_livetime_segments(element, &failure) ||
        !crashcar_load_exact_window_config(element, &failure) ||
        !crashcar_load_background_binding(element, &failure) ||
        (!element->live_single_background_readonly &&
         !crashcar_bind_worker_authority(element, &failure))) {
        GST_ELEMENT_ERROR(
          element->owner, RESOURCE, READ,
          ("crashcar graph/segment authority failed closed"),
          ("%s", failure ? failure : "unknown segment authority error"));
        g_free(failure);
        return FALSE;
    }
    g_clear_pointer(&failure, g_free);
    if (!element->segment_livetime_binding_valid ||
        !element->have_livetime_segments) {
        GST_ELEMENT_ERROR(
          element->owner, RESOURCE, READ,
          ("crashcar pinned segment authority is not valid"),
          ("schema=%d", CRASHCAR_SEGMENT_JSON_SCHEMA_VERSION));
        return FALSE;
    }
    element->graph_binding_locked = TRUE;
    return TRUE;
}

GstFlowReturn crashcar_singlefar_engine_transform_ip(
  CrashcarSingleFarEngine *element,
  GstBuffer *buf) {
    g_return_val_if_fail(element != NULL, GST_FLOW_ERROR);
    g_return_val_if_fail(element->owner != NULL, GST_FLOW_ERROR);

    if (!element->enabled) return GST_FLOW_OK;
    if (GST_BUFFER_FLAG_IS_SET(buf, GST_BUFFER_FLAG_GAP)) {
        /*
         * A normal Postcoh GAP represents a no-event interval, not a row ABI.
         * Keep every byte and all buffer metadata untouched for FinalSink.
         */
        return GST_FLOW_OK;
    }

    GstMapInfo mapInfo = GST_MAP_INFO_INIT;
    if (!gst_buffer_map(buf, &mapInfo, GST_MAP_WRITE)) {
        GST_ELEMENT_ERROR(
          element->owner, STREAM, FAILED,
          ("crashcar could not map the Postcoh row buffer"),
          ("required_access=GST_MAP_WRITE"));
        return GST_FLOW_ERROR;
    }

    if (mapInfo.size == 0) {
        /*
         * Normal Postcoh may emit a non-GAP, zero-byte control buffer when
         * there are no rows.  It has no single-detector science payload, so
         * preserve its timestamps, offsets, flags, and metadata verbatim.
         */
        gst_buffer_unmap(buf, &mapInfo);
        return GST_FLOW_OK;
    }

    const gsize postcoh_row_size = sizeof(PostcohInspiralTable);
    if (!mapInfo.data ||
        mapInfo.size < postcoh_row_size ||
        mapInfo.size % postcoh_row_size != 0) {
        GST_ELEMENT_ERROR(
          element->owner, STREAM, FAILED,
          ("crashcar Postcoh row buffer has incompatible ABI shape"),
          ("buffer_size=%" G_GSIZE_FORMAT " row_size=%" G_GSIZE_FORMAT
           " data_nonnull=%d",
           mapInfo.size, postcoh_row_size, mapInfo.data ? 1 : 0));
        gst_buffer_unmap(buf, &mapInfo);
        return GST_FLOW_ERROR;
    }

    const gsize row_count = mapInfo.size / postcoh_row_size;
    PostcohInspiralTable *table_begin =
      (PostcohInspiralTable *)mapInfo.data;
    CrashcarRowWork *row_work =
      row_count > 0 ? g_new0(CrashcarRowWork, row_count) : NULL;
    gsize work_count = 0;

    /*
     * Build scoring work only for foreground rows relevant to the H/L single
     * extension.  Normal Postcoh background/control rows do not carry a shared
     * event time, so they pass through byte-for-byte.  Invalid detector masks
     * also remain byte-for-byte after a warning.  Valid V-only rows preserve
     * every normal A107 byte while the two crashcar-only A109 LLR slots are
     * canonicalized to zero.
     */
    for (gsize original_ordinal = 0;
         original_ordinal < row_count;
         ++original_ordinal) {
        PostcohInspiralTable *table = &table_begin[original_ordinal];
        if (table->is_background != FLAG_FOREGROUND) continue;

        const CrashcarSingleFinalRoute final_route =
          crashcar_singlefar_final_route_from_ifos(table->ifos);
        if (final_route == CRASHCAR_SINGLE_FINAL_ROUTE_INVALID) {
            GST_WARNING_OBJECT(element->owner,
              "single processing skipped for foreground row with invalid "
              "detector mask: ordinal=%" G_GSIZE_FORMAT
              " event_id=%ld ifos=%s",
              original_ordinal, (long)table->event_id, table->ifos);
            continue;
        }
        crashcar_singlefar_prepare_row_llrs(table);
        if (final_route == CRASHCAR_SINGLE_FINAL_ROUTE_V1_ONLY) continue;
        const int route_owner_ifo =
          final_route == CRASHCAR_SINGLE_FINAL_ROUTE_H1
            ? 0
            : (final_route == CRASHCAR_SINGLE_FINAL_ROUTE_L1 ? 1 : -1);
        if (route_owner_ifo >= 0) {
            table->far_sngl[route_owner_ifo] = 0.0f;
        }

        gint64 row_assignment_gps_ns = 0;
        if (!crashcar_ligo_gps_to_ns(
              &table->end_time, &row_assignment_gps_ns) ||
            row_assignment_gps_ns < element->segment_run_start_gps_ns ||
            row_assignment_gps_ns >= element->segment_run_end_gps_ns) {
            GST_WARNING_OBJECT(element->owner,
              "single processing skipped for foreground row without a usable "
              "shared event time: ordinal=%" G_GSIZE_FORMAT
              " event_id=%ld",
              original_ordinal, (long)table->event_id);
            continue;
        }

        gint64 row_bg_end_ns = 0;
        if (!crashcar_assignment_window_end_ns(
              element, row_assignment_gps_ns, &row_bg_end_ns)) {
            GST_WARNING_OBJECT(element->owner,
              "single processing skipped because the foreground event time "
              "has no causal assignment window: ordinal=%" G_GSIZE_FORMAT
              " event_id=%ld gps_ns=%" G_GINT64_FORMAT,
              original_ordinal, (long)table->event_id,
              row_assignment_gps_ns);
            continue;
        }

        gint64 row_bg_start_ns = element->segment_run_start_gps_ns;
        gint64 candidate_start_ns = 0;
        if (crashcar_subtract_nonnegative(
              row_bg_end_ns, element->background_window_ns,
              &candidate_start_ns) &&
            candidate_start_ns > row_bg_start_ns) {
            row_bg_start_ns = candidate_start_ns;
        }
        guint64 row_bg_span_ns = 0;
        if (!crashcar_ordered_distance_u64(
              row_bg_start_ns, row_bg_end_ns, &row_bg_span_ns)) {
            GST_WARNING_OBJECT(element->owner,
              "single processing skipped because the causal background "
              "window is not ordered: ordinal=%" G_GSIZE_FORMAT
              " event_id=%ld",
              original_ordinal, (long)table->event_id);
            continue;
        }
        gint64 required_end_ns = 0;
        if (!crashcar_add_nonnegative_offset(
              element->segment_run_start_gps_ns,
              (guint64)element->background_required_ns,
              &required_end_ns)) {
            GST_WARNING_OBJECT(element->owner,
              "single processing skipped because the configured background "
              "readiness interval overflows: ordinal=%" G_GSIZE_FORMAT
              " event_id=%ld",
              original_ordinal, (long)table->event_id);
            continue;
        }

        CrashcarRowWork *work = &row_work[work_count++];
        work->table = table;
        work->row_assignment_gps_ns = row_assignment_gps_ns;
        work->row_bg_end_ns = row_bg_end_ns;
        work->row_bg_start_ns = row_bg_start_ns;
        work->row_bg_span_ns = row_bg_span_ns;
        work->original_ordinal = original_ordinal;
        work->event_id = (long)table->event_id;
        work->final_route = final_route;
        work->required_window_ready =
          row_bg_end_ns >= required_end_ns;
    }

    if (work_count > 1) {
        qsort(row_work, work_count, sizeof(CrashcarRowWork),
              crashcar_compare_row_work);
    }

    /*
     * Evaluate stable equal-shared-time groups from one pre-group support
     * state.  Support commits only after every row in the group is finalized.
     */
    gsize group_begin = 0;
    while (group_begin < work_count) {
        gsize group_end = group_begin + 1;
        while (group_end < work_count &&
               row_work[group_end].row_assignment_gps_ns ==
                 row_work[group_begin].row_assignment_gps_ns) {
            ++group_end;
        }
        const gint64 group_gps_ns =
          row_work[group_begin].row_assignment_gps_ns;
        const gboolean row_bg_only =
          element->authority_mode ==
            CRASHCAR_SINGLE_AUTHORITY_MODE_BG_ONLY;
        /*
         * Route ownership is fixed before any background lookup.  Multi-owned
         * HL/HLV rows need LLR/support work but never select or evaluate a
         * single FAR background.
         */
        CrashcarRowWork *support_work = NULL;
        gboolean group_needs_single_far = FALSE;
        for (gsize work_index = group_begin;
             work_index < group_end;
             ++work_index) {
            const CrashcarSingleFinalRoute route =
              row_work[work_index].final_route;
            if (!support_work &&
                route != CRASHCAR_SINGLE_FINAL_ROUTE_V1_ONLY) {
                support_work = &row_work[work_index];
            }
            if (route == CRASHCAR_SINGLE_FINAL_ROUTE_H1 ||
                route == CRASHCAR_SINGLE_FINAL_ROUTE_L1) {
                group_needs_single_far = TRUE;
            }
        }

        guint64 selected_authority_version = 0;
        gint64 selected_authority_epoch_ns = 0;
        char selected_authority_provenance[
          CRASHCAR_SHA256_HEX_LENGTH + 1] = { 0 };
        CrashcarAuthoritySelection authority_selection =
          CRASHCAR_AUTHORITY_SELECTION_NONE;
        if (support_work && group_needs_single_far) {
            if (element->live_single_background_readonly) {
                /*
                 * The candidate read and complete validation occur before row
                 * scoring.  H/L therefore see one immutable selected LKG for
                 * this entire shared-time group.
                 */
                crashcar_try_refresh_live_authority(
                  element, group_gps_ns);
                authority_selection = crashcar_snapshot_live_authority(
                  element, group_gps_ns,
                  &selected_authority_version,
                  &selected_authority_epoch_ns,
                  selected_authority_provenance);
            } else {
                /*
                 * Rolling no-injection uses the already reviewed causal
                 * score-before-support authority.
                 */
                authority_selection = crashcar_snapshot_paired_authority(
                  element, group_gps_ns,
                  &selected_authority_version,
                  &selected_authority_epoch_ns,
                  selected_authority_provenance);
            }
        }

        for (gsize work_index = group_begin;
             work_index < group_end;
             ++work_index) {
            CrashcarRowWork *work = &row_work[work_index];
            PostcohInspiralTable *table = work->table;
            const CrashcarSingleFinalRoute final_route = work->final_route;
            if (final_route == CRASHCAR_SINGLE_FINAL_ROUTE_V1_ONLY) continue;

            const int final_owner_ifo =
              final_route == CRASHCAR_SINGLE_FINAL_ROUTE_H1
                ? 0
                : (final_route == CRASHCAR_SINGLE_FINAL_ROUTE_L1 ? 1 : -1);
            const gboolean multi_llr_only =
              final_route == CRASHCAR_SINGLE_FINAL_ROUTE_MULTI;

            for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
                if (!crashcar_row_has_ifo(element, table, ifo_id)) continue;
                const gboolean compute_single_far =
                  ifo_id == final_owner_ifo;
                if (!multi_llr_only && !compute_single_far) continue;

                /*
                 * The final owner was fixed and its route-owned FAR cleared
                 * before any time precondition.  Only H/HV or L/LV may replace
                 * that A107 value; HL/HLV never writes any A107 byte.
                 */
                REAL8 *llr_slot =
                  crashcar_singlefar_row_llr_slot(table, ifo_id);
                if (!llr_slot) {
                    continue;
                }

                gboolean *append_future_support =
                  work->append_future_support;
                double *future_support_llr = work->future_support_llr;
                gint64 *future_support_gps_ns =
                  work->future_support_gps_ns;
                const gint64 row_assignment_gps_ns =
                  work->row_assignment_gps_ns;
                const double row_assignment_gps =
                  crashcar_ns_to_seconds(row_assignment_gps_ns);
                const gint64 bg_start_ns = work->row_bg_start_ns;
                const gint64 bg_end_ns = work->row_bg_end_ns;
                const gboolean row_authority_valid =
                  compute_single_far && !row_bg_only &&
                  authority_selection == CRASHCAR_AUTHORITY_SELECTION_VALID;

                CrashcarSingleFarStatus single_status =
                  CRASHCAR_SINGLE_FAR_STATUS_NOT_EVALUATED;
                CrashcarSingleFarSource single_source =
                  CRASHCAR_SINGLE_FAR_SOURCE_NONE;
                gboolean calculated_valid = FALSE;
                gboolean assigned_valid = FALSE;
                double calculated_far = 0.0;
                double assigned_far = 0.0;

                if (!isfinite(table->snglsnr[ifo_id])) {
                    *llr_slot = 0.0;
                    single_status =
                      CRASHCAR_SINGLE_FAR_STATUS_FAILED_INPUT;
                    continue;
                }
                const gboolean single_component_eligible =
                  table->snglsnr[ifo_id] >= CRASHCAR_MIN_SNR;
                if (!single_component_eligible) {
                    *llr_slot = 0.0;
                    single_status =
                      CRASHCAR_SINGLE_FAR_STATUS_NOT_ELIGIBLE;
                    continue;
                }
                if (table->bankid >= 384) {
                    *llr_slot = 0.0;
                    single_status =
                      CRASHCAR_SINGLE_FAR_STATUS_UNSUPPORTED;
                    continue;
                }

                const LIGOTimeGPS *component_time =
                  crashcar_component_end_time(table, ifo_id);
                gint64 component_support_gps_ns = 0;
                if ((component_time->gpsSeconds == 0 &&
                     component_time->gpsNanoSeconds == 0) ||
                    !crashcar_ligo_gps_to_ns(
                      component_time, &component_support_gps_ns) ||
                    component_support_gps_ns <
                      element->segment_run_start_gps_ns ||
                    component_support_gps_ns >=
                      element->segment_run_end_gps_ns) {
                    *llr_slot = 0.0;
                    single_status =
                      CRASHCAR_SINGLE_FAR_STATUS_FAILED_INPUT;
                    continue;
                }

                if (!(table->chisq[ifo_id] > 0.0f) ||
                    !isfinite(table->chisq[ifo_id])) {
                    *llr_slot = 0.0;
                    single_status =
                      CRASHCAR_SINGLE_FAR_STATUS_FAILED_LLR;
                    continue;
                }
                if (!crashcar_row_bank_matches_graph(
                      element, table->bankid)) {
                    GST_DEBUG_OBJECT(element->owner,
                      "crashcar component rejected "
                      "reason=worker_bank_mapping_mismatch "
                      "ifo_id=%d row_bank_id=%d stream_id=%d "
                      "stream_bank_id=%d",
                      ifo_id, table->bankid, element->stream_id,
                      element->stream_bank_id);
                    *llr_slot = 0.0;
                    single_status =
                      CRASHCAR_SINGLE_FAR_STATUS_FAILED_LLR;
                    continue;
                }

                double a_eff = NAN;
                double dof = NAN;
                double llr = NAN;
                if (!crashcar_lookup_template_shape(
                      element, ifo_id, table->bankid, table->tmplt_idx,
                      &a_eff, &dof) ||
                    !crashcar_singlefar_compute_llr(
                      table->snglsnr[ifo_id], table->chisq[ifo_id],
                      a_eff, dof, &llr)) {
                    *llr_slot = 0.0;
                    single_status =
                      CRASHCAR_SINGLE_FAR_STATUS_FAILED_LLR;
                    continue;
                }
                *llr_slot = llr;

                const CrashcarCompletedAuthorityIfo
                  *selected_live_authority =
                    row_authority_valid
                      ? &element->completed_authority[ifo_id]
                      : NULL;

                gint64 bg_livetime_ns = 0;
                double bg_livetime = 0.0;
                const double component_support_gps =
                  crashcar_ns_to_seconds(component_support_gps_ns);
                double bg_start = crashcar_ns_to_seconds(bg_start_ns);
                double bg_end = crashcar_ns_to_seconds(bg_end_ns);
                gboolean selected_completed_authority = FALSE;

                guint direct_far_count_ge = 0;
                const double *fit_ranks = NULL;
                guint window_count = 0;
                guint total_window_count = 0;
                double fitted_far = NAN;
                gboolean has_fitted_far = FALSE;
                gboolean used_tail_fit = FALSE;

                if (compute_single_far && selected_live_authority) {
                    bg_livetime_ns =
                      selected_live_authority->livetime_ns;
                    bg_livetime =
                      (double)bg_livetime_ns /
                      (double)CRASHCAR_NS_PER_SECOND;
                    window_count =
                      selected_live_authority->ranks->len;
                    total_window_count =
                      crashcar_total_completed_authority_support(
                        element);
                    direct_far_count_ge =
                      crashcar_count_ge_from_rank_array(
                        selected_live_authority->ranks, llr);
                    fit_ranks = (const double *)
                      selected_live_authority->ranks->data;
                    bg_start = crashcar_ns_to_seconds(
                      selected_live_authority->window_start_gps_ns);
                    bg_end = crashcar_ns_to_seconds(
                      selected_live_authority->window_end_gps_ns);
                    selected_completed_authority = TRUE;
                }

                if (compute_single_far &&
                    selected_completed_authority &&
                    bg_livetime_ns > 0 && bg_livetime > 0.0 &&
                    window_count > 0) {
                    CrashcarSingleFarEvaluation evaluation = { 0 };
                    has_fitted_far =
                      crashcar_singlefar_evaluate_far_with_tail(
                        fit_ranks, window_count, bg_livetime, llr,
                        element->tail_log10_far, &evaluation);
                    if (crashcar_far_double_is_valid(
                          evaluation.calculated_far)) {
                        calculated_far = evaluation.calculated_far;
                        calculated_valid = TRUE;
                    }
                    if (has_fitted_far &&
                        crashcar_far_double_is_valid(
                          evaluation.assigned_far)) {
                        assigned_far = evaluation.assigned_far;
                        assigned_valid = TRUE;
                        fitted_far = evaluation.assigned_far;
                        used_tail_fit = evaluation.used_tail_fit;
                    }
                }

                float far_sngl = 0.0f;
                if (multi_llr_only) {
                    single_status =
                      CRASHCAR_SINGLE_FAR_STATUS_LLR_ONLY_MULTI;
                } else if (row_bg_only) {
                    single_status =
                      CRASHCAR_SINGLE_FAR_STATUS_BG_ONLY;
                } else if (authority_selection ==
                             CRASHCAR_AUTHORITY_SELECTION_INVALID) {
                    single_status =
                      CRASHCAR_SINGLE_FAR_STATUS_FAILED_BG;
                } else if (!selected_completed_authority) {
                    single_status =
                      CRASHCAR_SINGLE_FAR_STATUS_PENDING_BG;
                } else if (!has_fitted_far || !assigned_valid ||
                           !calculated_valid) {
                    single_status =
                      CRASHCAR_SINGLE_FAR_STATUS_FAILED_BG;
                } else {
                    single_source =
                      element->live_single_background_readonly
                        ? (used_tail_fit
                             ? CRASHCAR_SINGLE_FAR_SOURCE_LIVE_BG_TAIL_FIT
                             : CRASHCAR_SINGLE_FAR_SOURCE_LIVE_BG)
                        : (used_tail_fit
                             ? CRASHCAR_SINGLE_FAR_SOURCE_COMPLETED_BG_TAIL_FIT
                             : CRASHCAR_SINGLE_FAR_SOURCE_COMPLETED_BG);
                    single_status =
                      CRASHCAR_SINGLE_FAR_STATUS_ASSIGNED;
                    const float projected_far = (float)fitted_far;
                    if (crashcar_far_is_valid(projected_far)) {
                        far_sngl = projected_far;
                        table->far_sngl[ifo_id] = far_sngl;
                    } else {
                        single_status =
                          CRASHCAR_SINGLE_FAR_STATUS_FAILED_OUTPUT_POLICY;
                    }
                }

                if (!element->live_single_background_readonly &&
                    isfinite(llr) &&
                    (multi_llr_only ||
                     single_status ==
                       CRASHCAR_SINGLE_FAR_STATUS_PENDING_BG ||
                     single_status ==
                       CRASHCAR_SINGLE_FAR_STATUS_ASSIGNED ||
                     single_status ==
                       CRASHCAR_SINGLE_FAR_STATUS_BG_ONLY)) {
                    append_future_support[ifo_id] = TRUE;
                    future_support_llr[ifo_id] = llr;
                    future_support_gps_ns[ifo_id] =
                      component_support_gps_ns;
                }

                const gboolean write_all_details =
                  element->log10_far_threshold >= 90.0;
                const float far_multi =
                  crashcar_best_multi_far(table);
                const gboolean hit_single_far =
                  compute_single_far &&
                  single_status ==
                    CRASHCAR_SINGLE_FAR_STATUS_ASSIGNED &&
                  crashcar_hits_threshold(
                    far_sngl, element->log10_far_threshold);
                const gboolean hit_multi_far =
                  crashcar_hits_threshold(
                    far_multi, element->log10_far_threshold);
                if (write_all_details ||
                    hit_single_far ||
                    hit_multi_far) {
                    crashcar_write_detail(
                      element, table, ifo_id, llr,
                      calculated_far, calculated_valid,
                      assigned_far, assigned_valid,
                      single_source, single_status,
                      direct_far_count_ge, bg_livetime,
                      bg_start, bg_end, window_count,
                      total_window_count, component_support_gps,
                      row_assignment_gps, a_eff, dof,
                      row_authority_valid,
                      row_authority_valid
                        ? selected_authority_version : 0,
                      row_authority_valid
                        ? selected_authority_epoch_ns : 0,
                      row_authority_valid
                        ? selected_authority_provenance : "");
                }
            }

        }
        if (support_work &&
            !element->live_single_background_readonly) {
            const gboolean candidate_window_ready =
              element->background_window_ns > 0 &&
              support_work->row_bg_span_ns >=
                (guint64)element->background_window_ns &&
              support_work->required_window_ready;
            CrashcarWorkerAuthority *authority =
              &crashcar_worker_authority;

            g_mutex_lock(&crashcar_support_mutex);
            if (!authority->worker_bound ||
                authority->worker_id != element->worker_id ||
                !authority->points[0] || !authority->points[1] ||
                !authority->ranks[0] || !authority->ranks[1]) {
                g_mutex_unlock(&crashcar_support_mutex);
                GST_WARNING_OBJECT(element->owner,
                  "single future-support update skipped because worker-local "
                  "storage is unavailable: worker_id=%d shared_gps_ns=%"
                  G_GINT64_FORMAT,
                  element->worker_id, group_gps_ns);
                group_begin = group_end;
                continue;
            }
            if (!crashcar_try_complete_paired_authority_locked(
                  element,
                  support_work->row_bg_start_ns,
                  support_work->row_bg_end_ns,
                  group_gps_ns,
                  candidate_window_ready)) {
                GST_WARNING_OBJECT(element->owner,
                  "single background candidate was not published; prior "
                  "authority retained: worker_id=%d shared_gps_ns=%"
                  G_GINT64_FORMAT,
                  element->worker_id, group_gps_ns);
            }
            for (gsize work_index = group_begin;
                 work_index < group_end;
                 ++work_index) {
                CrashcarRowWork *support_row = &row_work[work_index];
                for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
                    if (!support_row->append_future_support[ifo_id]) continue;
                    crashcar_add_foreground_support_locked(
                      element,
                      ifo_id,
                      support_row->future_support_llr[ifo_id],
                      support_row->future_support_gps_ns[ifo_id],
                      group_gps_ns);
                }
            }
            g_mutex_unlock(&crashcar_support_mutex);
        }
        group_begin = group_end;
    }

    if (element->detail_output_file) {
        g_mutex_lock(&crashcar_detail_file_mutex);
        fflush(element->detail_output_file);
        g_mutex_unlock(&crashcar_detail_file_mutex);
    }
    g_free(row_work);
    gst_buffer_unmap(buf, &mapInfo);
    return GST_FLOW_OK;
}

void crashcar_singlefar_engine_clear(CrashcarSingleFarEngine *element) {
    if (!element) return;

    if (element->detail_output_file) {
        g_mutex_lock(&crashcar_detail_file_mutex);
        fflush(element->detail_output_file);
        fclose(element->detail_output_file);
        element->detail_output_file = NULL;
        g_mutex_unlock(&crashcar_detail_file_mutex);
    }
    g_free(element->ifos);
    element->ifos = NULL;
    g_free(element->worker_bank_ids);
    element->worker_bank_ids = NULL;
    if (element->worker_bank_id_values) {
        g_array_free(element->worker_bank_id_values, TRUE);
        element->worker_bank_id_values = NULL;
    }
    g_free(element->template_shape_map_fname);
    element->template_shape_map_fname = NULL;
    if (element->template_shape_map) {
        g_hash_table_destroy(element->template_shape_map);
        element->template_shape_map = NULL;
    }
    g_free(element->detail_output_fname);
    element->detail_output_fname = NULL;
    g_free(element->background_json_fname);
    element->background_json_fname = NULL;
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
        if (element->completed_authority[ifo_id].ranks) {
            g_array_free(element->completed_authority[ifo_id].ranks, TRUE);
            element->completed_authority[ifo_id].ranks = NULL;
        }
    }

    element->owner = NULL;
}

void crashcar_singlefar_engine_init(CrashcarSingleFarEngine *element,
                                    GstElement *owner) {
    g_return_if_fail(element != NULL);
    g_return_if_fail(owner != NULL);
    memset(element, 0, sizeof(*element));
    element->owner = owner;
    if (g_once_init_enter(&crashcar_singlefar_debug_initialized)) {
        GST_DEBUG_CATEGORY_INIT(
          GST_CAT_DEFAULT,
          "cohfar_assignfar.single",
          0,
          "single-detector FAR engine inside cohfar_assignfar");
        g_once_init_leave(&crashcar_singlefar_debug_initialized, 1);
    }
    element->ifos = g_strdup("H1L1");
    element->nifo = strlen(element->ifos) / IFO_LEN;
    element->enabled_ifos = ifo_set__parse_or_empty(element->ifos);
    element->stream_id = 0;
    element->stream_count = 1;
    element->stream_bank_id = 0;
    element->worker_bank_ids = g_strdup("0");
    element->worker_bank_id_values = g_array_new(FALSE, FALSE, sizeof(int));
    element->graph_binding_locked = FALSE;
    element->enabled = FALSE;
    element->dof = 120.0;
    element->log10_far_threshold = -4.0;
    element->tail_log10_far = -2.0;
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
    element->segment_livetime_binding_valid = FALSE;
    element->segment_run_start_gps_ns = 0;
    element->segment_run_end_gps_ns = 0;
    element->segment_source_xml_sha256[0] = '\0';
    element->segment_livetime_json_sha256[0] = '\0';
    element->worker_id = -1;
    element->background_worker_count = 0;
    element->background_origin_gps_ns = 0;
    element->authority_mode = CRASHCAR_SINGLE_AUTHORITY_MODE_UNSET;
    element->background_binding_valid = FALSE;
    element->run_namespace_sha256[0] = '\0';
    element->source_manifest_sha256[0] = '\0';
    element->runtime_manifest_sha256[0] = '\0';
    element->config_sha256[0] = '\0';
    element->background_segment_xml_sha256[0] = '\0';
    element->background_segment_canonical_sha256[0] = '\0';
    element->template_shape_map_sha256[0] = '\0';
    element->background_file_sha256[0] = '\0';
    element->background_json_fname = NULL;
    element->live_single_background_readonly =
      crashcar_single_background_mode_is_live_readonly();
    element->live_lkg_valid = FALSE;
    element->live_lkg_version = 0;
    element->live_lkg_epoch_gps_ns = 0;
    element->live_lkg_window_start_gps_ns = 0;
    element->live_lkg_window_end_gps_ns = 0;
    element->live_last_refresh_attempt_gps_ns = G_MININT64;
    element->live_last_refresh_success_gps_ns = G_MININT64;
    element->live_refresh_reject_count = 0;
    element->live_last_refresh_status = CRASHCAR_LIVE_REFRESH_NONE;
    element->live_last_reject_reason[0] = '\0';
    element->live_last_candidate_version = 0;
    element->live_last_candidate_coverage_gps_ns = 0;
    element->live_last_candidate_sha256[0] = '\0';
    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        element->livetime[ifo_id] = 0.0;
        element->ranks[ifo_id] = g_array_new(FALSE, FALSE, sizeof(double));
        element->support_points[ifo_id] =
          g_array_new(FALSE, FALSE, sizeof(CrashcarSupportPoint));
        element->livetime_segments[ifo_id] =
          g_array_new(FALSE, FALSE, sizeof(CrashcarLivetimeSegment));
        element->completed_authority[ifo_id].valid = FALSE;
        element->completed_authority[ifo_id].version = 0;
        element->completed_authority[ifo_id].epoch_gps_ns = 0;
        element->completed_authority[ifo_id].window_start_gps_ns = 0;
        element->completed_authority[ifo_id].window_end_gps_ns = 0;
        element->completed_authority[ifo_id].livetime_ns = 0;
        element->completed_authority[ifo_id].ranks =
          g_array_new(FALSE, FALSE, sizeof(double));
    }
    element->template_shape_map_fname = NULL;
    element->template_shape_map = NULL;
    element->template_shape_map_loaded = FALSE;
    element->detail_output_fname = NULL;
    element->detail_output_file = NULL;
    element->detail_output_header_written = FALSE;
}
