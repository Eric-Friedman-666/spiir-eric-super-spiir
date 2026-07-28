#include <math.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <glib.h>
#include <glib/gstdio.h>
#include <gst/gst.h>
#include <cohfar/crashcar_singlefar.h>
#include <pipe_macro.h>
#include <postcohtable.h>
#define NS G_GINT64_CONSTANT(1000000000)
#define SHAPES (2 * 384 * 1000)
#define MIN_SNR 0x1p+2
#define BETA_STEP 0x1.89374bc6a7efap-9
#define LOG_2PI 0x1.d67f1c864beb4p+0
#define LOG_64 0x1.0a2b23f3bab73p+2
typedef struct { gint64 gps; double llr, far; } Point;
typedef struct { gint64 start, end; } Span;
typedef struct { GArray *points[2]; double livetime[2], r_tail[2], slope[2], tail;
    guint fit_count[2]; guint64 version; gint64 start, end, available_after; gboolean valid; } Background;
typedef struct { GMutex lock; gboolean producer; gint64 run_start, window, update, last_publish, last_refresh;
    double tail, detail_threshold, *shapes; gchar *background_path, *shape_path; GArray *support[2], *segments[2];
    Background active, pending; FILE *detail; } SingleState;
typedef struct { PostcohInspiralTable *row; gint64 gps; gsize ordinal; gboolean add[2]; Point point[2]; } RowWork;
static SingleState state; static gsize state_once; static gboolean state_ok;
static const char *cfg(const char *name, const char *fallback) {
    const char *value = g_getenv(name); return value && *value ? value : fallback; }
static double cfg_double(const char *name, double fallback) {
    const char *text = cfg(name, ""); char *end = NULL;
    double value = g_ascii_strtod(text, &end); return end != text && isfinite(value) ? value : fallback; }
static gint64 gps_ns(const LIGOTimeGPS *gps) {
    return gps ? (gint64)gps->gpsSeconds * NS + gps->gpsNanoSeconds : 0; }
static void clear_bg(Background *bg) {
    for (int i = 0; i < 2; ++i) g_clear_pointer(&bg->points[i], g_array_unref);
    memset(bg, 0, sizeof(*bg)); }
static void move_bg(Background *to, Background *from) {
    clear_bg(to); *to = *from; memset(from, 0, sizeof(*from)); }
static gint point_cmp(gconstpointer a, gconstpointer b) {
    const Point *x = a, *y = b;
    return x->llr != y->llr ? (x->llr > y->llr) - (x->llr < y->llr) :
           (x->gps > y->gps) - (x->gps < y->gps); }
static gint row_cmp(const void *a, const void *b) {
    const RowWork *x = a, *y = b;
    return x->gps != y->gps ? (x->gps > y->gps) - (x->gps < y->gps) :
           (x->ordinal > y->ordinal) - (x->ordinal < y->ordinal); }
static gboolean load_shapes(void) {
    gchar *data = NULL; guint count = 0; char *save = NULL;
    if (!state.shape_path || !g_file_get_contents(state.shape_path, &data, NULL, NULL)) return FALSE;
    state.shapes = g_new0(double, SHAPES);
    for (char *line = strtok_r(data, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        int ifo, bank, tmplt; double value;
        if (sscanf(line, "%d,%d,%d,%la", &ifo, &bank, &tmplt, &value) == 4 &&
            ifo >= 0 && ifo < 2 && bank >= 0 && bank < 384 && tmplt >= 0 &&
            tmplt < 1000 && value > 0.0 && isfinite(value)) {
            state.shapes[(ifo * 384 + bank) * 1000 + tmplt] = value; ++count;
        } }
    g_free(data); return count == SHAPES; }
static gboolean load_segments(void) {
    const char *path = cfg("CRASHCAR_SEGMENT_LIVETIME_JSON", ""); gchar *data = NULL;
    if (!*path || !g_file_get_contents(path, &data, NULL, NULL)) return FALSE;
    for (int ifo = 0; ifo < 2; ++ifo) {
        char key[16]; g_snprintf(key, sizeof(key), "\"%s\":{", ifo ? "L1" : "H1");
        char *p = strstr(data, key); p = p ? strstr(p, "\"intervals\":[") : NULL; char *limit = p ? strchr(p, ']') : NULL;
        state.segments[ifo] = g_array_new(FALSE, FALSE, sizeof(Span));
        while (p && limit && (p = strstr(p, "{\"start\":{")) && p < limit) {
            long long a, an, b, bn; int used = 0;
            if (sscanf(p, "{\"start\":{\"seconds\":%lld,\"nanoseconds\":%lld},\"end\":{\"seconds\":%lld,\"nanoseconds\":%lld}}%n",
                       &a, &an, &b, &bn, &used) != 4 || used <= 0) break;
            Span span = {(gint64)a * NS + an, (gint64)b * NS + bn};
            if (span.start < span.end) { g_array_append_val(state.segments[ifo], span); } p += used;
        }
        if (!state.segments[ifo]->len) { g_free(data); return FALSE; }
    } g_free(data); return TRUE; }
static double livetime(int ifo, gint64 start, gint64 end) {
    gint64 total = 0;
    for (guint i = 0; i < state.segments[ifo]->len; ++i) {
        Span span = g_array_index(state.segments[ifo], Span, i); gint64 a = MAX(start, span.start), b = MIN(end, span.end);
        if (a < b) total += b - a;
    } return (double)total / NS; }
static gboolean normal_log(double x, double mean, double variance, double *out) {
    if (!isfinite(x) || !isfinite(mean) || !(variance > 0.0) || !isfinite(variance)) return FALSE;
    double delta = x - mean, scaled = delta * delta / variance;
    *out = -0.5 * (LOG_2PI + log(variance) + scaled);
    return isfinite(scaled) && isfinite(*out); }
static gboolean compute_llr(double rho, double chisq, double a_eff, double dof, double *out) {
    if (out) *out = NAN;
    if (!out || rho < MIN_SNR || !(chisq > 0.0) || !(a_eff > 0.0) ||
        !isfinite(rho) || !isfinite(chisq) || !isfinite(a_eff) ||
        (dof != 120.0 && dof != 600.0)) return FALSE;
    double rho2 = rho * rho, x = dof * chisq, lambda = rho2 * a_eff, noise;
    if (!normal_log(x, dof + lambda, 2.0 * (dof + 2.0 * lambda), &noise)) return FALSE;
    double terms[64], maximum, sum = 0.0;
    for (guint j = 0; j < 64; ++j) {
        double beta = BETA_STEP + BETA_STEP * (double)j, lambda1 = beta * beta * lambda;
        if (!normal_log(x, dof + lambda1, 2.0 * (dof + 2.0 * lambda1), &terms[j])) return FALSE;
    }
    maximum = terms[0]; for (guint j = 1; j < 64; ++j) maximum = MAX(maximum, terms[j]);
    for (guint j = 0; j < 64; ++j) sum += exp(terms[j] - maximum);
    *out = maximum + log(sum) - LOG_64 - noise + rho2 / 2.0;
    return isfinite(*out); }
static gboolean build_curve(int ifo, gint64 start, gint64 end, Background *bg) {
    GArray *curve = g_array_new(FALSE, FALSE, sizeof(Point));
    for (guint i = 0; i < state.support[ifo]->len; ++i) {
        Point point = g_array_index(state.support[ifo], Point, i); if (point.gps >= start && point.gps < end) g_array_append_val(curve, point); }
    bg->livetime[ifo] = livetime(ifo, start, end);
    if (!curve->len || !(bg->livetime[ifo] > 0.0)) { g_array_unref(curve); return FALSE; }
    g_array_sort(curve, point_cmp); guint tail = 0; double best = G_MAXDOUBLE;
    for (guint i = 0; i < curve->len;) {
        guint next = i + 1; while (next < curve->len && g_array_index(curve, Point, next).llr == g_array_index(curve, Point, i).llr) ++next;
        double far = (double)(curve->len - i) / bg->livetime[ifo];
        for (guint k = i; k < next; ++k) g_array_index(curve, Point, k).far = far;
        double distance = fabs(log10(far) - bg->tail);
        if (distance < best) { best = distance; tail = i; } i = next;
    }
    bg->r_tail[ifo] = g_array_index(curve, Point, tail).llr;
    double numerator = 0.0, denominator = 0.0;
    for (guint i = tail; i < curve->len;) {
        Point point = g_array_index(curve, Point, i); double dx = point.llr - bg->r_tail[ifo];
        numerator += dx * (log10(point.far) - bg->tail); denominator += dx * dx; ++bg->fit_count[ifo];
        do { ++i; } while (i < curve->len && g_array_index(curve, Point, i).llr == point.llr);
    }
    bg->slope[ifo] = denominator > 0.0 ? numerator / denominator : NAN;
    if (bg->fit_count[ifo] < 2 || !(bg->slope[ifo] < 0.0) || !isfinite(bg->slope[ifo])) {
        g_array_unref(curve); return FALSE;
    }
    bg->points[ifo] = curve; return TRUE; }
static double assign_far(const Background *bg, int ifo, double llr, double *direct, guint *count) {
    GArray *curve = bg->points[ifo]; *count = 0;
    for (guint i = 0; i < curve->len; ++i) if (g_array_index(curve, Point, i).llr >= llr) ++*count;
    *direct = MAX((double)*count, 1.0) / bg->livetime[ifo];
    if (llr > bg->r_tail[ifo]) return pow(10.0, bg->tail + bg->slope[ifo] * (llr - bg->r_tail[ifo]));
    Point nearest = g_array_index(curve, Point, 0); double distance = fabs(llr - nearest.llr);
    for (guint i = 1; i < curve->len; ++i) {
        Point point = g_array_index(curve, Point, i); if (point.llr > bg->r_tail[ifo]) break;
        double candidate = fabs(llr - point.llr);
        if (candidate < distance) { distance = candidate; nearest = point; }
    } return nearest.far; }
static void append_gps(GString *json, gint64 value) {
    g_string_append_printf(json, "{\"seconds\":%" G_GINT64_FORMAT ",\"nanoseconds\":%" G_GINT64_FORMAT "}", value / NS, value % NS); }
static const char *sha(const char *name) {
    return cfg(name, "0000000000000000000000000000000000000000000000000000000000000000"); }
static gboolean write_background(const Background *bg) {
    GString *json = g_string_sized_new(4096);
    g_string_append_printf(json, "{\"schema_version\":4,\"background_kind\":\"no_injection\",\"run_namespace_sha256\":\"%s\",\"source_manifest_sha256\":\"%s\",\"runtime_manifest_sha256\":\"%s\",\"config_sha256\":\"%s\",\"segment_xml_sha256\":\"%s\",\"segment_canonical_sha256\":\"%s\",\"template_shape_map_sha256\":\"%s\",\"worker_id\":%s,\"worker_count\":%s,\"worker_bank_ids\":[%s],\"accepted_version\":%" G_GUINT64_FORMAT ",\"epoch_gps\":" ,
      sha("CRASHCAR_RUN_NAMESPACE_SHA256"), sha("CRASHCAR_SOURCE_MANIFEST_SHA256"), sha("CRASHCAR_RUNTIME_MANIFEST_SHA256"), sha("CRASHCAR_CONFIG_SHA256"), sha("CRASHCAR_SEGMENT_XML_SHA256"), sha("CRASHCAR_SEGMENT_CANONICAL_SHA256"), sha("CRASHCAR_TEMPLATE_SHAPE_MAP_SHA256"), cfg("CRASHCAR_WORKER_ID", "0"), cfg("CRASHCAR_WORKER_COUNT", "1"), cfg("CRASHCAR_WORKER_BANK_IDS_EXPECTED", "0"), bg->version);
    append_gps(json, bg->end); g_string_append(json, ",\"window_start_gps\":"); append_gps(json, bg->start);
    g_string_append(json, ",\"window_end_gps\":"); append_gps(json, bg->end); g_string_append(json, ",\"window_duration\":"); append_gps(json, state.window); g_string_append(json, ",\"update_period\":"); append_gps(json, state.update);
    g_string_append_printf(json, ",\"far_floor_count\":1,\"tail_log10_far\":%.17g,\"backgrounds\":{", bg->tail);
    for (int ifo = 0; ifo < 2; ++ifo) {
        if (ifo) { g_string_append_c(json, ','); } g_string_append_printf(json, "\"%s\":{\"livetime\":", ifo ? "L1" : "H1"); append_gps(json, (gint64)llround(bg->livetime[ifo] * NS));
        g_string_append_printf(json, ",\"support_count\":%u,\"tail_fit\":{\"method\":\"anchored_ols_all_unique_ranks_ge_r_tail\",\"r_tail\":\"%.13a\",\"slope\":\"%.13a\",\"fit_unique_rank_count\":%u},\"far_llr_points\":[", bg->points[ifo]->len, bg->r_tail[ifo], bg->slope[ifo], bg->fit_count[ifo]);
        for (guint i = 0; i < bg->points[ifo]->len; ++i) {
            Point point = g_array_index(bg->points[ifo], Point, i); if (i) g_string_append_c(json, ',');
            g_string_append(json, "{\"gps\":"); append_gps(json, point.gps); g_string_append_printf(json, ",\"llr\":\"%.13a\",\"far\":\"%.13a\"}", point.llr, point.far);
        } g_string_append(json, "]}");
    }
    g_string_append(json, "}}\n"); gchar *tmp = g_strdup_printf("%s.tmp.%ld", state.background_path, (long)getpid());
    gboolean ok = g_file_set_contents(tmp, json->str, json->len, NULL);
    if (ok) { g_chmod(tmp, 0444); ok = g_rename(tmp, state.background_path) == 0; }
    if (!ok) { g_unlink(tmp); } g_free(tmp); g_string_free(json, TRUE); return ok; }
static const char *after(const char *text, const char *key) {
    const char *found = text ? strstr(text, key) : NULL; return found ? found + strlen(key) : NULL; }
static gboolean parse_gps(const char *text, gint64 *value) {
    long long sec, ns;
    if (!text || sscanf(text, "{\"seconds\":%lld,\"nanoseconds\":%lld}", &sec, &ns) != 2 || ns < 0 || ns >= NS) return FALSE;
    *value = (gint64)sec * NS + ns; return TRUE; }
static gboolean read_background(Background *bg) {
    gchar *data = NULL; gsize size = 0;
    if (!g_file_get_contents(state.background_path, &data, &size, NULL) || !size || data[size - 1] != '\n') { g_free(data); return FALSE; }
    char binding[160]; g_snprintf(binding, sizeof(binding), "\"worker_id\":%s,\"worker_count\":%s,\"worker_bank_ids\":[%s]", cfg("CRASHCAR_WORKER_ID", "0"), cfg("CRASHCAR_WORKER_COUNT", "1"), cfg("CRASHCAR_WORKER_BANK_IDS_EXPECTED", "0"));
    const char *p = after(data, "\"accepted_version\":"); bg->version = p ? g_ascii_strtoull(p, NULL, 10) : 0;
    p = after(data, "\"tail_log10_far\":"); bg->tail = p ? g_ascii_strtod(p, NULL) : NAN;
    gboolean ok = strstr(data, "\"schema_version\":4") && strstr(data, "\"background_kind\":\"no_injection\"") && strstr(data, binding) && bg->version && bg->tail < 0.0 && isfinite(bg->tail) && parse_gps(after(data, "\"window_start_gps\":"), &bg->start) && parse_gps(after(data, "\"window_end_gps\":"), &bg->end) && bg->start < bg->end;
    for (int ifo = 0; ok && ifo < 2; ++ifo) {
        char key[32]; g_snprintf(key, sizeof(key), "\"%s\":{\"livetime\":", ifo ? "L1" : "H1");
        p = after(data, key); gint64 live_ns = 0; ok = parse_gps(p, &live_ns) && live_ns > 0;
        bg->livetime[ifo] = (double)live_ns / NS; p = after(p, "\"r_tail\":\""); bg->r_tail[ifo] = p ? g_ascii_strtod(p, NULL) : NAN;
        p = after(p, "\"slope\":\""); bg->slope[ifo] = p ? g_ascii_strtod(p, NULL) : NAN;
        p = after(p, "\"far_llr_points\":["); const char *limit = p ? strchr(p, ']') : NULL;
        bg->points[ifo] = g_array_new(FALSE, FALSE, sizeof(Point));
        while (ok && p && limit && (p = strstr(p, "{\"gps\":")) && p < limit) {
            Point point = {0}; const char *llr = after(p, "\"llr\":\""); const char *far = after(p, "\"far\":\"");
            ok = llr && far && llr < limit && far < limit && parse_gps(p + strlen("{\"gps\":"), &point.gps);
            point.llr = llr ? g_ascii_strtod(llr, NULL) : NAN; point.far = far ? g_ascii_strtod(far, NULL) : NAN;
            ok = ok && isfinite(point.llr) && point.far > 0.0 && isfinite(point.far);
            if (ok) { g_array_append_val(bg->points[ifo], point); } p = far ? far + 1 : NULL;
        }
        ok = ok && bg->points[ifo]->len && isfinite(bg->r_tail[ifo]) && bg->slope[ifo] < 0.0 && isfinite(bg->slope[ifo]);
    }
    bg->valid = ok; g_free(data); if (!ok) clear_bg(bg); return ok; }
static int route(const char *ifos) {
    if (!strcmp(ifos, "H1") || !strcmp(ifos, "H1V1")) { return 0; } if (!strcmp(ifos, "L1") || !strcmp(ifos, "L1V1")) return 1;
    if (!strcmp(ifos, "H1L1") || !strcmp(ifos, "H1L1V1")) return 2;
    return !strcmp(ifos, "V1") ? 3 : -1; }
static float multi_far(const PostcohInspiralTable *row) {
    const float far[] = {row->far_1w, row->far_1d, row->far_2h};
    for (guint i = 0; i < G_N_ELEMENTS(far); ++i) if (far[i] > 0.0f && isfinite(far[i])) return far[i];
    return 0.0f; }
static void write_detail(PostcohInspiralTable *row, int ifo, double llr, double direct, double assigned, guint count, double a_eff, double dof, gint64 assignment_gps) {
    if (!state.detail) return;
    gboolean valid = state.active.valid && (!state.producer || state.active.end <= assignment_gps) && assigned > 0.0 && isfinite(assigned); float multi = multi_far(row);
    if (state.detail_threshold < 90.0 && !(valid && log10(assigned) <= state.detail_threshold) && !(multi > 0.0f && log10(multi) <= state.detail_threshold)) return;
    const LIGOTimeGPS *time = &row->end_time_sngl[ifo]; gboolean tail = valid && llr > state.active.r_tail[ifo];
    int source = valid ? (state.producer ? (tail ? 3 : 1) : (tail ? 6 : 5)) : 0;
    fprintf(state.detail, "%ld,%d,%d,%d,%d,%d,%d,%.9g,%.9g,%.17g,%.17g,%d,%u,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.17g,%d,%d,%d,%.9g,%.9g,%d,%.17g,%.17g,%u,%u,%d,%" G_GUINT64_FORMAT ",%" G_GINT64_FORMAT ",,%s,%.17g,%.17g,%.9f,%s,%d,0,%" G_GUINT64_FORMAT ",%" G_GINT64_FORMAT ",,0\n",
      row->event_id, row->bankid, row->tmplt_idx, time->gpsSeconds, time->gpsNanoSeconds, ifo, row->is_background, row->snglsnr[ifo], row->chisq[ifo], llr, direct, valid, count, valid ? state.active.livetime[ifo] : 0.0, row->cohsnr, row->cmbchisq, multi, row->far_1w_sngl[ifo], row->far_1d_sngl[ifo], row->far_2h_sngl[ifo], row->far_sngl[ifo], assigned, valid, source, valid ? 1 : (route(row->ifos) == 2 ? 11 : 2), a_eff, dof, row->snr_series_list[ifo] != NULL, valid ? (double)state.active.start / NS : 0.0, valid ? (double)state.active.end / NS : 0.0, valid ? state.active.points[ifo]->len : 0, valid ? state.active.points[0]->len + state.active.points[1]->len : 0, valid, valid ? state.active.version : 0, valid ? state.active.end : 0, "compact_ab_r3", (double)gps_ns(time) / NS, (double)assignment_gps / NS, (double)g_get_real_time() / 1000000.0, state.background_path, valid, valid ? state.active.version : 0, valid ? state.active.end : 0); }
static gboolean publish(gint64 gps) {
    gint64 first = state.run_start + state.window;
    if (gps < first) return TRUE;
    gint64 boundary = first + ((gps - first) / state.update) * state.update;
    if (boundary <= state.last_publish) return TRUE;
    Background next = {.version = MAX(state.active.version, state.pending.version) + 1, .start = MAX(state.run_start, boundary - state.window), .end = boundary, .available_after = gps, .tail = state.tail};
    if (!build_curve(0, next.start, next.end, &next) || !build_curve(1, next.start, next.end, &next) || !write_background(&next)) { clear_bg(&next); return FALSE; }
    next.valid = TRUE; move_bg(&state.pending, &next); state.last_publish = boundary;
    for (int ifo = 0; ifo < 2; ++ifo) {
        guint out = 0;
        for (guint i = 0; i < state.support[ifo]->len; ++i) {
            Point point = g_array_index(state.support[ifo], Point, i);
            if (point.gps >= boundary - state.window) g_array_index(state.support[ifo], Point, out++) = point;
        }
        g_array_set_size(state.support[ifo], out);
    } return TRUE; }
static void refresh(gint64 gps) {
    if (state.last_refresh && gps - state.last_refresh < state.update) return;
    Background next = {0}; gboolean loaded = read_background(&next);
    if (loaded && (!state.active.valid || next.version > state.active.version)) move_bg(&state.active, &next); else clear_bg(&next);
    state.last_refresh = gps; }
static gboolean initialize(void) {
    memset(&state, 0, sizeof(state)); g_mutex_init(&state.lock);
    const char *role = cfg("CRASHCAR_ROLE", ""); state.producer = !strcmp(role, "A");
    if (!state.producer && strcmp(role, "B")) return FALSE;
    state.background_path = g_strdup(cfg("CRASHCAR_SINGLE_BACKGROUND_JSON", ""));
    state.shape_path = g_strdup(cfg("CRASHCAR_TEMPLATE_SHAPE_MAP_FNAME", ""));
    state.run_start = (gint64)llround(cfg_double("DATA_START_TIME", 0.0) * NS);
    gint64 run_end = (gint64)llround(cfg_double("DATA_END_TIME", 0.0) * NS);
    state.window = (gint64)llround(cfg_double("BACKGROUND_ACCUMULATION_SECONDS", 10800.0) * NS);
    state.update = (gint64)llround(cfg_double("BACKGROUND_UPDATE_TRIGGER_SECONDS", 3600.0) * NS);
    state.tail = cfg_double("TAIL_LOG_FAR", -2.0); state.detail_threshold = cfg_double("CRASHCAR_LOG10_FAR_THRESHOLD", 90.0);
    if (!*state.background_path || !*state.shape_path || state.window <= 0 || state.update <= 0 || !(state.tail < 0.0) || !load_shapes()) return FALSE;
    if (state.producer && (!state.run_start || run_end <= state.run_start || !load_segments())) return FALSE;
    for (int ifo = 0; state.producer && ifo < 2; ++ifo) state.support[ifo] = g_array_new(FALSE, FALSE, sizeof(Point));
    const char *detail = cfg("CRASHCAR_DETAIL_OUTPUT_FNAME", ""); if (*detail) {
        state.detail = fopen(detail, "a"); if (!state.detail) return FALSE;
        if (!ftell(state.detail)) fprintf(state.detail, "event_id,bankid,tmplt_idx,end_time,end_time_ns,ifo_id,is_background,snglsnr,chisq,llr,far_calculated_exact,far_calculated_valid,far_calculated_support_count,bg_livetime,cohsnr,cmbchisq,far_multi,far_1w_sngl,far_1d_sngl,far_2h_sngl,far_sngl_legacy,far_assigned_exact,far_assigned_valid,far_assigned_source,far_assigned_status,a_eff,dof,has_snr_series,bg_start,bg_end,window_count,total_window_count,single_bg_authority_valid,single_bg_authority_version,single_bg_authority_epoch_gps_ns,single_bg_authority_provenance_sha256,code_version,feature_gps,assignment_gps,assignment_unix,single_bg_path,single_bg_refresh_status,single_bg_refresh_reject_count,single_bg_last_candidate_version,single_bg_last_candidate_coverage_gps_ns,single_bg_last_candidate_sha256,single_bg_lkg_reused\n");
    } return TRUE; }
void crashcar_singlefar_engine_init(CrashcarSingleFarEngine *engine, GstElement *owner) { engine->owner = owner; engine->enabled = FALSE; }
void crashcar_singlefar_engine_clear(CrashcarSingleFarEngine *engine) {
    engine->owner = NULL; engine->enabled = FALSE; }
gboolean crashcar_singlefar_engine_start(CrashcarSingleFarEngine *engine) {
    engine->enabled = !strcmp(cfg("CRASHCAR_ENABLE", "0"), "1");
    if (!engine->enabled) return TRUE;
    if (g_once_init_enter(&state_once)) { state_ok = initialize(); g_once_init_leave(&state_once, 1); }
    return state_ok; }
GstFlowReturn crashcar_singlefar_engine_transform_ip(CrashcarSingleFarEngine *engine, GstBuffer *buffer) {
    if (!engine->enabled || GST_BUFFER_FLAG_IS_SET(buffer, GST_BUFFER_FLAG_GAP)) return GST_FLOW_OK;
    GstMapInfo map = GST_MAP_INFO_INIT; if (!gst_buffer_map(buffer, &map, GST_MAP_WRITE)) return GST_FLOW_ERROR;
    if (!map.size) { gst_buffer_unmap(buffer, &map); return GST_FLOW_OK; }
    if (!map.data || map.size % sizeof(PostcohInspiralTable)) { gst_buffer_unmap(buffer, &map); return GST_FLOW_ERROR; }
    gsize rows = map.size / sizeof(PostcohInspiralTable), count = 0;
    RowWork *work = g_new0(RowWork, rows); PostcohInspiralTable *table = (PostcohInspiralTable *)map.data;
    for (gsize i = 0; i < rows; ++i) if (table[i].is_background == FLAG_FOREGROUND) {
        table[i].H1_LLR = table[i].L1_LLR = 0.0; int owner = route(table[i].ifos);
        if (owner < 0 || owner == 3) { continue; } if (owner < 2) { table[i].far_sngl[owner] = 0.0f; }
        work[count++] = (RowWork){.row = &table[i], .gps = gps_ns(&table[i].end_time), .ordinal = i};
    }
    qsort(work, count, sizeof(*work), row_cmp); g_mutex_lock(&state.lock);
    for (gsize begin = 0; begin < count;) {
        gsize end = begin + 1; while (end < count && work[end].gps == work[begin].gps) ++end; gint64 group_gps = work[begin].gps;
        if (state.producer && state.pending.valid && group_gps > state.pending.available_after) move_bg(&state.active, &state.pending);
        if (!state.producer) refresh(group_gps);
        for (gsize i = begin; i < end; ++i) {
            PostcohInspiralTable *row = work[i].row; int owner = route(row->ifos);
            for (int ifo = 0; ifo < 2; ++ifo) {
                if ((owner < 2 && ifo != owner) || (owner == 2 && !strstr(row->ifos, ifo ? "L1" : "H1"))) continue;
                if (row->bankid < 0 || row->bankid >= 384 || row->tmplt_idx < 0 || row->tmplt_idx >= 1000) continue;
                double dof = row->bankid < 100 ? 120.0 : 600.0, a_eff = state.shapes[(ifo * 384 + row->bankid) * 1000 + row->tmplt_idx], llr;
                if (!compute_llr(row->snglsnr[ifo], row->chisq[ifo], a_eff, dof, &llr)) continue;
                *(ifo ? &row->L1_LLR : &row->H1_LLR) = llr;
                double direct = 0.0, assigned = 0.0; guint direct_count = 0; if (owner == ifo && state.active.valid && (!state.producer || state.active.end <= group_gps)) {
                    assigned = assign_far(&state.active, ifo, llr, &direct, &direct_count); float projected = (float)assigned;
                    row->far_sngl[ifo] = projected > 0.0f && isfinite(projected) ? projected : 0.0f;
                }
                write_detail(row, ifo, llr, direct, assigned, direct_count, a_eff, dof, group_gps);
                if (state.producer && (owner == ifo || owner == 2)) { work[i].add[ifo] = TRUE; work[i].point[ifo] = (Point){gps_ns(&row->end_time_sngl[ifo]), llr, 0.0}; }
            } }
        if (state.producer) {
            for (gsize i = begin; i < end; ++i) for (int ifo = 0; ifo < 2; ++ifo)
                if (work[i].add[ifo]) g_array_append_val(state.support[ifo], work[i].point[ifo]);
            publish(group_gps);
        } begin = end;
    }
    if (state.detail) fflush(state.detail);
    g_mutex_unlock(&state.lock); g_free(work); gst_buffer_unmap(buffer, &map); return GST_FLOW_OK; }
