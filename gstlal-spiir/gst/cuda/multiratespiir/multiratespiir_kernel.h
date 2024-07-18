/*
 * Copyright (C) 2014 Qi Chu
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

#ifndef __CUDA_MULTIRATESPIIR_KERNEL_H__
#define __CUDA_MULTIRATESPIIR_KERNEL_H__

#include <cuda_runtime.h>
#include <multiratespiir/multiratespiir_state.h>

int multi_downsample(SpiirState **spstate,
                     const float *in_multidown,
                     int num_in_multidown,
                     uint num_depths,
                     cudaStream_t stream);

int spiirup(SpiirState **spstate,
            int num_in_multiup,
            uint num_depths,
            float *out,
            cudaStream_t stream);

#endif
