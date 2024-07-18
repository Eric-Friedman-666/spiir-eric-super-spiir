#ifndef __CUDA_MULTIRATESPIIR_STATE_H__
#define __CUDA_MULTIRATESPIIR_STATE_H__

#include <complex_f.h>

#define SPSTATE(i)     (*(spstate + (i)))
#define SPSTATEDOWN(i) (SPSTATE(i)->downstate)
#define SPSTATEUP(i)   (SPSTATE(i)->upstate)

typedef struct _ResamplerState {
    float *d_sinc_table;
    float *d_mem; /* fixed length to store input */
    float *d_mem_copy; /* fixed length to store input */
    int channels;
    int mem_len;
    int last_sample;
    int filt_len;
    int sinc_len;
    int inrate;
    int outrate;
    float amplifier; /* correction factor for resampling */
} ResamplerState;

typedef struct _SpiirState {
    COMPLEX_F *d_a1;
    COMPLEX_F *d_b0;
    int *d_d;
    int delay_max;
    COMPLEX_F *d_y;

    uint nb;
    int num_filters;
    int num_templates;

    int depth; /* supposed to be 0-6 */
    ResamplerState *downstate, *upstate;
    float
      *d_queue; /* circular buffer (or ring buffer) for downsampler and spiir */
    float *d_out; /* only apply to 0 depth */
    int queue_len;
    int queue_first_sample; /* queue start position */
    int queue_last_sample; /* queue end position */
    int pre_out_spiir_len; /* previous output length for spiir filtering */
} SpiirState;

#endif
