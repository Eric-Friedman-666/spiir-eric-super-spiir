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
    double autocorr_power;
    double dof;
    gboolean has_autocorr_power;
    gboolean has_dof;
} CrashcarTemplateShape;

typedef struct {
    double rank;
    double gps;
} CrashcarSupportPoint;

typedef struct {
    double start_gps;
    double end_gps;
} CrashcarLivetimeSegment;

typedef struct {
    GstBaseTransform element;

    char *ifos;
    int nifo;
    ifo_set_type enabled_ifos;

    gboolean enabled;
    double log10_far_threshold;
    double snr_series_log10_far_threshold;
    double min_snr;
    double far_floor_count;
    double livetime_step;
    double background_window_seconds;
    double background_required_seconds;
    double background_update_seconds;
    double snapshot_interval_seconds;
    double data_start_gps;
    gboolean have_livetime_segments;
    double livetime[MAX_NIFO];
    GArray *ranks[MAX_NIFO];
    GArray *support_points[MAX_NIFO];
    GArray *livetime_segments[MAX_NIFO];

    char *template_shape_map_fname;
    GHashTable *template_shape_map;
    gboolean template_shape_map_loaded;

    char *detail_output_fname;
    FILE *detail_output_file;
    gboolean detail_output_header_written;
} CrashcarSinglefar;

GType crashcar_singlefar_get_type(void);

G_END_DECLS

#endif /* __CRASHCAR_SINGLEFAR_H__ */
