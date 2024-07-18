#ifndef __CUDA_POSTCOH_KERNEL_H__
#define __CUDA_POSTCOH_KERNEL_H__

#include <complex_f.h>
#include <cuda_runtime.h>
#include <postcoh/postcoh_state.h>

void cohsnr_and_chisq(PostcohState *state,
                      unsigned int coh_ifo_bitset,
                      int iifo,
                      int gps_idx,
                      int output_skymap,
                      cudaStream_t stream);

void transpose_snglsnr(COMPLEX_F *idata,
                       COMPLEX_F *odata,
                       int offset,
                       int copy_snglsnr_len,
                       int snglsnr_len,
                       int tmplt_len,
                       cudaStream_t stream);

#endif
