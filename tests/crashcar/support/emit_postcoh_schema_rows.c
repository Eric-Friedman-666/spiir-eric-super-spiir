#include <complex.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <lal/TimeSeries.h>
#include <lal/Units.h>
#include <pipe_macro.h>
#include <postcohtable.h>

static void initialize_common(PostcohInspiralTable *row,
                              long event_id,
                              const char *ifos) {
    memset(row, 0, sizeof(*row));
    row->process_id = 17;
    row->event_id = event_id;
    row->is_background = FLAG_FOREGROUND;
    strcpy(row->ifos, ifos);
    strcpy(row->pivotal_ifo,
           strstr(ifos, "H1") ? "H1" :
           (strstr(ifos, "L1") ? "L1" : "V1"));
    row->end_time.gpsSeconds = 1252187900 + (event_id - 4242);
    row->end_time.gpsNanoSeconds = 123456789;
    for (int ifo_id = 0; ifo_id < 3; ++ifo_id) {
        row->end_time_sngl[ifo_id].gpsSeconds = row->end_time.gpsSeconds;
        row->end_time_sngl[ifo_id].gpsNanoSeconds = 111 * (ifo_id + 1);
        row->snglsnr[ifo_id] = 8.5f + ifo_id;
        row->chisq[ifo_id] = 1.25f + 0.25f * ifo_id;
        row->coaphase[ifo_id] = 0.1f * (ifo_id + 1);
        row->deff[ifo_id] = 100.0 * (ifo_id + 1);
    }
    row->bankid = 7;
    row->tmplt_idx = 11;
    row->cohsnr = 12.0f;
    row->fap = 1.0e-7f;
    row->far = 3.0e-6f;
    row->far_1w = 3.0e-6f;
    row->far_1d = 3.0e-6f;
    row->far_2h = 3.0e-6f;
    row->nevent_1w = 1000001;
    row->nevent_1d = 1000001;
    row->nevent_2h = 1000001;
    row->livetime_1w = 604800;
    row->livetime_1d = 86400;
    row->livetime_2h = 7200;
    row->template_duration = 4.0;
    row->mass1 = 1.4f;
    row->mass2 = 1.3f;
    row->mchirp = 1.17f;
    row->mtotal = 2.7f;
    row->eta = 0.24f;
    row->f_final = 1024.0f;
}

static int row_has_ifo(const PostcohInspiralTable *row, int ifo_id) {
    static const char *names[MAX_NIFO] = {"H1", "L1", "V1", "K1"};
    return ifo_id >= 0 && ifo_id < MAX_NIFO &&
           strstr(row->ifos, names[ifo_id]) != NULL;
}

static int add_snr_series(PostcohInspiralTable *row) {
    for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
        if (!row_has_ifo(row, ifo_id)) continue;
        char name[32];
        snprintf(name, sizeof(name), "TEST_%d_%ld", ifo_id, row->event_id);
        COMPLEX8TimeSeries *series = XLALCreateCOMPLEX8TimeSeries(
          name, &row->end_time_sngl[ifo_id], 0.0, 1.0 / 4096.0,
          &lalDimensionlessUnit, 4);
        if (!series) return 0;
        for (size_t sample = 0; sample < series->data->length; ++sample) {
            series->data->data[sample] =
              (float)(sample + 1 + ifo_id) +
              I * (float)(0.25 * (sample + 1));
        }
        row->snr_series_list[ifo_id] = series;
    }
    return 1;
}

static void destroy_all_series(PostcohInspiralTable *rows, size_t count) {
    if (!rows) return;
    for (size_t row_id = 0; row_id < count; ++row_id) {
        for (int ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
            if (rows[row_id].snr_series_list[ifo_id]) {
                XLALDestroyCOMPLEX8TimeSeries(
                  rows[row_id].snr_series_list[ifo_id]);
                rows[row_id].snr_series_list[ifo_id] = NULL;
            }
        }
    }
}

void *crashcar_schema_rows_create(size_t *size_out) {
    const size_t count = 4;
    PostcohInspiralTable *rows = calloc(count, sizeof(*rows));
    if (!rows) return NULL;
    rows[0].is_background = FLAG_EMPTY;
    strcpy(rows[0].ifos, "H1L1V1");
    rows[0].end_time.gpsSeconds = 1252187822;

    PostcohInspiralTable *assigned = &rows[1];
    initialize_common(assigned, 4242, "H1");
    assigned->far_sngl[0] = 1.25e-5f;
    assigned->H1_LLR = 101.25;

    PostcohInspiralTable *pending = &rows[2];
    initialize_common(pending, 4243, "H1");
    pending->far_sngl[0] = 0.0f;
    pending->H1_LLR = 111.5;

    PostcohInspiralTable *multi = &rows[3];
    initialize_common(multi, 4244, "H1L1V1");
    multi->far_sngl[0] = 11.0f;
    multi->far_sngl[1] = 12.0f;
    multi->far_sngl[2] = 13.0f;
    multi->far_1w_sngl[0] = 21.0f;
    multi->far_1w_sngl[1] = 22.0f;
    multi->far_1w_sngl[2] = 23.0f;
    multi->H1_LLR = 301.25;
    multi->L1_LLR = 302.5;

    for (size_t row_id = 1; row_id < count; ++row_id) {
        if (!add_snr_series(&rows[row_id])) {
            destroy_all_series(rows, count);
            free(rows);
            return NULL;
        }
    }
    if (size_out) *size_out = count * sizeof(*rows);
    return rows;
}

void crashcar_schema_rows_release(void *data) {
    free(data);
}

void crashcar_schema_rows_destroy_unclaimed(void *data) {
    PostcohInspiralTable *rows = data;
    destroy_all_series(rows, 4);
    free(rows);
}
