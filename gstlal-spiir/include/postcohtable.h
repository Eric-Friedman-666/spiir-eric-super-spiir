/*
 * Copyright (C) 2014 Qi Chu <qi.chu@ligo.org>
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Library General Public
 * License as published by the Free Software Foundation; either
 * version 2 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Library General Public License for more details.
 *
 * You should have received a copy of the GNU Library General Public
 * License along with this library; if not, write to the
 * Free Software Foundation, Inc., 59 Temple Place - Suite 330,
 * Boston, MA 02111-1307, USA.
 */

/*
# This should be the place we define our own table: postcoh_inspiral
# Compare it with the tables in lalmetaio/ src/ LIGOMetadataTables.h
*/

#ifndef __POSTCOH_TABLE_H__
#define __POSTCOH_TABLE_H__

#include <lal/Date.h> // for the LIGOTimeGPS
#include <lal/LALStdlib.h> // for the datatypes
#include <pipe_macro.h> // for MAX_IFO_LEN and macros

typedef struct tagPostcohInspiralTable {
    struct tagPostcohInspiralTable *next;
    long process_id;
    long event_id;
    LIGOTimeGPS end_time;
    LIGOTimeGPS end_time_sngl[MAX_NIFO];
    INT4 is_background;
    INT4 livetime;
    CHAR ifos[MAX_ALLIFO_LEN];
    CHAR pivotal_ifo[MAX_IFO_LEN];
    INT4 tmplt_idx;
    INT4 bankid;
    INT4 pix_idx;
    REAL4 snglsnr[MAX_NIFO];
    REAL4 coaphase[MAX_NIFO];
    REAL4 chisq[MAX_NIFO];
    REAL4 cohsnr;
    REAL4 nullsnr;
    REAL4 cmbchisq;
    REAL4 spearman_pval;
    REAL4 fap;
    REAL4 far_sngl[MAX_NIFO];
    REAL4 far_1w_sngl[MAX_NIFO];
    REAL4 far_1d_sngl[MAX_NIFO];
    REAL4 far_2h_sngl[MAX_NIFO];
    REAL4 far;
    REAL4 far_2h;
    REAL4 far_1d;
    REAL4 far_1w;
    CHAR skymap_fname[MAX_SKYMAP_FNAME_LEN]; // location of skymap
    REAL8 template_duration;
    REAL4 mass1;
    REAL4 mass2;
    REAL4 mchirp;
    REAL4 mtotal;
    REAL4 spin1x;
    REAL4 spin1y;
    REAL4 spin1z;
    REAL4 spin2x;
    REAL4 spin2y;
    REAL4 spin2z;
    REAL4 eta;
    REAL8 ra;
    REAL8 dec;
    REAL8 deff[MAX_NIFO];
    REAL8 rank;
    REAL4 f_final;
    LIGOTimeGPS epoch;
    double deltaT;
    size_t snr_length;
    float complex *snr;
} PostcohInspiralTable;
#endif /* __POSTCOH_TABLE_H */
