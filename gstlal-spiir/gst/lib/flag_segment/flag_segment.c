#include <flag_segment.h>
#include <glib.h>
// Suppresses a warning from gstreamer using deprecated mutexes.
// Should be revisited after the gstreamer upgrade.
// See #15
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"
#include <gst/gst.h>
#pragma GCC diagnostic pop

gboolean flag_segments_is_gap(GArray *flag_segments,
                              GstClockTime start,
                              GstClockTime stop) {
    /* The final segment must not end before the stop time */
    g_assert(flag_segments->len > 0);
    FlagSegment *final_segment =
      &g_array_index(flag_segments, FlagSegment, flag_segments->len - 1);
    g_assert(final_segment->stop >= stop);

    guint flush_len      = 0;
    GstClockTime dur_gap = 0;
    for (guint i = 0; i < flag_segments->len; i++) {
        FlagSegment *this_segment =
          &g_array_index(flag_segments, FlagSegment, i);
        if (this_segment->start >= stop) break;

        if (this_segment->stop <= start) {
            flush_len = i + 1;
        } else if (this_segment->is_gap) {
            GstClockTime gap_start = MAX(this_segment->start, start);
            GstClockTime gap_stop  = MIN(this_segment->stop, stop);
            dur_gap += gap_stop - gap_start;
        }
    }
    if (flush_len > 0) g_array_remove_range(flag_segments, 0, flush_len);

    return dur_gap > (stop - start) / 2 - 1e-6;
}

void flag_segments_append(GArray *flag_segments,
                          GstClockTime start,
                          GstClockTime stop,
                          gboolean is_gap) {
    FlagSegment new_segment = { .start  = start,
                                .stop   = stop,
                                .is_gap = is_gap };

    g_assert_cmpuint(start, <=, stop);
    GST_DEBUG("found control segment [%" GST_TIME_FORMAT ", %" GST_TIME_FORMAT
              ") in state %d",
              GST_TIME_ARGS(new_segment.start), GST_TIME_ARGS(new_segment.stop),
              new_segment.is_gap);

    /* try coalescing the new segment with the most recent one */
    if (flag_segments->len) {
        FlagSegment *final_segment =
          &g_array_index(flag_segments, FlagSegment, flag_segments->len - 1);
        /* if the most recent segment and the new segment have the
         * same state and they touch, merge them */
        if (final_segment->is_gap == new_segment.is_gap
            && final_segment->stop >= new_segment.start) {
            g_assert_cmpuint(new_segment.stop, >=, final_segment->stop);
            final_segment->stop = new_segment.stop;
            return;
        }
        /* otherwise, if the most recent segment had 0 length,
         * replace it entirely with the new one.  note that the
         * state carried by a zero-length segment is meaningless,
         * zero-length segments are merely interpreted as a
         * heart-beat indicating how far the control stream has
         * advanced */
        if (final_segment->stop == final_segment->start) {
            *final_segment = new_segment;
            return;
        }
    }
    /* otherwise append a new segment */
    g_array_append_val(flag_segments, new_segment);
}
