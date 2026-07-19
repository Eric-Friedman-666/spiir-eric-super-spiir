#ifndef _POSIX_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#endif

#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#ifndef CRASHCAR_SINGLEFAR_SOURCE
#error "CRASHCAR_SINGLEFAR_SOURCE must name the current production C source"
#endif
#include CRASHCAR_SINGLEFAR_SOURCE

static const char *replace_hook_path = NULL;
static const char *replace_hook_candidate = NULL;
static int replace_hook_enabled = 0;

ssize_t __real_read(int fd, void *buffer, size_t count);
ssize_t __wrap_read(int fd, void *buffer, size_t count) {
    const ssize_t amount = __real_read(fd, buffer, count);
    if (amount > 0 && replace_hook_enabled) {
        replace_hook_enabled = 0;
        if (!replace_hook_path || !replace_hook_candidate ||
            rename(replace_hook_candidate, replace_hook_path) != 0) {
            fprintf(stderr, "read replacement hook failed: %s\n",
                    strerror(errno));
            _exit(120);
        }
    }
    return amount;
}

#define CHECK(condition, label)                                                \
    do {                                                                        \
        if (!(condition)) {                                                     \
            fprintf(stderr, "FAIL %s line=%d\n", (label), __LINE__);          \
            return 1;                                                           \
        }                                                                       \
    } while (0)

#define NS_PER_SECOND G_GINT64_CONSTANT(1000000000)

static gint64 gps_ns(gint64 seconds) {
    return seconds * NS_PER_SECOND;
}

static void fill_sha(char output[CRASHCAR_SHA256_HEX_LENGTH + 1],
                     char digit) {
    memset(output, digit, CRASHCAR_SHA256_HEX_LENGTH);
    output[CRASHCAR_SHA256_HEX_LENGTH] = 0;
}

static void set_gps(LIGOTimeGPS *output, gint64 gps) {
    output->gpsSeconds = (INT4)(gps / NS_PER_SECOND);
    output->gpsNanoSeconds = (INT4)(gps % NS_PER_SECOND);
}

static CrashcarSinglefar *make_element(const char *background_path) {
    CrashcarSinglefar *element = g_object_new(CRASHCAR_SINGLEFAR_TYPE, NULL);
    if (!element) return NULL;

    element->enabled = TRUE;
    element->live_single_background_readonly = TRUE;
    element->authority_mode = CRASHCAR_SINGLE_AUTHORITY_MODE_LIVE_READONLY;
    element->background_binding_valid = TRUE;
    element->worker_id = 0;
    element->background_worker_count = 1;
    element->background_origin_gps_ns = gps_ns(1000);
    element->background_window_ns = gps_ns(1000);
    element->background_required_ns = gps_ns(1000);
    element->background_update_ns = gps_ns(100);
    element->segment_run_start_gps_ns = gps_ns(1000);
    element->segment_run_end_gps_ns = gps_ns(5000);
    element->segment_livetime_binding_valid = TRUE;
    element->have_livetime_segments = TRUE;
    element->live_last_refresh_attempt_gps_ns = G_MININT64;
    element->live_last_refresh_success_gps_ns = G_MININT64;
    g_free(element->background_json_fname);
    element->background_json_fname = g_strdup(background_path);

    fill_sha(element->run_namespace_sha256, '1');
    fill_sha(element->source_manifest_sha256, '2');
    fill_sha(element->runtime_manifest_sha256, '3');
    fill_sha(element->config_sha256, '4');
    fill_sha(element->background_segment_xml_sha256, '5');
    fill_sha(element->background_segment_canonical_sha256, '6');
    fill_sha(element->template_shape_map_sha256, '7');

    g_array_set_size(element->worker_bank_id_values, 0);
    int bank_id = 0;
    g_array_append_val(element->worker_bank_id_values, bank_id);
    element->stream_id = 0;
    element->stream_count = 1;
    element->stream_bank_id = 0;
    element->graph_binding_locked = TRUE;

    element->template_shape_map = g_hash_table_new_full(
      g_str_hash, g_str_equal, g_free, g_free);
    for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
        gchar *key = crashcar_template_shape_key(ifo_id, 0, 0);
        CrashcarTemplateShape *shape = g_new0(CrashcarTemplateShape, 1);
        shape->a_eff = 8.0;
        shape->has_a_eff = TRUE;
        shape->dof = 120.0;
        shape->has_dof = TRUE;
        g_hash_table_insert(element->template_shape_map, key, shape);
    }
    element->template_shape_map_loaded = TRUE;
    return element;
}

static gboolean write_all(int fd, const char *bytes, gsize length) {
    gsize offset = 0;
    while (offset < length) {
        const ssize_t amount = write(fd, bytes + offset, length - offset);
        if (amount < 0 && errno == EINTR) continue;
        if (amount <= 0) return FALSE;
        offset += (gsize)amount;
    }
    return TRUE;
}

static gboolean atomic_replace_bytes(const char *path,
                                     const char *bytes,
                                     gsize length) {
    gchar *temporary = g_strdup_printf("%s.tmp.%ld", path, (long)getpid());
    unlink(temporary);
    int fd = open(temporary, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC,
                  0600);
    gboolean valid = fd >= 0 && write_all(fd, bytes, length) &&
      fchmod(fd, 0444) == 0 && fsync(fd) == 0 && close(fd) == 0 &&
      rename(temporary, path) == 0;
    if (fd >= 0 && !valid) close(fd);
    if (!valid) unlink(temporary);
    g_free(temporary);
    return valid;
}

static GString *build_candidate(CrashcarSinglefar *element,
                                guint64 version,
                                gint64 window_end_ns,
                                double rank_shift) {
    const gint64 window_start_ns =
      window_end_ns - element->background_window_ns;
    const gint64 livetime_ns[2] = { gps_ns(800), gps_ns(800) };
    GArray *support[2] = {
      g_array_new(FALSE, FALSE, sizeof(CrashcarSupportPoint)),
      g_array_new(FALSE, FALSE, sizeof(CrashcarSupportPoint))
    };
    double ranks[20];
    for (guint index = 0; index < G_N_ELEMENTS(ranks); ++index) {
        ranks[index] = rank_shift + (double)index + 1.0;
        for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
            CrashcarSupportPoint point = {
              ranks[index] + 0.125 * (double)ifo_id,
              window_start_ns +
                (gint64)(index + 1) *
                  (element->background_window_ns / 21),
              window_end_ns
            };
            g_array_append_val(support[ifo_id], point);
        }
    }

    double r_tail[2] = { NAN, NAN };
    double slope[2] = { NAN, NAN };
    guint fit_count[2] = { 0, 0 };
    gboolean valid = TRUE;
    for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
        double ifo_ranks[20];
        for (guint index = 0; index < G_N_ELEMENTS(ifo_ranks); ++index) {
            ifo_ranks[index] =
              ranks[index] + 0.125 * (double)ifo_id;
        }
        valid = valid && crashcar_authority_tail_metrics(
          ifo_ranks, G_N_ELEMENTS(ifo_ranks), 800.0,
          &r_tail[ifo_id], &slope[ifo_id], &fit_count[ifo_id]);
    }

    gchar *failure = NULL;
    GString *document = valid
      ? crashcar_build_schema4_bytes(
          element, version, window_start_ns, window_end_ns,
          livetime_ns, support, r_tail, slope, fit_count, &failure)
      : NULL;
    if (!document) {
        fprintf(stderr, "candidate build failed: %s\n",
                failure ? failure : "tail metrics");
    }
    g_free(failure);
    for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
        g_array_free(support[ifo_id], TRUE);
    }
    return document;
}

static gboolean publish_candidate(CrashcarSinglefar *element,
                                  guint64 version,
                                  gint64 coverage_ns,
                                  double rank_shift,
                                  const char *path) {
    GString *document = build_candidate(
      element, version, coverage_ns, rank_shift);
    if (!document) return FALSE;
    const gboolean valid = atomic_replace_bytes(
      path, document->str, document->len);
    g_string_free(document, TRUE);
    return valid;
}

static CrashcarAuthoritySelection snapshot(
  CrashcarSinglefar *element,
  gint64 event_gps_ns,
  guint64 *version_out) {
    gint64 epoch = 0;
    char sha[CRASHCAR_SHA256_HEX_LENGTH + 1] = { 0 };
    return crashcar_snapshot_live_authority(
      element, event_gps_ns, version_out, &epoch, sha);
}

static gboolean selected_far_is_positive(
  const CrashcarSinglefar *element,
  int ifo_id) {
    if (!element || ifo_id < 0 || ifo_id >= 2) return FALSE;
    const CrashcarCompletedAuthorityIfo *selected =
      &element->completed_authority[ifo_id];
    if (!selected->valid || !selected->ranks ||
        selected->ranks->len == 0 || selected->livetime_ns <= 0) {
        return FALSE;
    }
    CrashcarSingleFarEvaluation evaluation = { 0 };
    return crashcar_singlefar_evaluate_far(
             (const double *)selected->ranks->data,
             selected->ranks->len,
             (double)selected->livetime_ns / (double)NS_PER_SECOND,
             10.5, &evaluation) &&
      isfinite(evaluation.calculated_far) &&
      evaluation.calculated_far > 0.0 &&
      isfinite(evaluation.assigned_far) &&
      evaluation.assigned_far > 0.0;
}

static int test_nonconsumer_future_coverage_rejected(
  const char *directory) {
    gchar *path = g_build_filename(
      directory, "nonconsumer_future_background.json", NULL);
    g_setenv("CRASHCAR_INTERNAL_LIVE_BACKGROUND_ROLE", "producer", TRUE);
    g_setenv("WGUO_O3A_INJECTION_MODE", "none", TRUE);
    CrashcarSinglefar *element = make_element(path);
    CHECK(element != NULL, "nonconsumer future element");
    CHECK(publish_candidate(
            element, 1, gps_ns(2300), 0.0, path),
          "nonconsumer future publish");
    crashcar_try_refresh_live_authority(element, gps_ns(2200));
    guint64 selected = 0;
    CHECK(snapshot(element, gps_ns(2200), &selected) ==
            CRASHCAR_AUTHORITY_SELECTION_NONE &&
            !element->live_lkg_valid &&
            element->live_last_refresh_status ==
              CRASHCAR_LIVE_REFRESH_REJECTED_FUTURE,
          "nonconsumer future coverage remains rejected");
    g_object_unref(element);
    unlink(path);
    g_free(path);
    g_setenv("CRASHCAR_INTERNAL_LIVE_BACKGROUND_ROLE", "consumer", TRUE);
    g_setenv("WGUO_O3A_INJECTION_MODE", "blind", TRUE);
    return 0;
}

static int test_coverage_growth(const char *directory) {
    gchar *path = g_build_filename(
      directory, "coverage_growth_background.json", NULL);
    CrashcarSinglefar *element = make_element(path);
    CHECK(element != NULL, "coverage growth element");

    guint64 selected = 0;
    CHECK(publish_candidate(
            element, 3, gps_ns(2400), 0.5, path),
          "coverage growth publish v3");
    crashcar_try_refresh_live_authority(element, gps_ns(2400));
    CHECK(snapshot(element, gps_ns(2400), &selected) ==
            CRASHCAR_AUTHORITY_SELECTION_VALID &&
            selected == 3 &&
            element->live_lkg_window_end_gps_ns == gps_ns(2400),
          "coverage growth establish v3");

    CHECK(publish_candidate(
            element, 4, gps_ns(2500), 0.75, path),
          "coverage growth publish v4");
    crashcar_try_refresh_live_authority(element, gps_ns(2500));
    CHECK(snapshot(element, gps_ns(2500), &selected) ==
            CRASHCAR_AUTHORITY_SELECTION_VALID &&
            selected == 4 &&
            element->live_lkg_window_end_gps_ns == gps_ns(2500) &&
            element->live_last_refresh_status ==
              CRASHCAR_LIVE_REFRESH_ADOPTED,
          "higher coverage v4 accepted");

    g_object_unref(element);
    unlink(path);
    g_free(path);
    return 0;
}

static int test_pending_transform(const char *directory,
                                  guint support_before) {
    gchar *path = g_build_filename(directory, "cold_background.json", NULL);
    CHECK(atomic_replace_bytes(path, "{}\n", 3), "cold corrupt write");
    CrashcarSinglefar *element = make_element(path);
    CHECK(element != NULL, "cold element");

    PostcohInspiralTable rows[2];
    memset(rows, 0, sizeof(rows));
    rows[0].is_background = FLAG_EMPTY;
    rows[1].is_background = FLAG_FOREGROUND;
    rows[1].event_id = 7001;
    rows[1].bankid = 0;
    rows[1].tmplt_idx = 0;
    g_strlcpy(rows[1].ifos, "H1L1", sizeof(rows[1].ifos));
    set_gps(&rows[1].end_time, gps_ns(2000));
    for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
        set_gps(&rows[1].end_time_sngl[ifo_id], gps_ns(2000));
        rows[1].snglsnr[ifo_id] = 4.0f;
        rows[1].chisq[ifo_id] = 1.0f;
    }

    CHECK(crashcar_singlefar_final_route_from_ifos(rows[1].ifos) ==
            CRASHCAR_SINGLE_FINAL_ROUTE_MULTI,
          "pending route");
    for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
        double a_eff = NAN;
        double dof = NAN;
        double llr = NAN;
        gint64 component_gps_ns = 0;
        CHECK(crashcar_row_has_ifo(element, &rows[1], ifo_id),
              "pending row ifo");
        CHECK(crashcar_ligo_gps_to_ns(
                &rows[1].end_time_sngl[ifo_id], &component_gps_ns) &&
                component_gps_ns == gps_ns(2000),
              "pending component gps");
        CHECK(crashcar_row_bank_matches_graph(element, rows[1].bankid),
              "pending graph bank");
        CHECK(crashcar_lookup_template_shape(
                element, ifo_id, rows[1].bankid, rows[1].tmplt_idx,
                &a_eff, &dof),
              "pending template shape");
        CHECK(crashcar_singlefar_compute_llr(
                rows[1].snglsnr[ifo_id], rows[1].chisq[ifo_id],
                a_eff, dof, &llr),
              "pending llr prerequisite");
    }

    GstBuffer *buffer = gst_buffer_new_allocate(NULL, sizeof(rows), NULL);
    CHECK(buffer != NULL, "pending buffer");
    CHECK(gst_buffer_fill(buffer, 0, rows, sizeof(rows)) == sizeof(rows),
          "pending buffer fill");
    CHECK(crashcar_singlefar_transform_ip(
            GST_BASE_TRANSFORM(element), buffer) == GST_FLOW_OK,
          "pending transform");
    CHECK(gst_buffer_extract(buffer, 0, rows, sizeof(rows)) == sizeof(rows),
          "pending buffer extract");
    CHECK(isfinite(rows[1].H1_LLR),
          "multi H1 llr retained");
    CHECK(isfinite(rows[1].L1_LLR),
          "multi L1 llr retained");
    for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
        CHECK(rows[1].far_sngl[ifo_id] == 0.0f,
              "multi leaves A107 single FAR untouched");
    }
    CHECK(crashcar_singlefar_support_count(0) == support_before &&
            crashcar_singlefar_support_count(1) == support_before,
          "pending injection support unchanged");

    const REAL8 original_h1_llr = rows[1].H1_LLR;
    const REAL8 original_l1_llr = rows[1].L1_LLR;
    CHECK(publish_candidate(
            element, 1, gps_ns(2000), 0.0, path),
          "cold later candidate");
    crashcar_try_refresh_live_authority(element, gps_ns(2100));
    CHECK(rows[1].H1_LLR == original_h1_llr &&
            rows[1].L1_LLR == original_l1_llr,
          "multi row no backfill");

    gst_buffer_unref(buffer);
    g_object_unref(element);
    g_free(path);
    return 0;
}


static void seed_route_contract_row(PostcohInspiralTable *row,
                                    const char *ifos,
                                    long event_id) {
    const gsize a107_size = offsetof(PostcohInspiralTable, H1_LLR);
    memset(row, 0x5a, a107_size);
    memset((unsigned char *)row + a107_size, 0,
           sizeof(*row) - a107_size);

    row->next = NULL;
    row->process_id = 501;
    row->event_id = event_id;
    row->is_background = FLAG_FOREGROUND;
    row->livetime = 17;
    row->bankid = 0;
    row->tmplt_idx = 0;
    row->pix_idx = 9;
    g_strlcpy(row->ifos, ifos, sizeof(row->ifos));
    g_strlcpy(row->pivotal_ifo, "H1", sizeof(row->pivotal_ifo));
    row->skymap_fname[0] = 0;
    set_gps(&row->end_time, gps_ns(2000));
    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        set_gps(&row->end_time_sngl[ifo_id], gps_ns(2000));
        row->snglsnr[ifo_id] = 4.0f;
        row->chisq[ifo_id] = 1.0f;
        row->far_sngl[ifo_id] = 11.0f + (float)ifo_id;
        row->far_1w_sngl[ifo_id] = 21.0f + (float)ifo_id;
        row->far_1d_sngl[ifo_id] = 31.0f + (float)ifo_id;
        row->far_2h_sngl[ifo_id] = 41.0f + (float)ifo_id;
        row->snr_series_list[ifo_id] = NULL;
    }
    row->far = 51.0f;
    row->far_1w = 52.0f;
    row->far_1d = 53.0f;
    row->far_2h = 54.0f;
    row->H1_LLR = 91.0;
    row->L1_LLR = 92.0;
}

static int test_route_transform_contract(const char *directory,
                                         const char *ifos,
                                         int expected_owner_ifo,
                                         gboolean expect_h1_llr,
                                         gboolean expect_l1_llr,
                                         long event_id) {
    gchar *basename = g_strdup_printf(
      "route_contract_%ld.json", event_id);
    gchar *path = g_build_filename(directory, basename, NULL);
    g_free(basename);
    CrashcarSinglefar *element = make_element(path);
    CHECK(element != NULL, "route contract element");
    element->log10_far_threshold = -INFINITY;

    CHECK(publish_candidate(
            element, 1, gps_ns(2000), 0.0, path),
          "route contract publish");

    const guint support_h_before = crashcar_singlefar_support_count(0);
    const guint support_l_before = crashcar_singlefar_support_count(1);
    PostcohInspiralTable rows[2];
    memset(rows, 0, sizeof(rows));
    rows[0].is_background = FLAG_EMPTY;
    seed_route_contract_row(&rows[1], ifos, event_id);

    unsigned char a107_before[
      offsetof(PostcohInspiralTable, H1_LLR)];
    memcpy(a107_before, &rows[1], sizeof(a107_before));
    const float owner_far_before =
      expected_owner_ifo >= 0
        ? rows[1].far_sngl[expected_owner_ifo] : 0.0f;

    GstBuffer *buffer = gst_buffer_new_allocate(NULL, sizeof(rows), NULL);
    CHECK(buffer != NULL, "route contract buffer");
    CHECK(gst_buffer_fill(buffer, 0, rows, sizeof(rows)) == sizeof(rows),
          "route contract buffer fill");
    CHECK(crashcar_singlefar_transform_ip(
            GST_BASE_TRANSFORM(element), buffer) == GST_FLOW_OK,
          "route contract transform");
    CHECK(gst_buffer_extract(buffer, 0, rows, sizeof(rows)) == sizeof(rows),
          "route contract buffer extract");

    if (expected_owner_ifo >= 0) {
        CHECK(crashcar_far_is_valid(
                rows[1].far_sngl[expected_owner_ifo]) &&
              rows[1].far_sngl[expected_owner_ifo] != owner_far_before,
              "route owner receives positive finite FAR");
        PostcohInspiralTable normalized = rows[1];
        normalized.far_sngl[expected_owner_ifo] = owner_far_before;
        CHECK(memcmp(
                &normalized, a107_before, sizeof(a107_before)) == 0,
              "only route-owned A107 FAR byte range may change");
    } else {
        CHECK(memcmp(
                &rows[1], a107_before, sizeof(a107_before)) == 0,
              "multi or V route preserves arbitrary nonzero A107 prefix");
    }

    CHECK((expect_h1_llr && isfinite(rows[1].H1_LLR)) ||
            (!expect_h1_llr && rows[1].H1_LLR == 0.0),
          "route H1 LLR ownership");
    CHECK((expect_l1_llr && isfinite(rows[1].L1_LLR)) ||
            (!expect_l1_llr && rows[1].L1_LLR == 0.0),
          "route L1 LLR ownership");
    CHECK(crashcar_singlefar_support_count(0) == support_h_before &&
            crashcar_singlefar_support_count(1) == support_l_before,
          "live injection route never mutates support");

    gst_buffer_unref(buffer);
    g_object_unref(element);
    unlink(path);
    g_free(path);
    return 0;
}

static void reset_paired_test_state(void) {
    g_mutex_lock(&crashcar_support_mutex);
    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        if (crashcar_global_support_points[ifo_id]) {
            g_array_free(crashcar_global_support_points[ifo_id], TRUE);
            crashcar_global_support_points[ifo_id] = NULL;
        }
    }
    for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
        if (crashcar_worker_authority.points[ifo_id]) {
            g_array_free(crashcar_worker_authority.points[ifo_id], TRUE);
        }
        if (crashcar_worker_authority.ranks[ifo_id]) {
            g_array_free(crashcar_worker_authority.ranks[ifo_id], TRUE);
        }
    }
    crashcar_parsed_background_clear(&crashcar_pending_authority.parsed);
    memset(&crashcar_worker_authority, 0,
           sizeof(crashcar_worker_authority));
    crashcar_worker_authority.worker_id = -1;
    memset(&crashcar_pending_authority, 0,
           sizeof(crashcar_pending_authority));
    g_mutex_unlock(&crashcar_support_mutex);
}

static CrashcarSinglefar *make_paired_element(const char *background_path,
                                               int stream_bank_id) {
    CrashcarSinglefar *element = make_element(background_path);
    if (!element) return NULL;
    element->live_single_background_readonly = FALSE;
    element->authority_mode = CRASHCAR_SINGLE_AUTHORITY_MODE_CAUSAL_NOINJ;
    element->stream_id = stream_bank_id;
    element->stream_count = 2;
    element->stream_bank_id = stream_bank_id;

    g_array_set_size(element->worker_bank_id_values, 0);
    for (int bank_id = 0; bank_id < 2; ++bank_id) {
        g_array_append_val(element->worker_bank_id_values, bank_id);
    }
    g_hash_table_remove_all(element->template_shape_map);
    for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
        for (int bank_id = 0; bank_id < 2; ++bank_id) {
            gchar *key = crashcar_template_shape_key(
              ifo_id, bank_id, 0);
            CrashcarTemplateShape *shape =
              g_new0(CrashcarTemplateShape, 1);
            shape->a_eff = 8.0;
            shape->has_a_eff = TRUE;
            shape->dof = 120.0;
            shape->has_dof = TRUE;
            g_hash_table_insert(element->template_shape_map, key, shape);
        }
    }
    return element;
}

static GstBuffer *make_paired_pending_buffer(int bank_id,
                                              gint64 shared_gps_ns,
                                              long event_id) {
    PostcohInspiralTable rows[2];
    memset(rows, 0, sizeof(rows));
    rows[0].is_background = FLAG_EMPTY;
    rows[1].is_background = FLAG_FOREGROUND;
    rows[1].event_id = event_id;
    rows[1].bankid = bank_id;
    rows[1].tmplt_idx = 0;
    g_strlcpy(rows[1].ifos, "H1L1", sizeof(rows[1].ifos));
    set_gps(&rows[1].end_time, shared_gps_ns);
    for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
        set_gps(&rows[1].end_time_sngl[ifo_id], shared_gps_ns);
        rows[1].snglsnr[ifo_id] = 4.0f;
        rows[1].chisq[ifo_id] = 1.0f;
    }
    GstBuffer *buffer = gst_buffer_new_allocate(NULL, sizeof(rows), NULL);
    if (!buffer) return NULL;
    if (gst_buffer_fill(buffer, 0, rows, sizeof(rows)) != sizeof(rows)) {
        gst_buffer_unref(buffer);
        return NULL;
    }
    return buffer;
}

static int check_paired_pending_row(GstBuffer *buffer, const char *label) {
    PostcohInspiralTable rows[2];
    CHECK(gst_buffer_extract(buffer, 0, rows, sizeof(rows)) == sizeof(rows),
          label);
    CHECK(isfinite(rows[1].H1_LLR), label);
    CHECK(isfinite(rows[1].L1_LLR), label);
    for (int ifo_id = 0; ifo_id < 2; ++ifo_id) {
        CHECK(rows[1].far_sngl[ifo_id] == 0.0f, label);
    }
    return 0;
}

static int test_paired_out_of_order_pending(const char *directory) {
    reset_paired_test_state();
    gchar *path = g_build_filename(
      directory, "paired_background.json", NULL);
    CrashcarSinglefar *bank_a = make_paired_element(path, 0);
    CrashcarSinglefar *bank_b = make_paired_element(path, 1);
    CHECK(bank_a && bank_b, "paired elements");

    gchar *failure = NULL;
    CHECK(crashcar_bind_worker_authority(bank_a, &failure),
          "paired bind bank a");
    CHECK(failure == NULL, "paired bind bank a reason");
    CHECK(crashcar_bind_worker_authority(bank_b, &failure),
          "paired bind bank b");
    CHECK(failure == NULL, "paired bind bank b reason");

    const guint support_h_before = crashcar_singlefar_support_count(0);
    const guint support_l_before = crashcar_singlefar_support_count(1);
    GstBuffer *gps7 = make_paired_pending_buffer(
      0, gps_ns(1907), 7100);
    CHECK(gps7 != NULL, "paired gps7 buffer");
    CHECK(crashcar_singlefar_transform_ip(
            GST_BASE_TRANSFORM(bank_a), gps7) == GST_FLOW_OK,
          "paired gps7 transform");
    CHECK(check_paired_pending_row(gps7, "paired gps7 pending") == 0,
          "paired gps7 row");
    CHECK(crashcar_singlefar_support_count(0) == support_h_before + 1 &&
            crashcar_singlefar_support_count(1) == support_l_before + 1,
          "paired gps7 support after score");

    crashcar_pending_authority.valid = TRUE;
    crashcar_pending_authority.available_after_gps_ns = gps_ns(1907);
    memset(&crashcar_pending_authority.parsed, 0,
           sizeof(crashcar_pending_authority.parsed));

    GstBuffer *gps5 = make_paired_pending_buffer(
      1, gps_ns(1905), 5100);
    CHECK(gps5 != NULL, "paired gps5 buffer");
    CHECK(crashcar_singlefar_transform_ip(
            GST_BASE_TRANSFORM(bank_b), gps5) == GST_FLOW_OK,
          "paired gps5 out of order transform");
    CHECK(check_paired_pending_row(gps5, "paired gps5 pending") == 0,
          "paired gps5 row");
    CHECK(crashcar_singlefar_support_count(0) == support_h_before + 2 &&
            crashcar_singlefar_support_count(1) == support_l_before + 2,
          "paired gps5 support after score");
    CHECK(crashcar_pending_authority.valid &&
            crashcar_pending_authority.available_after_gps_ns ==
              gps_ns(1907),
          "paired future coverage not adopted");

    guint64 version = 0;
    gint64 epoch = 0;
    char provenance[CRASHCAR_SHA256_HEX_LENGTH + 1] = { 0 };
    CHECK(crashcar_snapshot_paired_authority(
            bank_b, gps_ns(1908), &version, &epoch, provenance) ==
              CRASHCAR_AUTHORITY_SELECTION_INVALID,
          "paired true corrupt candidate fails closed");

    gst_buffer_unref(gps5);
    gst_buffer_unref(gps7);
    g_object_unref(bank_b);
    g_object_unref(bank_a);
    g_free(path);
    reset_paired_test_state();
    return 0;
}

int main(int argc, char **argv) {
    CHECK(argc == 2, "usage");
    CHECK(g_mkdir_with_parents(argv[1], 0700) == 0, "runtime dir");
    g_setenv("CRASHCAR_SINGLE_BACKGROUND_MODE", "live_readonly", TRUE);
    g_setenv("CRASHCAR_INTERNAL_LIVE_BACKGROUND_ROLE", "consumer", TRUE);
    g_setenv("WGUO_O3A_INJECTION_MODE", "blind", TRUE);
    gst_init(NULL, NULL);

    CHECK(test_nonconsumer_future_coverage_rejected(argv[1]) == 0,
          "future coverage is scoped to injection consumer");

    gchar *path = g_build_filename(argv[1], "single_background.json", NULL);
    CrashcarSinglefar *element = make_element(path);
    CHECK(element != NULL, "element");

    const guint support_h_before = crashcar_singlefar_support_count(0);
    const guint support_l_before = crashcar_singlefar_support_count(1);
    CHECK(support_h_before == support_l_before, "initial support pairing");

    CHECK(publish_candidate(
            element, 1, gps_ns(2000), 0.0, path), "publish v1");
    crashcar_try_refresh_live_authority(element, gps_ns(2000));
    if (!element->live_lkg_valid) {
        fprintf(
          stderr,
          "first refresh status=%d version=%" G_GUINT64_FORMAT
          " coverage=%" G_GINT64_FORMAT " reason=%s\n",
          (int)element->live_last_refresh_status,
          element->live_last_candidate_version,
          element->live_last_candidate_coverage_gps_ns,
          element->live_last_reject_reason);
    }
    guint64 selected = 0;
    CHECK(snapshot(element, gps_ns(2000), &selected) ==
            CRASHCAR_AUTHORITY_SELECTION_VALID &&
            selected == 1 &&
            selected_far_is_positive(element, 0) &&
            selected_far_is_positive(element, 1),
          "coverage equality adopts v1");
    const guint64 row_a_version = selected;

    CHECK(atomic_replace_bytes(path, "{}\n", 3), "corrupt v2");
    crashcar_try_refresh_live_authority(element, gps_ns(2100));
    CHECK(snapshot(element, gps_ns(2100), &selected) ==
            CRASHCAR_AUTHORITY_SELECTION_VALID &&
            selected == 1 &&
            selected_far_is_positive(element, 0) &&
            selected_far_is_positive(element, 1) &&
            element->live_last_refresh_status ==
              CRASHCAR_LIVE_REFRESH_REJECTED_SCHEMA,
          "past coverage corrupt refresh retains positive v1 LKG");

    CHECK(publish_candidate(
            element, 2, gps_ns(2300), 0.25, path), "publish future v2");
    crashcar_try_refresh_live_authority(element, gps_ns(2200));
    CHECK(snapshot(element, gps_ns(2200), &selected) ==
            CRASHCAR_AUTHORITY_SELECTION_VALID &&
            selected == 2 &&
            selected_far_is_positive(element, 0) &&
            selected_far_is_positive(element, 1) &&
            element->live_last_refresh_status ==
              CRASHCAR_LIVE_REFRESH_ADOPTED,
          "future coverage v2 accepted with positive FAR");
    crashcar_try_refresh_live_authority(element, gps_ns(2300));
    CHECK(snapshot(element, gps_ns(2300), &selected) ==
            CRASHCAR_AUTHORITY_SELECTION_VALID &&
            selected == 2,
          "v2 accepted without restart at equality");

    const guint64 same_row_version = selected;
    const CrashcarCompletedAuthorityIfo *h_snapshot =
      &element->completed_authority[0];
    const CrashcarCompletedAuthorityIfo *l_snapshot =
      &element->completed_authority[1];
    CrashcarSingleFarEvaluation h_far = { 0 };
    CrashcarSingleFarEvaluation l_far = { 0 };
    CHECK(crashcar_singlefar_evaluate_far(
            (const double *)h_snapshot->ranks->data,
            h_snapshot->ranks->len,
            (double)h_snapshot->livetime_ns / (double)NS_PER_SECOND,
            10.5, &h_far),
          "same row h far");
    CHECK(publish_candidate(
            element, 3, gps_ns(2400), 0.5, path), "publish v3 mid row");
    CHECK(crashcar_singlefar_evaluate_far(
            (const double *)l_snapshot->ranks->data,
            l_snapshot->ranks->len,
            (double)l_snapshot->livetime_ns / (double)NS_PER_SECOND,
            10.5, &l_far),
          "same row l far");
    CHECK(element->live_lkg_version == same_row_version &&
            h_snapshot->version == same_row_version &&
            l_snapshot->version == same_row_version,
          "same row h l snapshot stable");
    CHECK(isfinite(h_far.calculated_far) && h_far.calculated_far > 0.0 &&
            isfinite(h_far.assigned_far) && h_far.assigned_far > 0.0 &&
            isfinite(l_far.calculated_far) && l_far.calculated_far > 0.0 &&
            isfinite(l_far.assigned_far) && l_far.assigned_far > 0.0,
          "no zero nan success");

    crashcar_try_refresh_live_authority(element, gps_ns(2400));
    CHECK(snapshot(element, gps_ns(2400), &selected) ==
            CRASHCAR_AUTHORITY_SELECTION_VALID &&
            selected == 3,
          "v3 adopted next calculation");
    CHECK(row_a_version == 1, "prior row provenance not backfilled");

    CHECK(publish_candidate(
            element, 4, gps_ns(2300), 0.625, path),
          "publish higher-version lower-coverage v4");
    crashcar_try_refresh_live_authority(element, gps_ns(2500));
    CHECK(snapshot(element, gps_ns(2500), &selected) ==
            CRASHCAR_AUTHORITY_SELECTION_VALID &&
            selected == 3 &&
            element->live_lkg_window_end_gps_ns == gps_ns(2400) &&
            element->live_last_candidate_version == 4 &&
            element->live_last_candidate_coverage_gps_ns == gps_ns(2300) &&
            element->live_last_refresh_status ==
              CRASHCAR_LIVE_REFRESH_REJECTED_VERSION,
          "coverage rollback v4 rejected and v3 retained");

    CHECK(publish_candidate(
            element, 4, gps_ns(2400), 0.75, path),
          "publish higher-version equal-coverage v4");
    crashcar_try_refresh_live_authority(element, gps_ns(2600));
    CHECK(snapshot(element, gps_ns(2600), &selected) ==
            CRASHCAR_AUTHORITY_SELECTION_VALID &&
            selected == 4 &&
            element->live_lkg_window_end_gps_ns == gps_ns(2400) &&
            element->live_last_refresh_status ==
              CRASHCAR_LIVE_REFRESH_ADOPTED,
          "equal coverage v4 accepted");

    CHECK(publish_candidate(
            element, 2, gps_ns(2300), 0.25, path), "publish rollback");
    crashcar_try_refresh_live_authority(element, gps_ns(2700));
    CHECK(snapshot(element, gps_ns(2700), &selected) ==
            CRASHCAR_AUTHORITY_SELECTION_VALID &&
            selected == 4 &&
            element->live_last_refresh_status ==
              CRASHCAR_LIVE_REFRESH_REJECTED_VERSION,
          "version rollback retains v4");

    const int saved_worker = element->worker_id;
    element->worker_id = 1;
    CHECK(publish_candidate(
            element, 4, gps_ns(2500), 0.75, path), "publish wrong worker");
    element->worker_id = saved_worker;
    crashcar_try_refresh_live_authority(element, gps_ns(2800));
    CHECK(snapshot(element, gps_ns(2800), &selected) ==
            CRASHCAR_AUTHORITY_SELECTION_VALID &&
            selected == 4 &&
            element->live_last_refresh_status ==
              CRASHCAR_LIVE_REFRESH_REJECTED_SCHEMA,
          "wrong worker retains v4");

    char saved_run[CRASHCAR_SHA256_HEX_LENGTH + 1];
    g_strlcpy(saved_run, element->run_namespace_sha256, sizeof(saved_run));
    fill_sha(element->run_namespace_sha256, '8');
    CHECK(publish_candidate(
            element, 4, gps_ns(2500), 1.0, path), "publish wrong run");
    g_strlcpy(element->run_namespace_sha256, saved_run,
              sizeof(element->run_namespace_sha256));
    crashcar_try_refresh_live_authority(element, gps_ns(2900));
    CHECK(snapshot(element, gps_ns(2900), &selected) ==
            CRASHCAR_AUTHORITY_SELECTION_VALID &&
            selected == 4 &&
            element->live_last_refresh_status ==
              CRASHCAR_LIVE_REFRESH_REJECTED_SCHEMA,
          "wrong run retains v4");

    char saved_source[CRASHCAR_SHA256_HEX_LENGTH + 1];
    g_strlcpy(saved_source, element->source_manifest_sha256,
              sizeof(saved_source));
    fill_sha(element->source_manifest_sha256, '9');
    CHECK(publish_candidate(
            element, 5, gps_ns(2500), 1.125, path),
          "publish wrong provenance");
    g_strlcpy(element->source_manifest_sha256, saved_source,
              sizeof(element->source_manifest_sha256));
    crashcar_try_refresh_live_authority(element, gps_ns(3000));
    CHECK(snapshot(element, gps_ns(3000), &selected) ==
            CRASHCAR_AUTHORITY_SELECTION_VALID &&
            selected == 4 &&
            element->live_last_refresh_status ==
              CRASHCAR_LIVE_REFRESH_REJECTED_SCHEMA,
          "wrong provenance retains v4");

    g_array_index(element->worker_bank_id_values, int, 0) = 1;
    CHECK(publish_candidate(
            element, 4, gps_ns(2500), 1.25, path),
          "publish wrong geometry");
    g_array_index(element->worker_bank_id_values, int, 0) = 0;
    crashcar_try_refresh_live_authority(element, gps_ns(3100));
    CHECK(snapshot(element, gps_ns(3100), &selected) ==
            CRASHCAR_AUTHORITY_SELECTION_VALID &&
            selected == 4 &&
            element->live_last_refresh_status ==
              CRASHCAR_LIVE_REFRESH_REJECTED_SCHEMA,
          "wrong geometry retains v4");

    CHECK(publish_candidate(
            element, 4, gps_ns(2400), 1.5, path),
          "publish same version different bytes");
    crashcar_try_refresh_live_authority(element, gps_ns(3200));
    CHECK(snapshot(element, gps_ns(3200), &selected) ==
            CRASHCAR_AUTHORITY_SELECTION_VALID &&
            selected == 4 &&
            element->live_last_refresh_status ==
              CRASHCAR_LIVE_REFRESH_REJECTED_VERSION,
          "same version different bytes rejected");

    gchar *replacement = g_build_filename(argv[1], "replacement.json", NULL);
    CHECK(publish_candidate(
            element, 5, gps_ns(3000), 1.75, path), "publish v5 for race");
    CHECK(publish_candidate(
            element, 6, gps_ns(3000), 2.0, replacement),
          "publish replacement v6");
    replace_hook_path = path;
    replace_hook_candidate = replacement;
    replace_hook_enabled = 1;
    crashcar_try_refresh_live_authority(element, gps_ns(3300));
    CHECK(replace_hook_enabled == 0, "inode hook fired");
    CHECK(snapshot(element, gps_ns(3300), &selected) ==
            CRASHCAR_AUTHORITY_SELECTION_VALID &&
            selected == 4 &&
            element->live_last_refresh_status ==
              CRASHCAR_LIVE_REFRESH_REJECTED_READ,
          "inode replacement retains v4");

    gchar *target = g_build_filename(argv[1], "symlink_target.json", NULL);
    CHECK(publish_candidate(
            element, 7, gps_ns(3000), 2.25, target),
          "publish symlink target");
    unlink(path);
    CHECK(symlink(target, path) == 0, "make symlink");
    crashcar_try_refresh_live_authority(element, gps_ns(3400));
    CHECK(snapshot(element, gps_ns(3400), &selected) ==
            CRASHCAR_AUTHORITY_SELECTION_VALID &&
            selected == 4 &&
            element->live_last_refresh_status ==
              CRASHCAR_LIVE_REFRESH_REJECTED_READ,
          "symlink rejected retains v4");
    unlink(path);

    CHECK(crashcar_singlefar_support_count(0) == support_h_before &&
            crashcar_singlefar_support_count(1) == support_l_before,
          "live reader does not mutate support");
    CHECK(test_coverage_growth(argv[1]) == 0,
          "coverage growth v4");
    CHECK(test_pending_transform(argv[1], support_h_before) == 0,
          "cold pending transform");
    CHECK(test_route_transform_contract(
            argv[1], "H1", 0, TRUE, FALSE, 8001) == 0,
          "H owner transform contract");
    CHECK(test_route_transform_contract(
            argv[1], "H1V1", 0, TRUE, FALSE, 8002) == 0,
          "HV owner transform contract");
    CHECK(test_route_transform_contract(
            argv[1], "L1", 1, FALSE, TRUE, 8003) == 0,
          "L owner transform contract");
    CHECK(test_route_transform_contract(
            argv[1], "L1V1", 1, FALSE, TRUE, 8004) == 0,
          "LV owner transform contract");
    CHECK(test_route_transform_contract(
            argv[1], "H1L1", -1, TRUE, TRUE, 8005) == 0,
          "HL multi-owned transform contract");
    CHECK(test_route_transform_contract(
            argv[1], "H1L1V1", -1, TRUE, TRUE, 8006) == 0,
          "HLV multi-owned transform contract");
    CHECK(test_route_transform_contract(
            argv[1], "V1", -1, FALSE, FALSE, 8007) == 0,
          "V-only transform contract");
    CHECK(test_paired_out_of_order_pending(argv[1]) == 0,
          "paired out of order pending");

    printf("{\"schema\":1,\"coverage_lt_eq_gt_positive\":true,"
           "\"future_scope_guard\":true,\"corrupt_lkg\":true,"
           "\"same_row_hl\":true,"
           "\"version_rollback\":true,"
           "\"coverage_rollback\":true,\"coverage_equal\":true,"
           "\"coverage_growth\":true,"
           "\"wrong_worker_run_geometry_provenance\":true,"
           "\"inode_replace\":true,\"symlink\":true,"
           "\"pending\":true,\"no_backfill\":true,"
           "\"support_unchanged\":true,"
           "\"route_owner_matrix\":true,"
           "\"arbitrary_a107_prefix\":true,"
           "\"injection_no_support\":true,"
           "\"out_of_order_pending\":true,"
           "\"postcoh_row_size\":%zu,"
           "\"reject_count\":%" G_GUINT64_FORMAT "}\n",
           sizeof(PostcohInspiralTable),
           element->live_refresh_reject_count);

    g_object_unref(element);
    g_free(target);
    g_free(replacement);
    g_free(path);
    return 0;
}
