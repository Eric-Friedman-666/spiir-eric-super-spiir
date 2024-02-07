/* GStreamer
 * Copyright (C) Qi Chu,
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Library General Public
 * License as published by the Free Software Foundation; either
 * version 2 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Library General Public License for more deroll-offss.
 *
 * You should have received a copy of the GNU Library General Public
 * License along with this library; if not, write to the
 * Free Software Foundation, Inc., 59 Temple Place - Suite 330,
 * Boston, MA 02111-1307, USA.
 */

// Standard and third party includes
#include <LIGOLwHeader.h>
#include <chealpix.h>
#include <cuda_debug.h>
#include <cuda_runtime.h>

// Suppresses a warning from gstreamer using deprecated mutexes.
// Should be revisited after the gstreamer upgrade.
// See #15
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"
#include <gst/gst.h>
#pragma GCC diagnostic pop

// Our includes
#include <IFOMap.h>
#include <pipe_macro.h>
#include <postcoh/postcoh_utils.h>
#include <postcohtable.h>

//#define __DEBUG__ 0
#define NSNGL_TMPLT_COLS 15

void cuda_device_print(int deviceCount) {
    int dev, driverVersion = 0, runtimeVersion = 0;

    for (dev = 0; dev < deviceCount; ++dev) {
        cudaSetDevice(dev);
#ifdef __cplusplus
        cudaDeviceProp deviceProp;
#else // !__cplusplus
        struct cudaDeviceProp deviceProp;
#endif

        cudaGetDeviceProperties(&deviceProp, dev);

        printf("\nDevice %d: \"%s\"\n", dev, deviceProp.name);

        // Console log
        cudaDriverGetVersion(&driverVersion);
        cudaRuntimeGetVersion(&runtimeVersion);
        printf(
          "  CUDA Driver Version / Runtime Version          %d.%d / %d.%d\n",
          driverVersion / 1000, (driverVersion % 100) / 10,
          runtimeVersion / 1000, (runtimeVersion % 100) / 10);
        printf("  CUDA Capability Major/Minor version number:    %d.%d\n",
               deviceProp.major, deviceProp.minor);

        printf("  (%2d) Multiprocessors \n", deviceProp.multiProcessorCount);

        printf(
          "  GPU Max Clock rate:                            %.0f MHz (%0.2f "
          "GHz)\n",
          deviceProp.clockRate * 1e-3f, deviceProp.clockRate * 1e-6f);

        printf(
          "  Maximum Texture Dimension Size (x,y,z)         1D=(%d), 2D=(%d, "
          "%d), 3D=(%d, %d, %d)\n",
          deviceProp.maxTexture1D, deviceProp.maxTexture2D[0],
          deviceProp.maxTexture2D[1], deviceProp.maxTexture3D[0],
          deviceProp.maxTexture3D[1], deviceProp.maxTexture3D[2]);
        printf("  Maximum Layered 1D Texture Size, (num) layers  1D=(%d), %d "
               "layers\n",
               deviceProp.maxTexture1DLayered[0],
               deviceProp.maxTexture1DLayered[1]);
        printf(
          "  Maximum Layered 2D Texture Size, (num) layers  2D=(%d, %d), %d "
          "layers\n",
          deviceProp.maxTexture2DLayered[0], deviceProp.maxTexture2DLayered[1],
          deviceProp.maxTexture2DLayered[2]);

        printf("  Total amount of constant memory:               %lu bytes\n",
               deviceProp.totalConstMem);
        printf("  Total amount of shared memory per block:       %lu bytes\n",
               deviceProp.sharedMemPerBlock);
        printf("  Total number of registers available per block: %d\n",
               deviceProp.regsPerBlock);
        printf("  Warp size:                                     %d\n",
               deviceProp.warpSize);
        printf("  Maximum number of threads per multiprocessor:  %d\n",
               deviceProp.maxThreadsPerMultiProcessor);
        printf("  Maximum number of threads per block:           %d\n",
               deviceProp.maxThreadsPerBlock);
        printf("  Max dimension size of a thread block (x,y,z): (%d, %d, %d)\n",
               deviceProp.maxThreadsDim[0], deviceProp.maxThreadsDim[1],
               deviceProp.maxThreadsDim[2]);
        printf("  Max dimension size of a grid size    (x,y,z): (%d, %d, %d)\n",
               deviceProp.maxGridSize[0], deviceProp.maxGridSize[1],
               deviceProp.maxGridSize[2]);
        printf("  Maximum memory pitch:                          %lu bytes\n",
               deviceProp.memPitch);
        printf("  Texture alignment:                             %lu bytes\n",
               deviceProp.textureAlignment);
        printf(
          "  Concurrent copy and kernel execution:          %s with %d copy "
          "engine(s)\n",
          (deviceProp.deviceOverlap ? "Yes" : "No"),
          deviceProp.asyncEngineCount);
        printf("  Run time limit on kernels:                     %s\n",
               deviceProp.kernelExecTimeoutEnabled ? "Yes" : "No");
        printf("  Integrated GPU sharing Host Memory:            %s\n",
               deviceProp.integrated ? "Yes" : "No");
        printf("  Support host page-locked memory mapping:       %s\n",
               deviceProp.canMapHostMemory ? "Yes" : "No");
        printf("  Alignment requirement for Surfaces:            %s\n",
               deviceProp.surfaceAlignment ? "Yes" : "No");
        printf("  Device has ECC support:                        %s\n",
               deviceProp.ECCEnabled ? "Enabled" : "Disabled");
        printf("  Device supports Unified Addressing (UVA):      %s\n",
               deviceProp.unifiedAddressing ? "Yes" : "No");
        printf("  Device supports Compute Preemption:            %s\n",
               deviceProp.computePreemptionSupported ? "Yes" : "No");
        printf("  Supports Cooperative Kernel Launch:            %s\n",
               deviceProp.cooperativeLaunch ? "Yes" : "No");
        printf("  Supports MultiDevice Co-op Kernel Launch:      %s\n",
               deviceProp.cooperativeMultiDeviceLaunch ? "Yes" : "No");
        printf(
          "  Device PCI Domain ID / Bus ID / location ID:   %d / %d / %d\n",
          deviceProp.pciDomainID, deviceProp.pciBusID, deviceProp.pciDeviceID);

        const char *sComputeMode[] = {
            "Default (multiple host threads can use ::cudaSetDevice() with "
            "device "
            "simultaneously)",
            "Exclusive (only one host thread in one process is able to use "
            "::cudaSetDevice() with this device)",
            "Prohibited (no host thread can use ::cudaSetDevice() with this "
            "device)",
            "Exclusive Process (many threads in one process is able to use "
            "::cudaSetDevice() with this device)",
            "Unknown",
            NULL
        };
        printf("  Compute Mode:\n");
        printf("     < %s >\n", sComputeMode[deviceProp.computeMode]);
    }
}

/* get ifo indices of a given set of ifos
 * e.g. HV: 0, 2
 */
void get_write_ifo_mapping(char *ifos, int nifo, int *write_ifo_mapping) {
    int iifo, jifo;
    for (iifo = 0; iifo < nifo; iifo++)
        for (jifo = 0; jifo < MAX_NIFO; jifo++)
            if (strncmp(ifos + iifo * IFO_LEN, get_ifo_string(jifo), IFO_LEN)
                == 0) {
                write_ifo_mapping[iifo] = jifo;
                break;
            }
#ifdef __DEBUG__
    for (iifo = 0; iifo < nifo; iifo++)
        printf("write_ifo_mapping %d->%d\n", iifo, write_ifo_mapping[iifo]);
#endif
}
PeakList *create_peak_list(PostcohState *state, cudaStream_t stream) {
    int hist_trials = state->hist_trials;
    g_assert(hist_trials != -1);
    int max_npeak = state->max_npeak;
#ifdef __DEBUG__
    printf("max_npeak %d\n", max_npeak);
#endif
    PeakList *pklist = (PeakList *)malloc(sizeof(PeakList));

    // FIXME: Remove these comments once issue #5 is resolved:
    //        https://git.ligo.org/lscsoft/spiir/-/issues/5
    //        It should ensure the following calculations are automated
    //
    // This calculation is the sum of the sizes of the following variables
    // peak_pos    => max_npeak
    // len_idx     => max_npeak
    // d_tmplt_idx => max_npeak
    // pix_idx     => max_npeak
    // ntoff       => max_npeak * MAX_NIFO
    // pix_idx_bg  => max_npeak * hist_trials
    // npeak       => 1
    int peak_intlen = (4 + MAX_NIFO + hist_trials) * max_npeak + 1;

    // This calculation is the sum of the sizes of the following variables
    // snglsnr     => max_npeak * MAX_NIFO
    // coaphase    => max_npeak * MAX_NIFO
    // chisq       => max_npeak * MAX_NIFO
    // cohsnr      => max_npeak
    // nullsnr     => max_npeak
    // cmbchisq    => max_npeak
    // snglsnr_bg  => max_npeak * hist_trials * MAX_NIFO
    // coaphase_bg => max_npeak * hist_trials * MAX_NIFO
    // chisq_bg    => max_npeak * hist_trials * MAX_NIFO
    // cohsnr_bg   => max_npeak * hist_trials
    // nullsnr_bg  => max_npeak * hist_trials
    // cmbchisq_bg => max_npeak * hist_trials
    int peak_floatlen =
      ((3 * MAX_NIFO + 3) + (hist_trials * (3 * MAX_NIFO + 3))) * max_npeak;
    pklist->peak_intlen   = peak_intlen;
    pklist->peak_floatlen = peak_floatlen;

    // Why do we use `cudaMallocManaged()` sometimes below? Well, a large
    // number of the below pointers are to 2D arrays that we won't be accessing
    // after setting up, but whilst we're setting them up they need to be able
    // to be accessed on the CPU. `cudaMallocManaged()` allows us to access the
    // memory involved on both the CPU and GPU, which means that we can assign
    // the pointers here instead of having to do an awkward `cudaMemcpyAsync()`
    // to copy across the right pointer.
    //
    // It's likely that there will be a small performance hit for having used
    // `cudaMallocManaged()` instead of doing an async copy, so if its needed
    // to, the below code can definitely can be changed and will work with
    // `cudaMemcpyAsync()`.
    //
    // FIXME: Move to `cudaMemcpyAsync()` instead of `cudaMallocManaged()` for a
    // slight performance bump

    /* create device space for peak list for int-type variables */
    CUDA_CHECK(
      cudaMalloc((void **)&(pklist->d_npeak), sizeof(int) * peak_intlen));
    CUDA_CHECK(
      cudaMemsetAsync(pklist->d_npeak, 0, sizeof(int) * peak_intlen, stream));
    pklist->d_peak_pos   = pklist->d_npeak + 1 + 0 * max_npeak;
    pklist->d_len_idx    = pklist->d_npeak + 1 + 1 * max_npeak;
    pklist->d_tmplt_idx  = pklist->d_npeak + 1 + 2 * max_npeak;
    pklist->d_pix_idx    = pklist->d_npeak + 1 + 3 * max_npeak;
    pklist->d_pix_idx_bg = pklist->d_npeak + 1 + 4 * max_npeak;
    CUDA_CHECK(cudaMallocManaged((void **)&(pklist->d_ntoff),
                                 sizeof(int *) * MAX_NIFO,
                                 cudaMemAttachGlobal));
    for (int i = 0; i < MAX_NIFO; ++i) {
        pklist->d_ntoff[i] =
          pklist->d_npeak + 1 + ((4 + hist_trials + i) * max_npeak);
    }

    // printf("d_npeak %p\n", pklist->d_npeak);
    // CUDA_CHECK(cudaMemsetAsync(pklist->d_npeak, 0, sizeof(int), stream));

    /* create device space for peak list for float-type variables */
    CUDA_CHECK(cudaMallocManaged((void **)&(pklist->d_snglsnr),
                                 sizeof(float *) * MAX_NIFO,
                                 cudaMemAttachGlobal));
    CUDA_CHECK(cudaMallocManaged((void **)&(pklist->d_coaphase),
                                 sizeof(float *) * MAX_NIFO,
                                 cudaMemAttachGlobal));
    CUDA_CHECK(cudaMallocManaged((void **)&(pklist->d_chisq),
                                 sizeof(float *) * MAX_NIFO,
                                 cudaMemAttachGlobal));
    CUDA_CHECK(cudaMallocManaged((void **)&(pklist->d_snglsnr_bg),
                                 sizeof(float *) * MAX_NIFO,
                                 cudaMemAttachGlobal));
    CUDA_CHECK(cudaMallocManaged((void **)&(pklist->d_coaphase_bg),
                                 sizeof(float *) * MAX_NIFO,
                                 cudaMemAttachGlobal));
    CUDA_CHECK(cudaMallocManaged((void **)&(pklist->d_chisq_bg),
                                 sizeof(float *) * MAX_NIFO,
                                 cudaMemAttachGlobal));

    CUDA_CHECK(cudaMalloc((void **)&(pklist->d_snglsnr[0]),
                          sizeof(float) * peak_floatlen));
    CUDA_CHECK(cudaMemsetAsync(pklist->d_snglsnr[0], 0,
                               sizeof(float) * peak_floatlen, stream));

    for (int i = 0; i < MAX_NIFO; ++i) {
        pklist->d_snglsnr[i] =
          pklist->d_snglsnr[0] + (max_npeak * (i + 0 * MAX_NIFO));
        pklist->d_coaphase[i] =
          pklist->d_snglsnr[0] + (max_npeak * (i + 1 * MAX_NIFO));
        pklist->d_chisq[i] =
          pklist->d_snglsnr[0] + (max_npeak * (i + 2 * MAX_NIFO));
    }

    pklist->d_cohsnr   = pklist->d_snglsnr[0] + (3 * MAX_NIFO + 0) * max_npeak;
    pklist->d_nullsnr  = pklist->d_snglsnr[0] + (3 * MAX_NIFO + 1) * max_npeak;
    pklist->d_cmbchisq = pklist->d_snglsnr[0] + (3 * MAX_NIFO + 2) * max_npeak;

    for (int i = 0; i < MAX_NIFO; ++i) {
        pklist->d_snglsnr_bg[i] =
          pklist->d_snglsnr[0]
          + (max_npeak
             * ((3 * MAX_NIFO + 3) + (hist_trials * (i + 0 * MAX_NIFO))));
        pklist->d_coaphase_bg[i] =
          pklist->d_snglsnr[0]
          + (max_npeak
             * ((3 * MAX_NIFO + 3) + (hist_trials * (i + 1 * MAX_NIFO))));
        pklist->d_chisq_bg[i] =
          pklist->d_snglsnr[0]
          + (max_npeak
             * ((3 * MAX_NIFO + 3) + (hist_trials * (i + 2 * MAX_NIFO))));
    }

    pklist->d_cohsnr_bg =
      pklist->d_snglsnr[0]
      + (max_npeak * ((3 * MAX_NIFO + 3) + (hist_trials * (3 * MAX_NIFO + 0))));
    pklist->d_nullsnr_bg =
      pklist->d_snglsnr[0]
      + (max_npeak * ((3 * MAX_NIFO + 3) + (hist_trials * (3 * MAX_NIFO + 1))));
    pklist->d_cmbchisq_bg =
      pklist->d_snglsnr[0]
      + (max_npeak * ((3 * MAX_NIFO + 3) + (hist_trials * (3 * MAX_NIFO + 2))));

    /* create host space for peak list for int-type variables */
    CUDA_CHECK(
      cudaMallocHost((void **)&(pklist->npeak), sizeof(int) * peak_intlen));
    memset(pklist->npeak, 0, sizeof(int) * peak_intlen);
    pklist->peak_pos   = pklist->npeak + 1;
    pklist->len_idx    = pklist->npeak + 1 + max_npeak;
    pklist->tmplt_idx  = pklist->npeak + 1 + 2 * max_npeak;
    pklist->pix_idx    = pklist->npeak + 1 + 3 * max_npeak;
    pklist->pix_idx_bg = pklist->npeak + 1 + 4 * max_npeak;
    for (int i = 0; i < MAX_NIFO; ++i) {
        pklist->ntoff[i] =
          pklist->npeak + 1 + (4 + hist_trials + i) * max_npeak;
    }

    /* create host space for peak list for float-type variables */
    CUDA_CHECK(cudaMallocHost((void **)&(pklist->snglsnr[0]),
                              sizeof(float) * peak_floatlen));
    memset(pklist->snglsnr[0], 0, sizeof(float) * peak_floatlen);
    for (int i = 0; i < MAX_NIFO; ++i) {
        pklist->snglsnr[i] =
          pklist->snglsnr[0] + (max_npeak * (i + 0 * MAX_NIFO));
        pklist->coaphase[i] =
          pklist->snglsnr[0] + (max_npeak * (i + 1 * MAX_NIFO));
        pklist->chisq[i] =
          pklist->snglsnr[0] + (max_npeak * (i + 2 * MAX_NIFO));
    }

    pklist->cohsnr   = pklist->snglsnr[0] + (3 * MAX_NIFO + 0) * max_npeak;
    pklist->nullsnr  = pklist->snglsnr[0] + (3 * MAX_NIFO + 1) * max_npeak;
    pklist->cmbchisq = pklist->snglsnr[0] + (3 * MAX_NIFO + 2) * max_npeak;

    for (int i = 0; i < MAX_NIFO; ++i) {
        pklist->snglsnr_bg[i] =
          pklist->snglsnr[0]
          + (max_npeak
             * ((3 * MAX_NIFO + 3) + (hist_trials * (i + 0 * MAX_NIFO))));
        pklist->coaphase_bg[i] =
          pklist->snglsnr[0]
          + (max_npeak
             * ((3 * MAX_NIFO + 3) + (hist_trials * (i + 1 * MAX_NIFO))));
        pklist->chisq_bg[i] =
          pklist->snglsnr[0]
          + (max_npeak
             * ((3 * MAX_NIFO + 3) + (hist_trials * (i + 2 * MAX_NIFO))));
    }

    pklist->cohsnr_bg =
      pklist->snglsnr[0]
      + (max_npeak * ((3 * MAX_NIFO + 3) + (hist_trials * (3 * MAX_NIFO + 0))));
    pklist->nullsnr_bg =
      pklist->snglsnr[0]
      + (max_npeak * ((3 * MAX_NIFO + 3) + (hist_trials * (3 * MAX_NIFO + 1))));
    pklist->cmbchisq_bg =
      pklist->snglsnr[0]
      + (max_npeak * ((3 * MAX_NIFO + 3) + (hist_trials * (3 * MAX_NIFO + 2))));

    /* temporary struct to store tmplt max in one max_npeak data */
    CUDA_CHECK(cudaMalloc((void **)&(pklist->d_peak_tmplt),
                          sizeof(float) * state->ntmplt));
    CUDA_CHECK(cudaMemsetAsync(pklist->d_peak_tmplt, 0,
                               sizeof(float) * state->ntmplt, stream));

    // add for new postcoh kernel optimized by Xiaoyang Guo
    pklist->d_snglsnr_buffer   = NULL;
    pklist->len_snglsnr_buffer = 0;

    int mem_alloc_size = sizeof(float) * state->npix * 2;
    printf("alloc cohsnr_skymap size %f MB\n", (float)mem_alloc_size / 1000000);

    CUDA_CHECK(cudaMalloc((void **)&(pklist->d_cohsnr_skymap), mem_alloc_size));
    CUDA_CHECK(
      cudaMemsetAsync(pklist->d_cohsnr_skymap, 0, mem_alloc_size, stream));
    pklist->d_nullsnr_skymap = pklist->d_cohsnr_skymap + state->npix;

    CUDA_CHECK(
      cudaMallocHost((void **)&(pklist->cohsnr_skymap), mem_alloc_size));
    memset(pklist->cohsnr_skymap, 0, mem_alloc_size);
    pklist->nullsnr_skymap = pklist->cohsnr_skymap + state->npix;

    CUDA_CHECK(cudaStreamSynchronize(stream));
    CUDA_CHECK(cudaPeekAtLastError());

    return pklist;
}

void cuda_postcoh_sigmasq_from_xml(char *fname, PostcohState *state) {

    gchar **isigma, **sigma_fnames = g_strsplit(fname, ",", -1);
    int mem_alloc_size = 0, ntmplt = 0, nifo = 0;

    /* parsing for nifo */
    for (isigma = sigma_fnames; *isigma; isigma++) nifo++;

#ifdef __DEBUG__
    printf("sigma from %d ifo\n", nifo);
#endif

    XmlNodeStruct *xns      = (XmlNodeStruct *)malloc(sizeof(XmlNodeStruct));
    XmlArray *array_sigmasq = (XmlArray *)malloc(sizeof(XmlArray));

    state->sigmasq   = (double **)malloc(sizeof(double *) * nifo);
    double **sigmasq = state->sigmasq;
    sigmasq[0]       = NULL;

    sprintf((char *)xns[0].tag, "sigmasq:array");
    xns[0].processPtr = readArray;
    xns[0].data       = &(array_sigmasq[0]);
    /* used for sanity check the lengths of sigmasq arrays should be equal */
    int last_dimension = -1;

    /* parsing for all_ifos */
    int num_parsed_ifos = 0;
    for (int i = 0; i < MAX_NIFO; i++) {
        // Find sigma_fname that matches ifo i, if any
        gchar *matched_fname = NULL;
        for (isigma = sigma_fnames; *isigma; isigma++) {
            if (strncmp(*isigma, get_ifo_string(i), IFO_LEN) == 0) {
                matched_fname = *isigma;
            }
        }
        if (!matched_fname) continue;
        int ifo_ind            = num_parsed_ifos++;
        gchar **this_ifo_split = g_strsplit(matched_fname, ":", -1);
        parseFile(this_ifo_split[1], xns, 1);

        ntmplt = array_sigmasq[0].dim[0];
// ifo sets like HL, ifo_ind will still be like 0:H,1:L
// ifo sets like HV, ifo_ind will still be like 0:H,1:V
#ifdef __DEBUG__
        printf("this sigma %s, this ifo %s, ifo ind %d,ntmplt %d \n", *isigma,
               this_ifo_split[0], ifo_ind, ntmplt);
#endif

        /* check if the lengths of sigmasq arrays are equal for all
         * detectors */
        if (last_dimension == -1) {
            /* allocate memory for sigmasq for all detectors upon the
             * first detector ntmplt */
            last_dimension = ntmplt;
            mem_alloc_size = sizeof(double) * ntmplt;
            for (int i = 0; i < nifo; i++) {
                sigmasq[i] = (double *)malloc(mem_alloc_size);
                memset(sigmasq[i], 0, mem_alloc_size);
            }
        } else if (last_dimension != ntmplt) {
            fprintf(stderr, "reading different lengths of sigmasq arrays from "
                            "different detectors, should exit\n");
            exit(0);
        }

        for (int j = 0; j < ntmplt; j++) {
            sigmasq[ifo_ind][j] =
              (double)((double *)(array_sigmasq[0].data))[j];
#ifdef __DEBUG__
            printf("ifo ind %d, template %d: %f\n", ifo_ind, j,
                   sigmasq[ifo_ind][j]);
#endif
        }

        freeArraydata(array_sigmasq);
        /*
         * Cleanup function for the XML library.
         */
        xmlCleanupParser();
        /*
         * this is to debug memory for regression tests
         */
        xmlMemoryDump();
        g_strfreev(this_ifo_split);
    }
    /* free memory */
    g_strfreev(sigma_fnames);
    free(array_sigmasq);
    free(xns);
}

void cuda_postcoh_map_from_xml(char *fname,
                               PostcohState *state,
                               cudaStream_t stream) {
    // FIXME: sanity check that the size of U matrix and diff matrix for
    // each sky pixel is consistent with number of detectors
    // printf("read map from xml\n");
    /* first get the params */
    XmlNodeStruct *xns = (XmlNodeStruct *)malloc(sizeof(XmlNodeStruct) * 3);
    XmlParam param_gps_step  = { 0, NULL };
    XmlParam param_gps_start = { 0, NULL };
    XmlParam param_order     = { 0, NULL };

    sprintf((char *)xns[0].tag, "gps_step:param");
    xns[0].processPtr = readParam;
    xns[0].data       = &param_gps_step;

    sprintf((char *)xns[1].tag, "gps_start:param");
    xns[1].processPtr = readParam;
    xns[1].data       = &param_gps_start;

    sprintf((char *)xns[2].tag, "chealpix_order:param");
    xns[2].processPtr = readParam;
    xns[2].data       = &param_order;
    // printf("read in detrsp map from xml %s\n", fname);

    parseFile(fname, xns, 3);
    /*
     * Cleanup function for the XML library.
     */
    xmlCleanupParser();
    /*
     * this is to debug memory for regression tests
     */
    xmlMemoryDump();

    /* assign basic information to state */
    int gps_step_new = *((int *)param_gps_step.data);
    if (state->npix != POSTCOH_PARAMS_NOT_INIT
        && state->gps_step != gps_step_new) {
        fprintf(
          stderr,
          "detrsp map has a different configuration than last read, aboring!");
        exit(0);
    }
    state->gps_step     = *((int *)param_gps_step.data);
    state->gps_start    = *((long *)param_gps_start.data);
    unsigned long nside = (unsigned long)1 << *((int *)param_order.data);
    state->nside        = nside;

    /* free memory */
    free(param_gps_step.data);
    free(param_gps_start.data);
    param_gps_step.data  = NULL;
    param_gps_start.data = NULL;
    free(param_order.data);
    param_order.data = NULL;
    free(xns);

    int gps = 0, gps_end = 24 * 3600;
    int ngps = gps_end / (state->gps_step);

    xns = (XmlNodeStruct *)malloc(sizeof(XmlNodeStruct) * 2 * ngps);
    if (state->npix == POSTCOH_PARAMS_NOT_INIT) {
        state->d_U_map    = (float **)malloc(sizeof(float *) * ngps);
        state->d_diff_map = (float **)malloc(sizeof(float *) * ngps);
    }

    int i;
    XmlArray *array_u    = (XmlArray *)malloc(sizeof(XmlArray) * ngps);
    XmlArray *array_diff = (XmlArray *)malloc(sizeof(XmlArray) * ngps);

    for (i = 0; i < ngps; i++) {

        sprintf((char *)xns[i].tag, "U_map_gps_%d:array", gps);
        xns[i].processPtr = readArray;
        xns[i].data       = &(array_u[i]);

        sprintf((char *)xns[i + ngps].tag, "diff_map_gps_%d:array", gps);
        xns[i + ngps].processPtr = readArray;
        xns[i + ngps].data       = &(array_diff[i]);
        gps += state->gps_step;
    }

    parseFile(fname, xns, 2 * ngps);

    int mem_alloc_size = sizeof(float) * array_u[0].dim[0] * array_u[0].dim[1];
    for (i = 0; i < ngps; i++) {
        if (state->npix == POSTCOH_PARAMS_NOT_INIT) {
            CUDA_CHECK(
              cudaMalloc((void **)&(state->d_U_map[i]), mem_alloc_size));
            CUDA_CHECK(
              cudaMalloc((void **)&(state->d_diff_map[i]), mem_alloc_size));
        }
        CUDA_CHECK(cudaMemcpyAsync(state->d_U_map[i], array_u[i].data,
                                   mem_alloc_size, cudaMemcpyHostToDevice,
                                   stream));
        CUDA_CHECK(cudaMemcpyAsync(state->d_diff_map[i], array_diff[i].data,
                                   mem_alloc_size, cudaMemcpyHostToDevice,
                                   stream));
    }
    /*
     * Cleanup function for the XML library.
     */
    xmlCleanupParser();
    /*
     * this is to debug memory for regression tests
     */
    xmlMemoryDump();

    /* label that the map has been initialized, no longer
     * POSTCOH_PARAMS_NOT_INIT */
    state->npix = nside2npix(nside);

    /* free memory */
    for (i = 0; i < ngps; i++) {
        free(array_u[i].data);
        free(array_diff[i].data);
    }
    free(array_u);
    free(array_diff);
    free(xns);
}

/**
 * @brief Write the autocorrelation and normalization to GPU for
 * the provided template banks.
 *
 * The autocorrelation data is provided in the template banks
 * themselves, while the normalization (degrees of freedom) are
 * calculated using that data.
 *
 * @param ifo_bankpaths a comma separated list of the bank
 * filepaths for each ifo, with a colon separating IFO and bank.
 * e.g. "H1:path/to/bank.xml,L1:path/to/other_bank.xml"
 * @param state The postcoh state storing pointers to gpu memory,
 * and autochisq_len.
 * @param stream The cuda stream.
 */
void cuda_postcoh_load_autocorr_on_gpu(const char *ifo_bankpaths,
                                       PostcohState *state,
                                       bool rescale_chisq_dof,
                                       cudaStream_t stream) {
#ifdef __DEBUG__
    printf("read in autocorrelation from xml %s\n", ifo_bankpaths);
#endif

    gchar **split_ifo_bankpaths = g_strsplit(ifo_bankpaths, ",", -1);
    int nifo                    = g_strv_length(split_ifo_bankpaths);

#ifdef __DEBUG__
    printf("autocorrelation from %d ifos.\n", nifo);
#endif

    XmlNodeStruct *bank_xml =
      (XmlNodeStruct *)malloc(sizeof(XmlNodeStruct) * 2);
    XmlArray *parsed_arrays = (XmlArray *)malloc(sizeof(XmlArray) * 2);

    CUDA_CHECK(cudaMalloc((void **)&(state->dd_autocorr_matrix),
                          sizeof(COMPLEX_F *) * nifo));
    CUDA_CHECK(
      cudaMalloc((void **)&(state->dd_autocorr_norm), sizeof(float *) * nifo));

    sprintf((char *)bank_xml[0].tag, "autocorrelation_bank_real:array");
    bank_xml[0].processPtr = readArray;
    bank_xml[0].data       = &(parsed_arrays[0]);

    sprintf((char *)bank_xml[1].tag, "autocorrelation_bank_imag:array");
    bank_xml[1].processPtr = readArray;
    bank_xml[1].data       = &(parsed_arrays[1]);

    COMPLEX_F **bank_autocorr =
      (COMPLEX_F **)malloc(sizeof(COMPLEX_F *) * nifo);
    float **bank_normalization = (float **)malloc(sizeof(float *) * nifo);

    // Read the autocorrelation array for each IFO and write to GPU
    COMPLEX_F *ifo_autocorr  = NULL;
    float *ifo_normalization = NULL;
    int num_templates;
    int autochisq_len;
    int ifo_autocorr_size;
    for (int enabled_ifo_id = 0; enabled_ifo_id < nifo; enabled_ifo_id++) {
        // Parse the IFO and bank filepath from "ifo:bankpath"
        int ifo_id = -1;
        char ifo_string[IFO_LEN + 1];
        strncpy(ifo_string, split_ifo_bankpaths[enabled_ifo_id], IFO_LEN);
        ifo_string[IFO_LEN] = '\0';
        if (!try_get_ifo_id(ifo_string, &ifo_id)) {
            fprintf(stderr, "Could not find an IFO matching %s in bank %s\n",
                    ifo_string, split_ifo_bankpaths[enabled_ifo_id]);
            exit(1);
        }
        int delimiter_len = 1;
        gchar *bank_filepath =
          split_ifo_bankpaths[enabled_ifo_id] + IFO_LEN + delimiter_len;

        // Read the IFO's autocorrelation arrays from its bankfile.
        parseFile(bank_filepath, bank_xml, 2);

        // Read the autocorrelation dimensions from the first IFO.
        if (ifo_autocorr == NULL) {
            autochisq_len = parsed_arrays[0].dim[0];
            num_templates = parsed_arrays[0].dim[1];
            ifo_autocorr_size =
              sizeof(COMPLEX_F) * num_templates * autochisq_len;
            ifo_autocorr      = (COMPLEX_F *)malloc(ifo_autocorr_size);
            ifo_normalization = (float *)malloc(sizeof(float) * num_templates);

            state->autochisq_len = autochisq_len;
        } else if (autochisq_len != parsed_arrays[0].dim[0]
                   || num_templates != parsed_arrays[0].dim[1]) {
            fprintf(
              stderr,
              "All IFO banks must have the same autochisq length and number of "
              "templates.\n"
              "The first IFO in %s has '%d' autochiq_len and '%d' templates,\n"
              "but IFO '%s' has '%d' autochiq_len and '%d' templates. "
              "Exiting.\n",
              ifo_bankpaths, autochisq_len, num_templates, ifo_string,
              parsed_arrays[0].dim[0], parsed_arrays[0].dim[1]);
            exit(1);
        }
#ifdef __DEBUG__
        printf("this filename %s, this ifo %s, enabled_ifo_id %d, "
               "num_templates %d, autochisq_len %d \n",
               matched_ifo_bankname, ifo_string, enabled_ifo_id, num_templates,
               autochisq_len);
#endif

        // Record autocorrelation and normalization in CPU memory as floats
        double *autocorr_res = parsed_arrays[0].data;
        double *autocorr_ims = parsed_arrays[1].data;
        memset(ifo_normalization, 0, sizeof(float) * num_templates);
        for (int i_template = 0; i_template < num_templates; i_template++) {
            for (int i_sample = 0; i_sample < autochisq_len; i_sample++) {
                size_t original_idx   = i_sample * num_templates + i_template;
                size_t transposed_idx = i_template * autochisq_len + i_sample;
                float autocorr_re     = (float)autocorr_res[original_idx];
                float autocorr_im     = (float)autocorr_ims[original_idx];

                ifo_autocorr[transposed_idx].re = autocorr_re;
                ifo_autocorr[transposed_idx].im = autocorr_im;

                float autocorr_mag =
                  (autocorr_re * autocorr_re + autocorr_im * autocorr_im);
                if (rescale_chisq_dof) {
                    ifo_normalization[i_template] += 2 - 2 * autocorr_mag;
                } else {
                    ifo_normalization[i_template] += 2 - autocorr_mag;
                }
            }
#ifdef __DEBUG__
            printf("ifo ind %d, norm %d: %f\n", enabled_ifo_id, i_template,
                   ifo_normalization[i_template]);
#endif
        }
        /* copy the autocorrelation array to GPU device;
         * copy the array address to GPU device */
        CUDA_CHECK(cudaMalloc((void **)&(bank_autocorr[enabled_ifo_id]),
                              ifo_autocorr_size));
        CUDA_CHECK(cudaMemcpyAsync(bank_autocorr[enabled_ifo_id], ifo_autocorr,
                                   ifo_autocorr_size, cudaMemcpyHostToDevice,
                                   stream));
        CUDA_CHECK(cudaMemcpyAsync(&(state->dd_autocorr_matrix[enabled_ifo_id]),
                                   &(bank_autocorr[enabled_ifo_id]),
                                   sizeof(COMPLEX_F *), cudaMemcpyHostToDevice,
                                   stream));

        CUDA_CHECK(cudaMalloc((void **)&(bank_normalization[enabled_ifo_id]),
                              sizeof(float) * num_templates));
        CUDA_CHECK(cudaMemcpyAsync(
          bank_normalization[enabled_ifo_id], ifo_normalization,
          sizeof(float) * num_templates, cudaMemcpyHostToDevice, stream));
        CUDA_CHECK(cudaMemcpyAsync(&(state->dd_autocorr_norm[enabled_ifo_id]),
                                   &(bank_normalization[enabled_ifo_id]),
                                   sizeof(float *), cudaMemcpyHostToDevice,
                                   stream));

        // Free the CPU memory
        freeArraydata(parsed_arrays);
        freeArraydata(parsed_arrays + 1);
        /*
         * Cleanup function for the XML library.
         */
        xmlCleanupParser();
        /*
         * this is to debug memory for regression tests
         */
        xmlMemoryDump();
    }

    /* free memory */
    g_strfreev(split_ifo_bankpaths);
    free(parsed_arrays);
    free(ifo_autocorr);
    free(ifo_normalization);
    free(bank_autocorr);
    free(bank_normalization);
}

char *ColNames[] = { "sngl_inspiral:template_duration",
                     "sngl_inspiral:mass1",
                     "sngl_inspiral:mass2",
                     "sngl_inspiral:mchirp",
                     "sngl_inspiral:mtotal",
                     "sngl_inspiral:spin1x",
                     "sngl_inspiral:spin1y",
                     "sngl_inspiral:spin1z",
                     "sngl_inspiral:spin2x",
                     "sngl_inspiral:spin2y",
                     "sngl_inspiral:spin2z",
                     "sngl_inspiral:eta",
                     "sngl_inspiral:end_time",
                     "sngl_inspiral:end_time_ns",
                     "sngl_inspiral:f_final" };

void cuda_postcoh_sngl_tmplt_from_xml(char *fname,
                                      SnglInspiralTable **psngl_table) {
#ifdef __DEBUG__
    printf("read in sngl tmplt from %s\n", fname);
#endif

    XmlNodeStruct *xns = (XmlNodeStruct *)malloc(sizeof(XmlNodeStruct));
    XmlTable *xtable   = (XmlTable *)malloc(sizeof(XmlTable));

    xtable->names      = NULL;
    xtable->type_names = NULL;

    strncpy((char *)xns->tag, "sngl_inspiral:table", XMLSTRMAXLEN);
    xns->processPtr = readTable;
    xns->data       = xtable;

    parseFile(fname, xns, 1);

    /*
     * Cleanup function for the XML library.
     */
    xmlCleanupParser();
    /*
     * this is to debug memory for regression tests
     */
    xmlMemoryDump();

    GHashTable *hash = xtable->hashContent;
    GString **col_names =
      (GString **)malloc(sizeof(GString *) * NSNGL_TMPLT_COLS);
    unsigned icol, jlen;
    for (icol = 0; icol < NSNGL_TMPLT_COLS; icol++) {
        col_names[icol] = g_string_new(ColNames[icol]);
    }
    XmlHashVal *val = g_hash_table_lookup(hash, (gpointer)col_names[0]);
    *psngl_table =
      (SnglInspiralTable *)malloc(sizeof(SnglInspiralTable) * (val->data->len));
    SnglInspiralTable *sngl_table = *psngl_table;
    for (jlen = 0; jlen < val->data->len; jlen++)
        sngl_table[jlen].template_duration =
          g_array_index(val->data, double, jlen);

    val = g_hash_table_lookup(hash, col_names[1]);
    for (jlen = 0; jlen < val->data->len; jlen++)
        sngl_table[jlen].mass1 = g_array_index(val->data, float, jlen);

    val = g_hash_table_lookup(hash, col_names[2]);
    for (jlen = 0; jlen < val->data->len; jlen++)
        sngl_table[jlen].mass2 = g_array_index(val->data, float, jlen);

    val = g_hash_table_lookup(hash, col_names[3]);
    for (jlen = 0; jlen < val->data->len; jlen++)
        sngl_table[jlen].mchirp = g_array_index(val->data, float, jlen);

    val = g_hash_table_lookup(hash, col_names[4]);
    for (jlen = 0; jlen < val->data->len; jlen++)
        sngl_table[jlen].mtotal = g_array_index(val->data, float, jlen);

    val = g_hash_table_lookup(hash, col_names[5]);
    for (jlen = 0; jlen < val->data->len; jlen++)
        sngl_table[jlen].spin1x = g_array_index(val->data, float, jlen);

    val = g_hash_table_lookup(hash, col_names[6]);
    for (jlen = 0; jlen < val->data->len; jlen++)
        sngl_table[jlen].spin1y = g_array_index(val->data, float, jlen);

    val = g_hash_table_lookup(hash, col_names[7]);
    for (jlen = 0; jlen < val->data->len; jlen++)
        sngl_table[jlen].spin1z = g_array_index(val->data, float, jlen);

    val = g_hash_table_lookup(hash, col_names[8]);
    for (jlen = 0; jlen < val->data->len; jlen++)
        sngl_table[jlen].spin2x = g_array_index(val->data, float, jlen);

    val = g_hash_table_lookup(hash, col_names[9]);
    for (jlen = 0; jlen < val->data->len; jlen++)
        sngl_table[jlen].spin2y = g_array_index(val->data, float, jlen);

    val = g_hash_table_lookup(hash, col_names[10]);
    for (jlen = 0; jlen < val->data->len; jlen++)
        sngl_table[jlen].spin2z = g_array_index(val->data, float, jlen);

    val = g_hash_table_lookup(hash, col_names[11]);
    for (jlen = 0; jlen < val->data->len; jlen++)
        sngl_table[jlen].eta = g_array_index(val->data, float, jlen);

    val = g_hash_table_lookup(hash, col_names[12]);
    for (jlen = 0; jlen < val->data->len; jlen++)
        sngl_table[jlen].end.gpsSeconds = g_array_index(val->data, int, jlen);

    val = g_hash_table_lookup(hash, col_names[13]);
    for (jlen = 0; jlen < val->data->len; jlen++) {
        sngl_table[jlen].end.gpsNanoSeconds =
          g_array_index(val->data, int, jlen);
#ifdef __DEBUG__
        printf("read %d, end gps %d, end nano %d\n", jlen,
               sngl_table[jlen].end.gpsSeconds,
               sngl_table[jlen].end.gpsNanoSeconds);
#endif
    }

    val = g_hash_table_lookup(hash, col_names[14]);
    for (jlen = 0; jlen < val->data->len; jlen++)
        sngl_table[jlen].f_final = g_array_index(val->data, float, jlen);

    /* free memory */
    freeTable(xtable);
    free(xtable);
    free(xns);
    for (icol = 0; icol < NSNGL_TMPLT_COLS; icol++)
        g_string_free(col_names[icol], TRUE);
    free(col_names);
}

static void sigmasq_destroy(PostcohState *state) {
    int iifo;
    if (state->sigmasq != NULL) {
        for (iifo = 0; iifo < state->nifo; iifo++) {
            if (state->sigmasq[iifo] != NULL) free(state->sigmasq[iifo]);
        }
        free(state->sigmasq);
    }
}

static void map_destroy(PostcohState *state) {
    int i, ngps = 24 * 3600 / state->gps_step;
    for (i = 0; i < ngps; i++) {
        CUDA_CHECK(cudaFree(state->d_U_map[i]));
        CUDA_CHECK(cudaFree(state->d_diff_map[i]));
    }

    free(state->d_U_map);
    free(state->d_diff_map);
}

static void autocorr_destroy(PostcohState *state) {
    int iifo;
    if (state->dd_autocorr_matrix != NULL) {
        for (iifo = 0; iifo < state->nifo; iifo++) {
            if (state->dd_autocorr_matrix[iifo] != NULL)
                cudaFree(state->dd_autocorr_matrix[iifo]);
            cudaFree(state->dd_autocorr_norm[iifo]);
        }
        cudaFree(state->dd_autocorr_matrix);
        cudaFree(state->dd_autocorr_norm);
    }
}

void peak_list_destroy(PeakList *pklist) {

    CUDA_CHECK(cudaFree(pklist->d_npeak));
    CUDA_CHECK(cudaFree(pklist->d_snglsnr[0]));
    CUDA_CHECK(cudaFree(pklist->d_snglsnr));
    CUDA_CHECK(cudaFree(pklist->d_coaphase));
    CUDA_CHECK(cudaFree(pklist->d_chisq));
    CUDA_CHECK(cudaFree(pklist->d_snglsnr_bg));
    CUDA_CHECK(cudaFree(pklist->d_coaphase_bg));
    CUDA_CHECK(cudaFree(pklist->d_chisq_bg));
    CUDA_CHECK(cudaFree(pklist->d_peak_tmplt));
    CUDA_CHECK(cudaFree(pklist->d_cohsnr_skymap));

    CUDA_CHECK(cudaFreeHost(pklist->npeak));
    CUDA_CHECK(cudaFreeHost(pklist->snglsnr[0]));
    CUDA_CHECK(cudaFreeHost(pklist->snglsnr));
    CUDA_CHECK(cudaFreeHost(pklist->coaphase));
    CUDA_CHECK(cudaFreeHost(pklist->chisq));
    CUDA_CHECK(cudaFreeHost(pklist->snglsnr_bg));
    CUDA_CHECK(cudaFreeHost(pklist->coaphase_bg));
    CUDA_CHECK(cudaFreeHost(pklist->chisq_bg));
    CUDA_CHECK(cudaFreeHost(pklist->cohsnr_skymap));
}

void state_destroy(PostcohState *state) {
    int i;
    if (state->is_member_init != POSTCOH_PARAMS_NOT_INIT) {
        for (i = 0; i < state->nifo; i++) {
            CUDA_CHECK(cudaFree(state->dd_snglsnr[i]));
            CUDA_CHECK(cudaFree(state->dd_autocorr_matrix[i]));
            CUDA_CHECK(cudaFree(state->dd_autocorr_norm[i]));
        }

        CUDA_CHECK(cudaFree(state->dd_snglsnr));
        CUDA_CHECK(cudaFree(state->dd_autocorr_matrix));
        CUDA_CHECK(cudaFree(state->dd_autocorr_norm));

        int gps_end = 24 * 3600;
        int ngps    = gps_end / (state->gps_step);
        for (i = 0; i < ngps; i++) {
            CUDA_CHECK(cudaFree(state->d_U_map[i]));
            CUDA_CHECK(cudaFree(state->d_diff_map[i]));
        }
        for (i = 0; i < state->nifo; i++) {
            peak_list_destroy(state->peak_list[i]);
            free(state->peak_list[i]);
        }
        sigmasq_destroy(state);
        autocorr_destroy(state);
        map_destroy(state);
    }
}

void state_reset_npeak(PeakList *pklist) {
    // printf("d_npeak %p\n", pklist->d_npeak);
    CUDA_CHECK(cudaMemset(pklist->d_npeak, 0, sizeof(int)));
    pklist->npeak[0] = 0;
}
