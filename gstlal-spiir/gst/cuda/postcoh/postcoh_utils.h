#ifndef __CUDA_POSTCOH_UTILS_H__
#define __CUDA_POSTCOH_UTILS_H__

#include <postcoh/postcoh.h>
#include <postcoh/postcoh_state.h>

#define POSTCOH_PARAMS_NOT_INIT -1
#define POSTCOH_PARAMS_INIT     1

void cuda_device_print(int deviceCount);

PeakList *create_peak_list(PostcohState *state, cudaStream_t stream);

void get_write_ifo_mapping(const char *ifos, int nifo, int *write_ifo_mapping);

void cuda_postcoh_map_from_xml(char *fname,
                               PostcohState *state,
                               cudaStream_t stream);

void cuda_postcoh_autocorr_from_xml(char *fname,
                                    PostcohState *state,
                                    cudaStream_t stream);

void cuda_postcoh_sigmasq_from_xml(char *fname, PostcohState *state);

void cuda_postcoh_sngl_tmplt_from_xml(char *fname,
                                      SnglInspiralTable **psngl_table);

void state_destroy(PostcohState *state);

#endif
