#ifndef __CUDA_POSTCOH_STATE_H__
#define __CUDA_POSTCOH_STATE_H__

#include <complex_f.h>
#include <ifo_set.h>

// FIXME: consider more flxible structure for PeakList
typedef struct _PeakList {
    int peak_intlen;
    int peak_floatlen;

    /* data in the same type are allocated together */
    int *npeak;
    int *peak_pos;
    int *len_idx;
    int *tmplt_idx;
    int *pix_idx;
    int *pix_idx_bg; // background Ntoff needs this, do not remove
    int *ntoff[MAX_NIFO];

    float *snglsnr[MAX_NIFO];
    float *coaphase[MAX_NIFO];
    float *chisq[MAX_NIFO];

    float *snglsnr_bg[MAX_NIFO];
    float *coaphase_bg[MAX_NIFO];
    float *chisq_bg[MAX_NIFO];

    float *cohsnr;
    float *nullsnr;
    float *cmbchisq;

    float *cohsnr_bg;
    float *nullsnr_bg;
    float *cmbchisq_bg;

    float *cohsnr_skymap;
    float *nullsnr_skymap;

    /* structure on GPU device */
    // It is important to note that pointers on the host device are not
    // exposed to the GPU device. For this reason, we can't allocate d_ntoff,
    // d_snglsnr, etc. here on the stack with sized arrays. Instead, we need
    // to malloc is when PeakList is built.
    int *d_npeak;
    int *d_peak_pos;
    int *d_len_idx;
    int *d_tmplt_idx;
    int *d_pix_idx;
    int *d_pix_idx_bg; // background Ntoff needs this, do not remove
    int **d_ntoff; // size (MAX_NIFO)

    float **d_snglsnr; // size (MAX_NIFO)
    float **d_coaphase; // size (MAX_NIFO)
    float **d_chisq; // size (MAX_NIFO)

    float **d_snglsnr_bg; // size (MAX_NIFO)
    float **d_coaphase_bg; // size (MAX_NIFO)
    float **d_chisq_bg; // size (MAX_NIFO)

    float *d_cohsnr;
    float *d_nullsnr;
    float *d_cmbchisq;

    float *d_cohsnr_bg;
    float *d_nullsnr_bg;
    float *d_cmbchisq_bg;

    float *d_cohsnr_skymap;
    float *d_nullsnr_skymap;

    float *d_peak_tmplt;
    float *d_maxsnglsnr; // for cuda peakfinder, not used now

    float *d_snglsnr_buffer; // we need to copy data from CPU memory to this
                             // buffer; then do transpose for new postcoh kernel
                             // optimized by Xiaoyang Guo
    int len_snglsnr_buffer;
} PeakList;

typedef struct _PostcohState {
    // Redundant, use postcoh equivalent instead
    int head_len;
    int exe_len;
    // Immutable pointer with immutable data outside of init/setcaps
    /* parent pointer in host device, each children pointer is in GPU device,
     * pointing to a detector autocorrelation array in GPU device*/
    COMPLEX_F **dd_autocorr_matrix;
    /* parent pointer in host device, each children pointer is in GPU device,
     * pointing to a detector autocorrealtion norm value in GPU device*/
    float **dd_autocorr_norm;
    /* map the position of detector snr series to the position of output snr
     * instances */
    int *write_ifo_mapping;
    int *d_write_ifo_mapping;
    /* sigmasq read from bank to compute effective distance */
    double **sigmasq;
    char *all_ifos;
    // Immutable outside of init/setcaps
    int nifo;
    int max_npeak;
    int ntmplt;
    float dt;
    float snglsnr_thresh;
    int hist_trials;
    int trial_sample_inv;
    float snglsnr_max[MAX_NIFO];
    int is_member_init;
    // Immutable pointer with immutable data outside of init/setcaps and detrsp
    // refresh
    /* parent pointer in host device, each children pointer is in host device,
     * pointing to the coherent U map of a certain time in GPU device*/
    float **d_U_map;
    /* parent pointer in host device, each children pointer is in host device,
     * pointing to the coherent time arrival diff map of a certain time in GPU
     * device*/
    float **d_diff_map;
    // Immutable outside of init/setcaps and detrsp refresh
    int autochisq_len;
    int snglsnr_len;
    int snglsnr_start_load;
    int snglsnr_start_exe;
    /* map the input sink to the 'enabled_ifo_id' (its index in all_ifos) */
    ifo_set_type enabled_ifos;
    int enabled_ifo_ids[MAX_NIFO];
    int gps_step;
    /* be careful that long has different length in different machines */
    long gps_start;
    unsigned long nside;
    int npix;
    // Immutable pointers with mutable data
    /* parent pointer in host device, each children pointer is in host device,
     * pointing to a detector snglsnr array in GPU device */
    COMPLEX_F **d_snglsnr;
    /* parent pointer in host device, each children pointer is in GPU device,
     * pointing to a detector snglsnr array in GPU device*/
    COMPLEX_F **dd_snglsnr;
    PeakList **peak_list;
    // Mutable
    COMPLEX_F *snr_history_per_template[MAX_NIFO];
} PostcohState;

#endif
