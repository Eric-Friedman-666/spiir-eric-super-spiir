#ifndef __GST_FLAG_SEGMENT_HEADER__
#define __GST_FLAG_SEGMENT_HEADER__

#include <glib.h>
#include <gst/gst.h>

typedef struct flag_segment {
    GstClockTime start, stop;
    gboolean is_gap; // GAP true, non-GAP, false
} FlagSegment;

gboolean flag_segments_is_gap(GArray *flag_segments,
                              GstClockTime start,
                              GstClockTime stop);

void flag_segments_append(GArray *flag_segments,
                          GstClockTime start,
                          GstClockTime stop,
                          gboolean is_gap);

#endif /* __GST_FLAG_SEGMENT_HEADER__ */
