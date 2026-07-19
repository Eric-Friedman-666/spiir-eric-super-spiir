/*
 * Crashcar low-latency single-detector FAR element.
 *
 * This element lives inside the same postcoh/cohfar GStreamer stream as the
 * multi-detector FAR assignment, so the single-detector branch can be moved out
 * of the Python sidecar and into the synchronized pipeline.
 */

#ifndef __CRASHCAR_SINGLEFAR_H__
#define __CRASHCAR_SINGLEFAR_H__

#include <ifo_set.h>
#include <pipe_macro.h>
#include <postcohtable.h>
#include <stdio.h>

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

G_BEGIN_DECLS

#define CRASHCAR_SHA256_HEX_LENGTH 64

typedef enum {
    CRASHCAR_SINGLE_FAR_SOURCE_NONE = 0,
    CRASHCAR_SINGLE_FAR_SOURCE_COMPLETED_BG = 1,
    CRASHCAR_SINGLE_FAR_SOURCE_FROZEN_BG = 2,
    CRASHCAR_SINGLE_FAR_SOURCE_COMPLETED_BG_TAIL_FIT = 3,
    CRASHCAR_SINGLE_FAR_SOURCE_FROZEN_BG_TAIL_FIT = 4,
    CRASHCAR_SINGLE_FAR_SOURCE_LIVE_BG = 5,
    CRASHCAR_SINGLE_FAR_SOURCE_LIVE_BG_TAIL_FIT = 6
} CrashcarSingleFarSource;

typedef enum {
    CRASHCAR_SINGLE_FAR_STATUS_NOT_EVALUATED = 0,
    CRASHCAR_SINGLE_FAR_STATUS_ASSIGNED = 1,
    CRASHCAR_SINGLE_FAR_STATUS_PENDING_BG = 2,
    CRASHCAR_SINGLE_FAR_STATUS_FAILED_BG = 3,
    CRASHCAR_SINGLE_FAR_STATUS_NOT_ELIGIBLE = 4,
    CRASHCAR_SINGLE_FAR_STATUS_UNSUPPORTED = 5,
    CRASHCAR_SINGLE_FAR_STATUS_FAILED_LLR = 6,
    CRASHCAR_SINGLE_FAR_STATUS_FAILED_OUTPUT_POLICY = 7,
    CRASHCAR_SINGLE_FAR_STATUS_PRESERVED_LEGACY = 8,
    CRASHCAR_SINGLE_FAR_STATUS_BG_ONLY = 9,
    CRASHCAR_SINGLE_FAR_STATUS_FAILED_INPUT = 10,
    CRASHCAR_SINGLE_FAR_STATUS_LLR_ONLY_MULTI = 11
} CrashcarSingleFarStatus;

typedef enum {
    CRASHCAR_SINGLE_AUTHORITY_MODE_UNSET = 0,
    CRASHCAR_SINGLE_AUTHORITY_MODE_CAUSAL_NOINJ = 1,
    CRASHCAR_SINGLE_AUTHORITY_MODE_BG_ONLY = 2,
    CRASHCAR_SINGLE_AUTHORITY_MODE_FROZEN_ASSIGNMENT = 3,
    CRASHCAR_SINGLE_AUTHORITY_MODE_LIVE_READONLY = 4
} CrashcarSingleAuthorityMode;

#define CRASHCAR_SINGLEFAR_TYPE (crashcar_singlefar_get_type())
#define CRASHCAR_SINGLEFAR(obj)                                                \
    (G_TYPE_CHECK_INSTANCE_CAST((obj), CRASHCAR_SINGLEFAR_TYPE,                \
                                CrashcarSinglefar))
#define CRASHCAR_SINGLEFAR_CLASS(klass)                                        \
    (G_TYPE_CHECK_CLASS_CAST((klass), CRASHCAR_SINGLEFAR_TYPE,                 \
                             CrashcarSinglefarClass))
#define GST_IS_CRASHCAR_SINGLEFAR(obj)                                         \
    (G_TYPE_CHECK_INSTANCE_TYPE((obj), CRASHCAR_SINGLEFAR_TYPE))
#define GST_IS_CRASHCAR_SINGLEFAR_CLASS(klass)                                 \
    (G_TYPE_CHECK_CLASS_TYPE((klass), CRASHCAR_SINGLEFAR_TYPE))

typedef struct {
    GstBaseTransformClass parent_class;
} CrashcarSinglefarClass;

typedef struct {
    double a_eff;
    gboolean has_a_eff;
    double dof;
    gboolean has_dof;
} CrashcarTemplateShape;

typedef struct {
    double calculated_far;
    double assigned_far;
    double r_tail;
    double tail_slope;
    double tail_intercept;
    gboolean used_tail_fit;
} CrashcarSingleFarEvaluation;

typedef enum {
    CRASHCAR_SINGLE_FINAL_ROUTE_INVALID = 0,
    CRASHCAR_SINGLE_FINAL_ROUTE_H1 = 1,
    CRASHCAR_SINGLE_FINAL_ROUTE_L1 = 2,
    CRASHCAR_SINGLE_FINAL_ROUTE_MULTI = 3,
    CRASHCAR_SINGLE_FINAL_ROUTE_V1_ONLY = 4
} CrashcarSingleFinalRoute;

typedef struct {
    double rank;
    gint64 gps_ns;
    /*
     * Scientific-availability fence: a support point scored at shared GPS T
     * may enter only authorities published for a strictly later shared GPS.
     * This is independent of when files or input paths become visible.
     */
    gint64 available_after_gps_ns;
} CrashcarSupportPoint;

typedef struct {
    gint64 start_gps_ns;
    gint64 end_gps_ns;
} CrashcarLivetimeSegment;

typedef struct {
    gboolean valid;
    guint64 version;
    gint64 epoch_gps_ns;
    gint64 window_start_gps_ns;
    gint64 window_end_gps_ns;
    gint64 livetime_ns;
    GArray *ranks;
} CrashcarCompletedAuthorityIfo;

typedef enum {
    CRASHCAR_LIVE_REFRESH_NONE = 0,
    CRASHCAR_LIVE_REFRESH_ADOPTED = 1,
    CRASHCAR_LIVE_REFRESH_UNCHANGED = 2,
    CRASHCAR_LIVE_REFRESH_REJECTED_READ = 3,
    CRASHCAR_LIVE_REFRESH_REJECTED_SCHEMA = 4,
    CRASHCAR_LIVE_REFRESH_REJECTED_VERSION = 5,
    CRASHCAR_LIVE_REFRESH_REJECTED_FUTURE = 6
} CrashcarLiveRefreshStatus;

typedef struct {
    GstBaseTransform element;

    char *ifos;
    int nifo;
    ifo_set_type enabled_ifos;

    int stream_id;
    int stream_count;
    int stream_bank_id;
    char *worker_bank_ids;
    GArray *worker_bank_id_values;
    gboolean graph_binding_locked;

    gboolean enabled;
    double dof;
    double log10_far_threshold;
    double tail_log10_far;
    double livetime_step;
    double background_window_seconds;
    double background_required_seconds;
    double background_update_seconds;
    double snapshot_interval_seconds;
    double data_start_gps;
    gint64 background_window_ns;
    gint64 background_required_ns;
    gint64 background_update_ns;
    gint64 snapshot_interval_ns;
    gboolean have_livetime_segments;
    gboolean segment_livetime_binding_valid;
    gint64 segment_run_start_gps_ns;
    gint64 segment_run_end_gps_ns;
    char segment_source_xml_sha256[CRASHCAR_SHA256_HEX_LENGTH + 1];
    char segment_livetime_json_sha256[CRASHCAR_SHA256_HEX_LENGTH + 1];
    int worker_id;
    int background_worker_count;
    gint64 background_origin_gps_ns;
    CrashcarSingleAuthorityMode authority_mode;
    gboolean background_binding_valid;
    char run_namespace_sha256[CRASHCAR_SHA256_HEX_LENGTH + 1];
    char source_manifest_sha256[CRASHCAR_SHA256_HEX_LENGTH + 1];
    char runtime_manifest_sha256[CRASHCAR_SHA256_HEX_LENGTH + 1];
    char config_sha256[CRASHCAR_SHA256_HEX_LENGTH + 1];
    char background_segment_xml_sha256[CRASHCAR_SHA256_HEX_LENGTH + 1];
    char background_segment_canonical_sha256[
      CRASHCAR_SHA256_HEX_LENGTH + 1];
    char template_shape_map_sha256[CRASHCAR_SHA256_HEX_LENGTH + 1];
    /* SHA-256 of the currently selected complete single-background bytes. */
    char background_file_sha256[CRASHCAR_SHA256_HEX_LENGTH + 1];
    char *background_json_fname;
    gboolean live_single_background_readonly;
    gboolean live_lkg_valid;
    guint64 live_lkg_version;
    gint64 live_lkg_epoch_gps_ns;
    gint64 live_lkg_window_start_gps_ns;
    gint64 live_lkg_window_end_gps_ns;
    gint64 live_last_refresh_attempt_gps_ns;
    gint64 live_last_refresh_success_gps_ns;
    guint64 live_refresh_reject_count;
    CrashcarLiveRefreshStatus live_last_refresh_status;
    char live_last_reject_reason[160];
    guint64 live_last_candidate_version;
    gint64 live_last_candidate_coverage_gps_ns;
    char live_last_candidate_sha256[CRASHCAR_SHA256_HEX_LENGTH + 1];
    double livetime[MAX_NIFO];
    GArray *ranks[MAX_NIFO];
    GArray *support_points[MAX_NIFO];
    GArray *livetime_segments[MAX_NIFO];
    /* Per-element immutable snapshot used for one single FAR calculation. */
    CrashcarCompletedAuthorityIfo completed_authority[MAX_NIFO];

    char *template_shape_map_fname;
    GHashTable *template_shape_map;
    gboolean template_shape_map_loaded;

    char *detail_output_fname;
    FILE *detail_output_file;
    gboolean detail_output_header_written;
} CrashcarSinglefar;

GType crashcar_singlefar_get_type(void);
gboolean crashcar_singlefar_ifos_valid(const char *ifos);
CrashcarSingleFinalRoute crashcar_singlefar_final_route_from_ifos(
  const char *ifos);
gboolean crashcar_singlefar_route_assigns_ifo(
  CrashcarSingleFinalRoute route,
  int ifo_id);
gboolean crashcar_singlefar_dof_for_bank(int bankid, double *dof_out);
gboolean crashcar_singlefar_parse_template_shape_row(
  const char *line,
  int *ifo_id,
  int *bankid,
  int *tmplt_idx,
  double *a_eff,
  double *dof);
guint crashcar_singlefar_beta_grid_size(void);
gboolean crashcar_singlefar_beta_at(guint index, double *beta_out);
gboolean crashcar_singlefar_compute_llr(double rho,
                                        double chisq,
                                        double a_eff,
                                        double dof,
                                        double *llr_out);
gboolean crashcar_singlefar_evaluate_far(
  const double *ranks,
  guint rank_count,
  double livetime,
  double rank,
  CrashcarSingleFarEvaluation *evaluation);
gboolean crashcar_singlefar_evaluate_far_with_tail(
  const double *ranks,
  guint rank_count,
  double livetime,
  double rank,
  double tail_log10_far,
  CrashcarSingleFarEvaluation *evaluation);
void crashcar_singlefar_prepare_row_llrs(PostcohInspiralTable *table);
guint crashcar_singlefar_support_count(int ifo_id);

G_END_DECLS

#endif /* __CRASHCAR_SINGLEFAR_H__ */
