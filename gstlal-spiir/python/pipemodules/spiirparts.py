# Copyright (C) 2009--2013  Kipp Cannon, Chad Hanna, Drew Keppel
# Copyright (C) 2013, 2014  Qi Chu
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation; either version 2 of the License, or (at your
# option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General
# Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

#
#
# =============================================================================
#
#                                   Preamble
#
# =============================================================================
#

import logging
import os

### The following snippet is a modified version of examples in GstLAL: A software framework for gravitational wave discovery
import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

Gst.init(None)

from gstlal import datasource
from gstlal.pipeparts import condition
from gstlal import pipeparts
from gstlal_spiir import pipemodules
from gstlal import simulation
from gstlal_spiir.pipemodules import snglrate_datasource, spiir_utils

logger = logging.getLogger(__name__)


def _env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _crashcar_worker_bank_layout(banks):
    """Return the exact graph stream-to-bank mapping for one worker."""

    if not banks or len(banks) > 384:
        raise ValueError("crashcar requires a nonempty worker bank roster")
    stream_bank_ids = []
    for stream_id, bank_dict in enumerate(banks):
        if not bank_dict:
            raise ValueError(
                "crashcar stream %d has no detector bank mapping" % stream_id)
        detector_bank_ids = set()
        for instrument, bank_list in bank_dict.items():
            if len(bank_list) != 1:
                raise ValueError(
                    "crashcar stream %d detector %s must have exactly one bank"
                    % (stream_id, instrument))
            bank_id = int(
                spiir_utils.get_bankid_from_bankname(bank_list[0]))
            if bank_id < 0 or bank_id >= 384:
                raise ValueError(
                    "crashcar stream %d detector %s bank %d is out of scope"
                    % (stream_id, instrument, bank_id))
            detector_bank_ids.add(bank_id)
        if len(detector_bank_ids) != 1:
            raise ValueError(
                "crashcar stream %d detector banks disagree: %r"
                % (stream_id, sorted(detector_bank_ids)))
        stream_bank_ids.append(detector_bank_ids.pop())
    if len(set(stream_bank_ids)) != len(stream_bank_ids):
        raise ValueError(
            "crashcar worker bank roster contains duplicate bank ids")
    return tuple(stream_bank_ids)


def mkSPIIRmulti(
    pipeline,
    detectors,
    banks,
    psd,
    psd_fft_length=8,
    ht_gate_threshold=None,
    veto_segments=None,
    verbose=False,
    nxydump_segment=None,
    nxydump_directory=".",
    chisq_type="autochisq",
    track_psd=False,
    block_duration=Gst.SECOND,
    blind_injections=None,
    peak_thresh=4,
    gpu_acc=False,
):
    #
    # check for recognized value of chisq_type
    #

    if chisq_type not in ["autochisq" or "autochisq_spearman"]:
        raise ValueError(
            "chisq_type must be either 'autochisq' or 'autochisq_spearman', given %s"
            % chisq_type)

    #
    # extract segments from the injection file for selected reconstruction
    #

    if detectors.injection_filename is not None:
        inj_seg_list = simulation.sim_inspiral_to_segment_list(
            detectors.injection_filename)
    else:
        inj_seg_list = None
        #
        # Check to see if we are specifying blind injections now that we know
        # we don't want real injections. Setting this
        # detectors.injection_filename will ensure that injections are added
        # but won't only reconstruct injection segments.
        #
        detectors.injection_filename = blind_injections

    #
    # construct dictionaries of whitened, conditioned, down-sampled
    # h(t) streams
    #

    hoftdicts = {}
    for instrument in detectors.channel_dict:
        src, statevector, dqvector, _ = datasource.mkbasicsrc(pipeline,
                                                              detectors,
                                                              instrument,
                                                              verbose=verbose)
        rates = set(rate for bank in banks[instrument]
                    for rate in bank.get_rates()
                    )  # FIXME what happens if the rates are not the same?
        if veto_segments is not None and instrument in list(
                veto_segments.keys()):
            hoftdicts[instrument] = condition.mkcondition(
                pipeline,
                src,
                max(rates),
                instrument,
                psd=psd[instrument],
                psd_fft_length=psd_fft_length,
                ht_gate_threshold=ht_gate_threshold,
                veto_segments=veto_segments[instrument],
                seekevent=detectors.seekevent,
                nxydump_segment=nxydump_segment,
                track_psd=track_psd,
                zero_pad=0,
                width=32,
            )
        else:
            hoftdicts[instrument] = condition.mkcondition(
                pipeline,
                src,
                max(rates),
                instrument,
                psd=psd[instrument],
                psd_fft_length=psd_fft_length,
                ht_gate_threshold=ht_gate_threshold,
                veto_segments=None,
                seekevent=detectors.seekevent,
                nxydump_segment=nxydump_segment,
                track_psd=track_psd,
                zero_pad=0,
                width=32,
            )

    #
    # construct trigger generators
    #
    # format of banklist : {'H1': <H1Bank0>, <H1Bank1>..;
    # 			'L1': <L1Bank0>, <L1Bank1>..;..}
    # format of bank: <H1bank0>

    triggersrcs = dict((instrument, set()) for instrument in hoftdicts)

    for instrument, bank in [(instrument, bank)
                             for instrument, banklist in banks.items()
                             for bank in banklist]:
        suffix = "%s%s" % (instrument,
                           (bank.logname and "_%s" % bank.logname or ""))
        snr = mkSPIIRhoftToSnrSlices(
            pipeline,
            hoftdicts[instrument],
            bank,
            instrument,
            verbose=verbose,
            nxydump_segment=nxydump_segment,
            quality=1,
            gpu_acc=gpu_acc,
        )
        # snr = pipeparts.mkchecktimestamps(pipeline, snr, "timestamps_%s_snr" % suffix)
        # comment out this command to turn off the checktimestamps debugging

        snr = pipeparts.mktogglecomplex(pipeline, snr)
        snr = pipeparts.mktee(pipeline, snr)
        # FIXME you get a different trigger generator depending on the chisq calculation :/
        if chisq_type == "autochisq":
            # FIXME don't hardcode
            # peak finding window (n) in samples is one second at max rate, ie max(rates)
            # FIXME: bank.snr_thresh is removed, use peak_thresh instead
            head = pipeparts.mkitac(
                pipeline,
                snr,
                max(rates),
                bank.template_bank_filename,
                autocorrelation_matrix=bank.autocorrelation_bank,
                mask_matrix=bank.autocorrelation_mask,
                snr_thresh=peak_thresh,
                sigmasq=bank.sigmasq,
            )
            if verbose:
                head = pipeparts.mkprogressreport(pipeline, head,
                                                  "progress_xml_%s" % suffix)
            triggersrcs[instrument].add(head)
        elif chisq_type == "autochisq_spearman":
            # FIXME don't hardcode
            # peak finding window (n) in samples is one second at max rate, ie max(rates)
            # FIXME: bank.snr_thresh is removed, use peak_thresh instead
            head = mkitac_spearman(
                pipeline,
                snr,
                max(rates),
                bank.template_bank_filename,
                autocorrelation_matrix=bank.autocorrelation_bank,
                mask_matrix=bank.autocorrelation_mask,
                snr_thresh=peak_thresh,
                sigmasq=bank.sigmasq,
            )
            if verbose:
                head = pipeparts.mkprogressreport(pipeline, head,
                                                  "progress_xml_%s" % suffix)
            triggersrcs[instrument].add(head)

        # FIXME:  find a way to use less memory without this hack
        del bank.autocorrelation_bank
        if nxydump_segment is not None:
            pipeparts.mknxydumpsink(
                pipeline,
                pipeparts.mktogglecomplex(pipeline,
                                          pipeparts.mkqueue(pipeline, snr)),
                "%s/snr_gpu_%d_%s.dump" %
                (nxydump_directory, nxydump_segment[0], suffix),
                segment=nxydump_segment,
            )

    #
    # done
    #

    assert any(triggersrcs.values())
    return triggersrcs


def mkSPIIRhoftToSnrSlices(
    pipeline,
    src,
    bank,
    instrument,
    verbose=None,
    nxydump_segment=None,
    quality=4,
    sample_rates=None,
    max_rate=None,
    gpu_acc=False,
):
    if sample_rates is None:
        sample_rates = sorted(bank.get_rates())
    else:
        sample_rates = sorted(sample_rates)
    # FIXME don't upsample everything to a common rate
    if max_rate is None:
        max_rate = max(sample_rates)
    prehead = None

    for sr in sample_rates:
        head = pipeparts.mkqueue(
            pipeline,
            src[sr],
            max_size_time=Gst.SECOND * 10,
            max_size_buffers=0,
            max_size_bytes=0,
        )
        head = pipeparts.mkreblock(pipeline, head)
        if gpu_acc:
            head = pipemodules.mkcudaiirbank(
                pipeline,
                head,
                a1=bank.A[sr],
                b0=bank.B[sr],
                delay=bank.D[sr],
                name="gstlaliirbank_%d_%s_%s" % (sr, instrument, bank.logname),
            )
        else:
            head = pipeparts.mkiirbank(
                pipeline,
                head,
                a1=bank.A[sr],
                b0=bank.B[sr],
                delay=bank.D[sr],
                name="gstlaliirbank_%d_%s_%s" % (sr, instrument, bank.logname),
            )

        head = pipeparts.mkqueue(
            pipeline,
            head,
            max_size_time=Gst.SECOND * 10,
            max_size_buffers=0,
            max_size_bytes=0,
        )
        if prehead is not None:
            adder = Gst.ElementFactory.make("lal_adder", None)
            adder.set_property("sync", True)
            pipeline.add(adder)
            head.link(adder)
            prehead.link(adder)
            head = adder
        # 	head = pipeparts.mkadder(pipeline, (head, prehead))
        # FIXME:  this should get a nofakedisconts after it until the resampler is patched
        head = pipeparts.mkresample(pipeline, head, quality=1)
        if sr == max_rate:
            head = pipeparts.mkcapsfilter(pipeline, head,
                                          "audio/x-raw, rate=%d" % max_rate)
        else:
            head = pipeparts.mkcapsfilter(pipeline, head,
                                          "audio/x-raw, rate=%d" % (2 * sr))
        prehead = head

    return head


def mkPostcohSPIIR(
    pipeline,
    detectors,
    banks,
    psd,
    psd_fft_length=8,
    ht_gate_threshold=None,
    veto_segments=None,
    verbose=False,
    nxydump_segment=None,
    chisq_type="autochisq",
    track_psd=False,
    block_duration=Gst.SECOND,
    blind_injections=None,
    cuda_postcoh_snglsnr_thresh=4,
    cuda_postcoh_detrsp_fname=None,
    cuda_postcoh_hist_trials=1,
    output_prefix=None,
    cuda_postcoh_output_skymap=0,
):
    # 	pdb.set_trace()
    #
    # check for recognized value of chisq_type
    #

    if chisq_type not in ["autochisq"]:
        raise ValueError("chisq_type must be either 'autochisq', given %s" %
                         chisq_type)

    #
    # extract segments from the injection file for selected reconstruction
    #

    if detectors.injection_filename is not None:
        inj_seg_list = simulation.sim_inspiral_to_segment_list(
            detectors.injection_filename)
    else:
        inj_seg_list = None
        #
        # Check to see if we are specifying blind injections now that we know
        # we don't want real injections. Setting this
        # detectors.injection_filename will ensure that injections are added
        # but won't only reconstruct injection segments.
        #
        detectors.injection_filename = blind_injections

    #
    # construct dictionaries of whitened, conditioned, down-sampled
    # h(t) streams

    #
    #

    hoftdicts = {}
    max_instru_rates = {}
    sngl_max_rate = 0
    for instrument in detectors.channel_dict:
        for instrument_from_bank, bank_list in [
            (instrument_from_bank, bank_list) for bank_dict in banks
                for instrument_from_bank, bank_list in bank_dict.items()
        ]:
            if instrument_from_bank == instrument:
                sngl_max_rate = max(
                    spiir_utils.get_maxrate_from_xml(bank_list[0]),
                    sngl_max_rate)
        max_instru_rates[instrument] = sngl_max_rate
        src, statevector, dqvector, _ = datasource.mkbasicsrc(pipeline,
                                                              detectors,
                                                              instrument,
                                                              verbose=verbose)
        if veto_segments is not None:
            hoftdicts[instrument] = snglrate_datasource.mkwhitened_src(
                pipeline,
                src,
                sngl_max_rate,
                instrument,
                psd=psd[instrument],
                psd_fft_length=psd_fft_length,
                ht_gate_threshold=ht_gate_threshold,
                veto_segments=veto_segments[instrument],
                seekevent=detectors.seekevent,
                nxydump_segment=nxydump_segment,
                track_psd=track_psd,
                zero_pad=0,
                width=32,
            )
        else:
            hoftdicts[instrument] = snglrate_datasource.mkwhitened_src(
                pipeline,
                src,
                sngl_max_rate,
                instrument,
                psd=psd[instrument],
                psd_fft_length=psd_fft_length,
                ht_gate_threshold=ht_gate_threshold,
                veto_segments=None,
                seekevent=detectors.seekevent,
                nxydump_segment=nxydump_segment,
                track_psd=track_psd,
                zero_pad=0,
                width=32,
            )

    #
    # construct trigger generators
    #
    triggersrcs = []

    # format of banks :	[{'H1': <H1Bank0>; 'L1': <L1Bank0>..;}
    # 			 {'H1': <H1Bank1>; 'L1': <L1Bank1>..;}
    # 			 ...]
    # format of bank_dict: {'H1': <H1Bank1>; 'L1': <L1Bank1>..;}
    autocorrelation_fname_list = []
    for bank_dict in banks:
        autocorrelation_fname = ""
        for instrument, bank_list in bank_dict.items():
            autocorrelation_fname += str(instrument)
            autocorrelation_fname += ":"
            autocorrelation_fname += str(bank_list[0])
            autocorrelation_fname += ","
            if len(bank_list) != 1:
                raise ValueError(
                    "%s instrument: number of banks is not equal to 1, can not do coherent analysis"
                    % instrument)
        autocorrelation_fname = autocorrelation_fname.rstrip(",")
        autocorrelation_fname_list.append(autocorrelation_fname)

    for instrument in banks[0].keys():
        hoftdicts[instrument] = pipeparts.mktee(pipeline,
                                                hoftdicts[instrument])

    for i_dict, bank_dict in enumerate(banks):
        postcoh = None
        head = None

        for instrument, bank_list in bank_dict.items():
            bankname = bank_list[0]
            bankid = spiir_utils.get_bankid_from_bankname(bankname)
            max_bank_rate = spiir_utils.get_maxrate_from_xml(bankname)
            head = pipeparts.mkqueue(
                pipeline,
                hoftdicts[instrument],
                max_size_time=Gst.SECOND * 10,
                max_size_buffers=0,
                max_size_bytes=0,
            )
            if max_bank_rate < max_instru_rates[instrument]:
                head = pipeparts.mkcapsfilter(
                    pipeline,
                    pipeparts.mkresample(pipeline, head, quality=9),
                    "audio/x-raw, rate=%d" % max_bank_rate,
                )
            suffix = "%s_%d" % (instrument, bankid)

            head = pipeparts.mkreblock(pipeline, head)
            snr = pipeparts.mkcudamultiratespiir(
                pipeline, head, bankname, gap_handle=0,
                stream_id=bankid)  # treat gap as zeros
            if verbose:
                snr = pipeparts.mkprogressreport(
                    pipeline, snr, "progress_done_gpu_filtering_%s" % suffix)

            if postcoh is None:
                postcoh = pipemodules.mkcudapostcoh(
                    pipeline,
                    snr,
                    instrument,
                    cuda_postcoh_detrsp_fname,
                    autocorrelation_fname_list[i_dict],
                    bank_list[0],
                    hist_trials=cuda_postcoh_hist_trials,
                    snglsnr_thresh=cuda_postcoh_snglsnr_thresh,
                    output_skymap=cuda_postcoh_output_skymap,
                    stream_id=bankid,
                )
            else:
                snr.link_pads(None, postcoh, instrument)

        # FIXME: hard-coded to do compression
        if verbose:
            postcoh = pipeparts.mkprogressreport(
                pipeline, postcoh, "progress_xml_dump_bank_stream%d" % i_dict)
        head = mkpostcohfilesink(
            pipeline,
            postcoh,
            location=output_prefix,
            compression=1,
            snapshot_interval=0,
        )
        triggersrcs.append(head)
    return triggersrcs


def parse_shift_string(shift_string):
    """
	parses strings of form 

	det1:shift1, det2:shift2
	
	into a dictionary of lists of shifts.
	"""
    out = {}
    if shift_string is None:
        return out
    for det in shift_string.split(","):
        ifo, shift_val = det.split(":")
        if ifo in out:
            raise ValueError("Only one shift per instrument should be given")
        out[ifo] = shift_val
    return out


def mkPostcohSPIIROnline(pipeline,
                         detectors,
                         banks,
                         psd,
                         control_time_shift_string=None,
                         psd_fft_length=8,
                         fir_whitener=0,
                         ht_gate_threshold=None,
                         veto_segments=None,
                         verbose=False,
                         nxydump_segment=None,
                         nxydump_directory=".",
                         chisq_type="autochisq",
                         track_psd=False,
                         block_duration=Gst.SECOND,
                         blind_injections=None,
                         cuda_postcoh_snglsnr_thresh=4.0,
                         cuda_postcoh_cohsnr_thresh=5.0,
                         cuda_postcoh_detrsp_fname=None,
                         cuda_postcoh_hist_trials=1,
                         cuda_postcoh_output_skymap=0,
                         cuda_postcoh_detrsp_refresh_interval=0,
                         cohfar_file_path=None,
                         cohfar_accumbackground_output_prefix=None,
                         cohfar_accumbackground_output_name=None,
                         cohfar_accumbackground_snapshot_interval=0,
                         cohfar_assignfar_refresh_interval=86400,
                         cohfar_assignfar_silent_time=2147483647,
                         cohfar_assignfar_input_fname=None,
                         resample_quality=9,
                         resample_method=4,
                         resample_sinc_filter_mode=0,
                         resample_sinc_filter_interpolation=1,
                         feature_signal_removal_bg=False,
                         feature_signal_removal_bg_threshold=8.5):
    #
    # check for recognized value of chisq_type
    #

    if chisq_type not in ["autochisq"]:
        raise ValueError("chisq_type must be either 'autochisq', given %s" %
                         chisq_type)

    #
    # extract segments from the injection file for selected reconstruction
    #

    # NOTE: "injection_filename" is a legacy name. The new version is "injections"
    # As with channel_dict vs channel_name
    if detectors.injection_filename is not None:
        inj_seg_list = simulation.sim_inspiral_to_segment_list(
            detectors.injection_filename)
    else:
        inj_seg_list = None
        #
        # Check to see if we are specifying blind injections now that we know
        # we don't want real injections. Setting this
        # detectors.injection_filename will ensure that injections are added
        # but won't only reconstruct injection segments.
        #
        detectors.injection_filename = blind_injections

    #
    # construct dictionaries of whitened, conditioned, down-sampled
    # h(t) streams

    #
    #

    hoftdicts = {}
    max_instru_rates = {}
    sngl_max_rate = 0
    for instrument in detectors.channel_dict:
        for instrument_from_bank, bank_list in [
            (instrument_from_bank, bank_list) for bank_dict in banks
                for instrument_from_bank, bank_list in bank_dict.items()
        ]:
            if instrument_from_bank == instrument:
                sngl_max_rate = max(
                    spiir_utils.get_maxrate_from_xml(bank_list[0]),
                    sngl_max_rate)

        max_instru_rates[instrument] = sngl_max_rate
        src, statevector, dqvector, _ = datasource.mkbasicsrc(pipeline,
                                                              detectors,
                                                              instrument,
                                                              verbose=verbose)

        logger.debug(f"{instrument}: max rate of all banks {sngl_max_rate} Hz")

        if veto_segments is not None and instrument in list(
                veto_segments.keys()):
            hoftdicts[instrument] = snglrate_datasource.mkwhitened_src(
                pipeline,
                src,
                sngl_max_rate,
                instrument,
                psd=psd[instrument],
                psd_fft_length=psd_fft_length,
                ht_gate_threshold=ht_gate_threshold,
                veto_segments=veto_segments[instrument],
                nxydump_segment=nxydump_segment,
                nxydump_directory=nxydump_directory,
                track_psd=track_psd,
                zero_pad=0,
                width=32,
                fir_whitener=fir_whitener,
                statevector=statevector,
                dqvector=dqvector,
                resample_quality=resample_quality,
                resample_method=resample_method,
                resample_sinc_filter_mode=resample_sinc_filter_mode,
                resample_sinc_filter_interpolation=
                resample_sinc_filter_interpolation,
            )
        else:
            hoftdicts[instrument] = snglrate_datasource.mkwhitened_src(
                pipeline,
                src,
                sngl_max_rate,
                instrument,
                psd=psd[instrument],
                psd_fft_length=psd_fft_length,
                ht_gate_threshold=ht_gate_threshold,
                veto_segments=None,
                nxydump_segment=nxydump_segment,
                nxydump_directory=nxydump_directory,
                track_psd=track_psd,
                zero_pad=0,
                width=32,
                fir_whitener=fir_whitener,
                statevector=statevector,
                dqvector=dqvector,
                resample_quality=resample_quality,
                resample_method=resample_method,
                resample_sinc_filter_mode=resample_sinc_filter_mode,
                resample_sinc_filter_interpolation=
                resample_sinc_filter_interpolation,
            )

    #
    # construct trigger generators
    #
    triggersrcs = []
    ifos = ""
    for instrument in banks[0].keys():
        ifos += str(instrument)

    crashcar_enabled = _env_bool("CRASHCAR_ENABLE", False)
    crashcar_role = os.environ.get("CRASHCAR_ROLE", "")
    if crashcar_role not in ("", "A", "B"):
        raise ValueError("CRASHCAR_ROLE must be A or B")
    accumulate_multi_background = crashcar_role != "B"
    crashcar_worker_bank_ids = (
        _crashcar_worker_bank_layout(banks)
        if crashcar_enabled else ())
    crashcar_worker_bank_ids_csv = ",".join(
        str(bank_id) for bank_id in crashcar_worker_bank_ids)
    expected_worker_bank_ids = os.environ.get(
        "CRASHCAR_WORKER_BANK_IDS_EXPECTED", "")
    if (crashcar_enabled and
            expected_worker_bank_ids != crashcar_worker_bank_ids_csv):
        raise ValueError(
            "instantiated bank roster differs from authenticated worker binding")

    # format of banks :	[{'H1': <H1Bank0>; 'L1': <L1Bank0>..;}
    # 			 {'H1': <H1Bank1>; 'L1': <L1Bank1>..;}
    # 			 ...]
    # format of bank_dict: {'H1': <H1Bank1>; 'L1': <L1Bank1>..;}

    # assemble autocorrelation_fname for postcoh chisq calculation
    autocorrelation_fname_list = []
    for bank_dict in banks:
        autocorrelation_fname = ""
        for instrument, bank_list in bank_dict.items():
            autocorrelation_fname += str(instrument)
            autocorrelation_fname += ":"
            autocorrelation_fname += str(bank_list[0])
            autocorrelation_fname += ","
            if len(bank_list) != 1:
                raise ValueError(
                    "%s instrument: number of banks is not equal to 1, can not do coherent analysis"
                    % instrument)

        autocorrelation_fname = autocorrelation_fname.rstrip(",")
        autocorrelation_fname_list.append(autocorrelation_fname)

    shift_dict = parse_shift_string(control_time_shift_string)

    for instrument in banks[0].keys():
        hoftdicts[instrument] = pipeparts.mktee(pipeline,
                                                hoftdicts[instrument])

    for i_dict, bank_dict in enumerate(banks):
        postcoh = None
        head = None

        for instrument, bank_list in bank_dict.items():
            bankname = bank_list[0]
            bankid = spiir_utils.get_bankid_from_bankname(bankname)
            max_bank_rate = spiir_utils.get_maxrate_from_xml(bankname)
            head = pipeparts.mkqueue(
                pipeline,
                hoftdicts[instrument],
                max_size_time=Gst.SECOND * 10,
                max_size_buffers=0,
                max_size_bytes=0,
            )
            if max_bank_rate < max_instru_rates[instrument]:
                head = pipeparts.mkcapsfilter(
                    pipeline,
                    pipeparts.mkresample(pipeline, head, quality=9),
                    "audio/x-raw, rate=%d" % max_bank_rate,
                )
            suffix = "%s_%d" % (instrument, bankid)
            if instrument in list(shift_dict.keys()):
                #FIXME: This codepath is defunct and should be removed
                # head = mktimeshift(pipeline, head,
                #                    float(shift_dict[instrument]))
                if verbose:
                    head = pipeparts.mkprogressreport(
                        pipeline, head, "after_timeshift_%s" % suffix)

            head = pipeparts.mkreblock(pipeline, head)
            snr = pipemodules.mkcudamultiratespiir(
                pipeline, head, bank_list[0], gap_handle=0,
                stream_id=bankid)  # treat gap as zeros
            if verbose:
                snr = pipeparts.mkprogressreport(
                    pipeline, snr, "progress_done_gpu_filtering_%s" % suffix)

            snr = pipeparts.mktee(pipeline, snr)

            if nxydump_segment is not None:
                pipeparts.mknxydumpsink(
                    pipeline,
                    pipeparts.mkqueue(pipeline, snr),
                    "%s/snr_gpu_%d_%s.dump" %
                    (nxydump_directory, nxydump_segment[0], suffix),
                    segment=nxydump_segment,
                )

            snr = pipeparts.mkqueue(
                pipeline,
                snr,
                max_size_time=Gst.SECOND * 10,
                max_size_buffers=10,
                max_size_bytes=100000000,
            )

            if postcoh is None:
                # make a queue for postcoh, otherwise it will be in the same thread with the first bank
                postcoh = pipemodules.mkcudapostcoh(
                    pipeline,
                    snr,
                    instrument,
                    cuda_postcoh_detrsp_fname,
                    autocorrelation_fname_list[i_dict],
                    bank_list[0],
                    hist_trials=cuda_postcoh_hist_trials,
                    snglsnr_thresh=cuda_postcoh_snglsnr_thresh,
                    cohsnr_thresh=cuda_postcoh_cohsnr_thresh,
                    output_skymap=cuda_postcoh_output_skymap,
                    detrsp_refresh_interval=
                    cuda_postcoh_detrsp_refresh_interval,
                    feature_signal_removal_bg=feature_signal_removal_bg,
                    feature_signal_removal_bg_threshold=
                    feature_signal_removal_bg_threshold,
                    stream_id=bankid,
                )
            else:
                snr.link_pads(None, postcoh, instrument)

        # FIXME: hard-coded to do compression
        if verbose:
            postcoh = pipeparts.mkprogressreport(
                pipeline, postcoh, "progress_xml_dump_bank_stream%d" % i_dict)

        if accumulate_multi_background:
            if cohfar_accumbackground_output_prefix is None:
                postcoh = pipemodules.mkcohfar_accumbackground(
                    pipeline,
                    postcoh,
                    ifos=ifos,
                    hist_trials=cuda_postcoh_hist_trials,
                    output_prefix=None,
                    output_name=cohfar_accumbackground_output_name[i_dict],
                    snapshot_interval=cohfar_accumbackground_snapshot_interval,
                )
            else:
                postcoh = pipemodules.mkcohfar_accumbackground(
                    pipeline,
                    postcoh,
                    ifos=ifos,
                    hist_trials=cuda_postcoh_hist_trials,
                    output_prefix=cohfar_accumbackground_output_prefix[i_dict],
                    output_name=None,
                    snapshot_interval=cohfar_accumbackground_snapshot_interval,
                )
        postcoh = pipemodules.mkcohfar_assignfar(
            pipeline,
            postcoh,
            ifos=ifos,
            assignfar_refresh_interval=cohfar_assignfar_refresh_interval,
            silent_time=cohfar_assignfar_silent_time,
            input_fname=cohfar_assignfar_input_fname,
        )
        # head = mkpostcohfilesink(pipeline, postcoh, location = output_prefix[i_dict], compression = 1, snapshot_interval = snapshot_interval)
        triggersrcs.append(postcoh)
    return triggersrcs


def mkPostcohSPIIROffline(
    pipeline,
    detectors,
    banks,
    psd,
    control_time_shift_string=None,
    psd_fft_length=8,
    ht_gate_threshold=None,
    veto_segments=None,
    verbose=False,
    nxydump_segment=None,
    chisq_type="autochisq",
    track_psd=False,
    block_duration=Gst.SECOND,
    blind_injections=None,
    cuda_postcoh_snglsnr_thresh=4.0,
    cuda_postcoh_cohsnr_thresh=5.0,
    cuda_postcoh_detrsp_fname=None,
    cuda_postcoh_hist_trials=1,
    cuda_postcoh_output_skymap=0,
    cuda_postcohfilesink_output_prefix=None,
    cuda_postcohfilesink_snapshot_interval=14400,
):
    # 	pdb.set_trace()
    #
    # check for recognized value of chisq_type
    #

    if chisq_type not in ["autochisq"]:
        raise valueerror("chisq_type must be either 'autochisq', given %s" %
                         chisq_type)

    #
    # extract segments from the injection file for selected reconstruction
    #

    if detectors.injection_filename is not None:
        inj_seg_list = simulation.sim_inspiral_to_segment_list(
            detectors.injection_filename)
    else:
        inj_seg_list = None
        #
        # check to see if we are specifying blind injections now that we know
        # we don't want real injections. setting this
        # detectors.injection_filename will ensure that injections are added
        # but won't only reconstruct injection segments.
        #
        detectors.injection_filename = blind_injections

    #
    # construct dictionaries of whitened, conditioned, down-sampled
    # h(t) streams

    #
    #

    hoftdicts = {}
    max_instru_rates = {}
    sngl_max_rate = 0
    for instrument in detectors.channel_dict:
        for instrument_from_bank, bank_list in [
            (instrument_from_bank, bank_list) for bank_dict in banks
                for instrument_from_bank, bank_list in bank_dict.items()
        ]:
            if instrument_from_bank == instrument:
                sngl_max_rate = max(
                    spiir_utils.get_maxrate_from_xml(bank_list[0]),
                    sngl_max_rate)
        max_instru_rates[instrument] = sngl_max_rate
        src, statevector, dqvector, _ = datasource.mkbasicsrc(pipeline,
                                                              detectors,
                                                              instrument,
                                                              verbose=verbose)
        if veto_segments is not None:
            hoftdicts[instrument] = snglrate_datasource.mkwhitened_src(
                pipeline,
                src,
                sngl_max_rate,
                instrument,
                psd=psd[instrument],
                psd_fft_length=psd_fft_length,
                ht_gate_threshold=ht_gate_threshold,
                veto_segments=veto_segments[instrument],
                seekevent=detectors.seekevent,
                nxydump_segment=nxydump_segment,
                track_psd=track_psd,
                zero_pad=0,
                width=32,
                fir_whitener=0,
                statevector=statevector,
                dqvector=dqvector,
            )
        else:
            hoftdicts[instrument] = snglrate_datasource.mkwhitened_src(
                pipeline,
                src,
                sngl_max_rate,
                instrument,
                psd=psd[instrument],
                psd_fft_length=psd_fft_length,
                ht_gate_threshold=ht_gate_threshold,
                veto_segments=None,
                seekevent=detectors.seekevent,
                nxydump_segment=nxydump_segment,
                track_psd=track_psd,
                zero_pad=0,
                width=32,
                fir_whitener=0,
                statevector=statevector,
                dqvector=dqvector,
            )

    #
    # construct trigger generators
    #
    triggersrcs = []
    ifos = ""
    for instrument in banks[0].keys():
        ifos += str(instrument)

    # format of banks :	[{'h1': <h1bank0>; 'l1': <l1bank0>..;}
    # 			 {'h1': <h1bank1>; 'l1': <l1bank1>..;}
    # 			 ...]
    # format of bank_dict: {'h1': <h1bank1>; 'l1': <l1bank1>..;}

    autocorrelation_fname_list = []
    for bank_dict in banks:
        autocorrelation_fname = ""
        for instrument, bank_list in bank_dict.items():
            autocorrelation_fname += str(instrument)
            autocorrelation_fname += ":"
            autocorrelation_fname += str(bank_list[0])
            autocorrelation_fname += ","
            if len(bank_list) != 1:
                raise valueerror(
                    "%s instrument: number of banks is not equal to other banks, can not do coherent analysis"
                    % instrument)
        autocorrelation_fname = autocorrelation_fname.rstrip(",")
        autocorrelation_fname_list.append(autocorrelation_fname)

    for instrument in banks[0].keys():
        hoftdicts[instrument] = pipeparts.mktee(pipeline,
                                                hoftdicts[instrument])

    shift_dict = parse_shift_string(control_time_shift_string)

    for i_dict, bank_dict in enumerate(banks):
        postcoh = None
        head = None

        for instrument, bank_list in bank_dict.items():
            bankname = bank_list[0]
            bankid = spiir_utils.get_bankid_from_bankname(bankname)
            max_bank_rate = spiir_utils.get_maxrate_from_xml(bankname)
            head = pipeparts.mkqueue(
                pipeline,
                hoftdicts[instrument],
                max_size_time=Gst.SECOND * 10,
                max_size_buffers=0,
                max_size_bytes=0,
            )
            if max_bank_rate < max_instru_rates[instrument]:
                head = pipeparts.mkcapsfilter(
                    pipeline,
                    pipeparts.mkresample(pipeline, head, quality=9),
                    "audio/x-raw, rate=%d" % max_bank_rate,
                )
            suffix = "%s_%d" % (instrument, bankid)

            if instrument in list(shift_dict.keys()):
                head = mktimeshift(pipeline, head,
                                   float(shift_dict[instrument]))

            head = pipeparts.mkreblock(pipeline, head)
            snr = pipemodules.mkcudamultiratespiir(
                pipeline, head, bank_list[0], gap_handle=0,
                stream_id=bankid)  # treat gap as zeros
            if verbose:
                snr = pipeparts.mkprogressreport(
                    pipeline, snr, "progress_done_gpu_filtering_%s" % suffix)

            if postcoh is None:
                # make a queue for postcoh, otherwise it will be in the same thread with the first bank
                snr = pipeparts.mkqueue(
                    pipeline,
                    snr,
                    max_size_time=Gst.SECOND * 10,
                    max_size_buffers=0,
                    max_size_bytes=0,
                )
                postcoh = pipemodules.mkcudapostcoh(
                    pipeline,
                    snr,
                    instrument,
                    cuda_postcoh_detrsp_fname,
                    autocorrelation_fname_list[i_dict],
                    bank_list[0],
                    hist_trials=cuda_postcoh_hist_trials,
                    snglsnr_thresh=cuda_postcoh_snglsnr_thresh,
                    cohsnr_thresh=cuda_postcoh_cohsnr_thresh,
                    output_skymap=cuda_postcoh_output_skymap,
                    stream_id=bankid,
                )
            else:
                snr.link_pads(None, postcoh, instrument)

        # FIXME: hard-coded to do compression
        if verbose:
            postcoh = pipeparts.mkprogressreport(
                pipeline, postcoh, "progress_xml_dump_bank_stream%d" % i_dict)

        head = pipemodules.mkpostcohfilesink(
            pipeline,
            postcoh,
            location=cuda_postcohfilesink_output_prefix[i_dict],
            compression=1,
            snapshot_interval=cuda_postcohfilesink_snapshot_interval,
        )
        triggersrcs.append(head)
    return triggersrcs
