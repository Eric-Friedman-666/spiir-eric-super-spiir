#ifdef __cplusplus
extern "C" {
#endif

// Standard and third party includes
#include <stdbool.h>

// Our includes
#include <postcoh/postcoh.h>

#ifdef __cplusplus
}
#endif

#define POSTCOH_PARAMS_NOT_INIT -1
#define POSTCOH_PARAMS_INIT     1

void cuda_device_print(int deviceCount);

PeakList *create_peak_list(PostcohState *state, cudaStream_t stream);

void get_write_ifo_mapping(char *ifos, int nifo, int *write_ifo_mapping);

void cuda_postcoh_map_from_xml(char *fname,
                               PostcohState *state,
                               cudaStream_t stream);

void cuda_postcoh_load_autocorr_on_gpu(const char *ifo_bankpaths,
                                       PostcohState *state,
                                       bool rescale_chisq_dof,
                                       cudaStream_t stream);

void cuda_postcoh_sigmasq_from_xml(char *fname, PostcohState *state);

void cuda_postcoh_sngl_tmplt_from_xml(char *fname,
                                      SnglInspiralTable **psngl_table);

void peakfinder(PostcohState *state, int iifo, cudaStream_t stream);
void state_destroy(PostcohState *state);

void peak_list_destroy(PeakList *pklist);

void state_reset_npeak(PeakList *pklist);

void cohsnr_and_chisq(PostcohState *state,
                      unsigned int coh_ifo_bitset,
                      int iifo,
                      int gps_idx,
                      int output_skymap,
                      int weight_cmbchisq,
                      cudaStream_t stream);

void cohsnr_and_chisq_background(PostcohState *state,
                                 int iifo,
                                 int hist_trials,
                                 int gps_idx);

void transpose_snglsnr(COMPLEX_F *idata,
                       COMPLEX_F *odata,
                       int offset,
                       int copy_snglsnr_len,
                       int snglsnr_len,
                       int tmplt_len,
                       cudaStream_t stream);
