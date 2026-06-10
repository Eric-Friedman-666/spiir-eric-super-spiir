#
# Copyright (C) 2015 Qi Chu
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

from collections import deque
import csv
import threading
import sys
import StringIO
from shutil import copyfile
import httplib
import subprocess
import re
import time
import numpy as np
import os
import logging

import pygtk

pygtk.require("2.0")
import gobject

gobject.threads_init()
import pygst

pygst.require('0.10')
import gst

try:
    from ligo.gracedb.rest import GraceDb
except ImportError:
    print >> sys.stderr, "warning: gracedb import failed"
    GraceDb = None

from glue import iterutils
from glue import segments
from glue.ligolw import ligolw
from glue.ligolw import lsctables
from glue.ligolw import array as ligolw_array
from glue.ligolw import param as ligolw_param
from glue.ligolw import utils as ligolw_utils

from glue.ligolw.utils import process as ligolw_process
from glue.ligolw.utils import segments as ligolw_segments

import lal
from lal import LIGOTimeGPS

from gstlal import bottle
from gstlal.pipemodules.postcohtable import postcoh_table_def
from gstlal.pipemodules.postcohtable import postcohtable
from gstlal.pipemodules import pipe_macro

lsctables.LIGOTimeGPS = LIGOTimeGPS

#
# =============================================================================
#
#						 glue.ligolw Content Handlers
#
# =============================================================================
#


class LIGOLWContentHandler(ligolw.LIGOLWContentHandler):
    pass


ligolw_array.use_in(LIGOLWContentHandler)
ligolw_param.use_in(LIGOLWContentHandler)
lsctables.use_in(LIGOLWContentHandler)


#
class SegmentDocument(object):

    def __init__(self, ifos):

        self.filename = None
        #
        # build the XML document
        #

        self.xmldoc = ligolw.Document()
        self.xmldoc.appendChild(ligolw.LIGO_LW())

        self.process = ligolw_process.register_to_xmldoc(
            self.xmldoc, "gstlal_inspiral_postcohspiir_online", {})
        self.segtype = pipe_macro.ONLINE_SEG_TYPE_NAME
        self.seglistdict = {
            self.segtype:
            segments.segmentlistdict((instrument, segments.segmentlist())
                                     for instrument in re.findall('..', ifos))
        }

    def close(self):
        self.xmldoc.unlink()

    def write_output_file(self, verbose=False, cleanup=True):
        assert self.filename is not None
        with ligolw_segments.LigolwSegments(self.xmldoc,
                                            self.process) as llwsegments:
            for segtype, one_type_dict in self.seglistdict.items():
                llwsegments.insert_from_segmentlistdict(
                    one_type_dict,
                    name=segtype,
                    comment="SPIIR postcoh snapshot")
        ligolw_process.set_process_end_time(self.process)
        ligolw_utils.write_filename(self.xmldoc,
                                    self.filename,
                                    gz=(self.filename
                                        or "stdout").endswith(".gz"),
                                    verbose=verbose,
                                    trap_signals=None)
        if cleanup:
            self.close()


# A class for writing zerolag files and clearing their write threads
class PostcohDocumentWriter(object):

    def __init__(self):
        self.output_docs = []

    def __del__(self):
        self.await_writes()

    def _clear_completed_docs(self):
        self.output_docs = [
            doc for doc in self.output_docs if doc.has_unfinished_write()
        ]

    def write_output_file(self, postcoh_document, filepath, verbose=False):
        postcoh_document.write_output_file(filepath, verbose)
        self.output_docs.append(postcoh_document)
        # Cleanup completed docs with each new write.
        self._clear_completed_docs()

    def await_writes(self):
        for doc in self.output_docs:
            doc.await_write()
        self._clear_completed_docs()


# A class for zerolag files
class PostcohDocument(object):

    def __init__(self):
        self.thread = None
        # FIXME: process table, search summary table
        # FIXME: should be implemented as lsctables.PostcohInspiralTable
        self.xmldoc = ligolw.Document()
        self.xmldoc.appendChild(ligolw.LIGO_LW())
        self.xmldoc.childNodes[-1].appendChild(
            lsctables.New(postcoh_table_def.PostcohInspiralTable))
        self.table = postcoh_table_def.PostcohInspiralTable.get_table(
            self.xmldoc)

    def __del__(self):
        self.await_write()
        self.xmldoc.unlink()

    def _write(self, filename, verbose=False):
        assert filename is not None
        ligolw_utils.write_filename(self.xmldoc,
                                    filename,
                                    gz=(filename or "stdout").endswith(".gz"),
                                    verbose=verbose,
                                    trap_signals=None)

    def has_unfinished_write(self):
        return self.thread is not None and self.thread.isAlive()

    def await_write(self):
        if self.has_unfinished_write():
            self.thread.join()

    def write_output_file(self, filepath, verbose=False):
        if filepath is None or filepath.isspace():
            logging.error(
                "Cannot write output file as filepath '%s' is invalid." %
                filepath)
            return

        if self.thread is not None:
            logging.warning(
                "Ignoring call to write output file as the write is "\
                "already in progress or completed."
            )
            return

        logging.info("Snapshotting '%s'." % filepath)

        self.thread = threading.Thread(target=self._write,
                                       args=(filepath, verbose))
        self.thread.start()


#
class OnlinePerformer(object):

    def __init__(self, parent_lock, should_record_latencies):
        # setup bottle routes
        bottle.route("/latency_history.txt")(self.web_get_latency_history)

        self.latency_history = deque(maxlen=1000)
        self.parent_lock = parent_lock
        self.should_record_latencies = should_record_latencies

    def web_get_latency_history(self):
        with self.parent_lock:
            # first one in the list is sacrificed for a time stamp
            for (end_time, latency, cohsnr, cmbchisq,
                coinc_upload_latency, log_upload_latency) \
                    in self.latency_history:
                yield "%f %f %f %f %f %f\n" % (end_time, latency, cohsnr,
                                               cmbchisq, coinc_upload_latency,
                                               log_upload_latency)

    def update_eye_candy(self,
                         postcoh_inspiral,
                         coinc_upload_latency=0.0,
                         log_upload_latency=0.0):
        if self.should_record_latencies:
            current_gps_second = float(lal.UTCToGPS(time.gmtime()))
            current_ms_remainder = time.time() % 1
            latency_ms = current_gps_second + current_ms_remainder - postcoh_inspiral.end
            latency_val = (float(postcoh_inspiral.end), latency_ms,
                           postcoh_inspiral.cohsnr, postcoh_inspiral.cmbchisq,
                           coinc_upload_latency, log_upload_latency)
            self.latency_history.append(latency_val)


class FAPUpdater(object):

    def __init__(self,
                 path,
                 input_prefix_list,
                 ifos,
                 calcfap_interval,
                 combine_stats_interval,
                 output_list_string=None,
                 collect_walltime_string=None,
                 should_block=None,
                 stats_dump_dir_path=None,
                 verbose=None):
        self.path = path
        self.input_prefix_list = input_prefix_list
        self.ifos = ifos
        self.calcfap_interval = calcfap_interval
        self.combine_stats_interval = combine_stats_interval
        self.combine_stats_processes = []
        self.output = []
        if output_list_string is not None:
            self.output = output_list_string.split(",")
        self.files_being_combined = []

        self.last_calfap_time = None
        self.last_combine_stats_time = None
        self.collect_walltimes = []
        self.should_block = should_block
        self.calcfap_processes = []
        self.calcfap_timestamps = []
        if collect_walltime_string is not None:
            times = collect_walltime_string.split(",")
            for itime in times:
                self.collect_walltimes.append(int(itime))
                self.calcfap_processes.append(None)
                self.calcfap_timestamps.append(None)

        self.stats_dump_dir_path = stats_dump_dir_path
        if self.stats_dump_dir_path is not None and not os.path.exists(
                self.stats_dump_dir_path):
            os.mkdir(self.stats_dump_dir_path)

        self.combine_duration = 86400 * 2
        self.max_nstats_perbank = 3
        # FIXME: fixed number of banks per job
        self.max_nbank_perjob = 10
        # set the limit for maximum input string length
        # when the number of banks reaches 140,
        # it will give you a signal 7 error in OPA2
        # FIXME: hard-coded, the first entry in collect_walltimes is the longest
        self.max_nstats_for_marignalization = (
            self.collect_walltimes[0] / self.combine_duration +
            self.max_nstats_perbank + 1) * self.max_nbank_perjob

        if self.output and len(self.output) != len(self.collect_walltimes):
            raise ValueError(
                "number of input walltimes does match the number of " \
                    "input filenames: %s does not match %s" \
                    % (collect_walltime_string, output_list_string))

        self.verbose = verbose

    def _await_process(self, process):
        if process is not None and process.poll() is None:
            (stdoutdata, stderrdata) = process.communicate()
            if process.returncode != 0:
                print >> sys.stderr, "last process return code", \
                    process.returncode, stderrdata

    def _get_running_processes(self, processes):
        return [
            process for process in processes
            if process is not None and process.poll() is None
        ]

    def _is_combine_stats_running(self):
        return len(self._get_running_processes(
            self.combine_stats_processes)) > 0

    def _is_calcfap_running(self):
        return len(self._get_running_processes(self.calcfap_processes)) > 0

    def _dump_stats(self):
        if self.stats_dump_dir_path is None:
            return

        for i, timestamp in enumerate(self.calcfap_timestamps):
            if timestamp is None:
                continue

            timestamp_dir_path = os.path.join(self.stats_dump_dir_path,
                                              str(timestamp))

            if not os.path.exists(timestamp_dir_path):
                os.mkdir(timestamp_dir_path)

            src_filepath = self.output[i]
            filename = os.path.basename(src_filepath)
            dest_filepath = os.path.join(timestamp_dir_path, filename)
            copyfile(src_filepath, dest_filepath)

    def _clear_completed_processes(self):
        self.combine_stats_processes = self._get_running_processes(
            self.combine_stats_processes)

        # The calcfap processes list must maintain its size & order.
        for i, process in enumerate(self.calcfap_processes):
            if process is None or process.poll() is None:
                continue

            self.calcfap_processes[i] = None

    def await_processes(self):
        '''Awaits any ongoing subprocesses belonging to FAPUpdater.'''
        if self._is_calcfap_running():
            for process in self.calcfap_processes:
                self._await_process(process)

        if self._is_combine_stats_running():
            for process in self.combine_stats_processes:
                self._await_process(process)

        self._clear_completed_processes()

    def _try_cleanup_combined_files(self):
        """Removes files that have finished being combined.

        Returns True on success.
        
        Returns False if an error occurs or if they have 
        not finished combining."""
        if self._is_combine_stats_running():
            return False

        try:
            map(lambda x: os.remove(x), self.files_being_combined)
            self.files_being_combined = []
            return True
        except:
            print >> sys.stderr, "Failed to remove the following files: %s" \
                % ', '.join(self.files_being_combined)
            return False

    def _get_bankstats_filepaths(self):
        """Find the list of available files containing 'bank'.

        Returns None if files are currently being combined, or if an error occurs.
        Otherwise returns the list of files, which may be empty."""
        # Both calcfap and combine_stats require the latest combined stats files
        if not self._try_cleanup_combined_files():
            return None

        ls_fnames = sorted(os.listdir(str(self.path)))
        bankstats_keyword = 'bank'
        grep_fnames = [
            fname for fname in ls_fnames if bankstats_keyword in fname
        ]
        # remove file names that contain "next" which are temporary files
        valid_fnames = [
            one_fname for one_fname in grep_fnames
            if not re.search("next", one_fname)
        ]
        return valid_fnames

    def _filter_bankstats(self, ls_fnames, lower_bound_timestamp):
        valid_fnames = []
        for ifname in ls_fnames:
            ifname_split = ifname.split("_")
            # FIXME: This assumes the format of the stats filename
            #   e.g. bank16_stats_1187008882_1800.xml.gz
            if len(ifname_split) > 1 and ifname[
                    -4:] != "next" and ifname_split[-2].isdigit() and int(
                        ifname_split[-2]) > lower_bound_timestamp:
                valid_fnames.append("%s/%s" % (self.path, ifname))
        return valid_fnames

    def _is_combine_stats_required(self, filepaths):
        """Checks if `combine_stats` would be required to prevent
        `filepaths` causing a signal 7 error due to excessive string length."""
        if len(filepaths) > self.max_nstats_for_marignalization:
            logging.info("update fap: %d stats files for " \
                "marignalization, over the input string length limit." \
                % len(filepaths))
            return True
        return False

    def _call_calcfap(self,
                      output_filename,
                      input_filenames,
                      ifos,
                      update_pdf=True):
        if len(input_filenames) == 0:
            return None
        joined_input_filenames = ",".join(input_filenames)

        cmd = []
        cmd += ["gstlal_cohfar_calc_fap"]
        cmd += ["--input", joined_input_filenames]
        cmd += ["--input-format", "stats"]
        cmd += ["--output", output_filename]
        cmd += ["--ifos", ifos]
        if update_pdf:
            cmd += ["--update-pdf"]
        logging.info("%s" % cmd)
        proc = subprocess.Popen(cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        return proc

    def _try_run_calcfap(self, timestamp):
        '''
        Try to launch the 'calcfap' subprocess.

        Returns True on success,
        Returns False if a conflicting process is in progress,
        or if a an error occurs.
        '''
        if self._is_calcfap_running() or self._is_combine_stats_running():
            return False

        bankstats_filenames = []
        for (i, collect_walltime) in enumerate(self.collect_walltimes):
            filepaths = self._get_bankstats_filepaths()
            if filepaths == None:
                return False

            walltime_filepaths = self._filter_bankstats(
                filepaths, timestamp - collect_walltime)

            if self._is_combine_stats_required(walltime_filepaths):
                self._try_combine_stats()
                return False

            bankstats_filenames.append(walltime_filepaths)

        self._dump_stats()

        for (i, collect_walltime) in enumerate(self.collect_walltimes):
            # execute the cmd in a different process
            self.calcfap_processes[i] = self._call_calcfap(
                self.output[i], bankstats_filenames[i], self.ifos)
            if self.calcfap_processes[i] != None:
                self.calcfap_timestamps[i] = timestamp
            logging.info("update '%s' fap %d" % (collect_walltime, timestamp))
        return True

    def run_calcfap(self, timestamp):
        if self.calcfap_interval is None:
            return

        # Initialization
        if self.last_calfap_time is None:
            self.last_calfap_time = timestamp
        # Check interval
        duration = timestamp - self.last_calfap_time
        if (self.last_calfap_time
                is not None) and (duration >= self.calcfap_interval):
            if self._try_run_calcfap(timestamp):
                delay = timestamp - self.last_calfap_time \
                    - self.calcfap_interval
                if delay > 0:
                    logging.info(
                        "Calcfap launched at '%f' after '%f' second delay." %
                        (timestamp, delay))
                self.last_calfap_time = timestamp
                if self.should_block:
                    self.await_processes()

    # combine stats every day
    def _try_combine_stats(self):
        ls_fnames = self._get_bankstats_filepaths()
        if ls_fnames is None:
            return False
        if len(ls_fnames) == 0:
            logging.info("combine_stats with no files")
            return True
        logging.info("combine_stats")

        # FIXME: decode information assuming fixed stats name
        # e.g. bank16_stats_1187008882_1800.xml.gz
        # decode to {'16', ['bank16_stats_1187008882_1800.xml.gz', ..]}
        stats_dict = {}
        for ifname in ls_fnames:
            this_bankid = ifname.split('_')[0][4:]
            stats_dict.setdefault(this_bankid, []).append(ifname)

        if '' in stats_dict.keys():
            del stats_dict['']

        for bankid, bank_fnames in stats_dict.items():
            collected_fnames = []
            for one_bank_fname in bank_fnames:
                this_walltime = int(
                    one_bank_fname.split('.')[-3].split('_')[-1])
                collected_walltimes = list(
                    map(
                        lambda x: int(
                            os.path.split(x)[-1].split('_')[-1].split('.')[0]),
                        collected_fnames))
                total_collected_walltime = sum(collected_walltimes)
                if this_walltime >= self.combine_duration:
                    continue
                elif (len(collected_fnames) >= self.max_nstats_perbank
                      or total_collected_walltime >= self.combine_duration):
                    start_banktime = int(
                        os.path.split(collected_fnames[0])[-1].split('_')[-2])
                    fout = "%s/bank%s_stats_%d_%d.xml.gz" % (
                        self.path, bankid, start_banktime,
                        total_collected_walltime)

                    proc = self._call_calcfap(fout,
                                              collected_fnames,
                                              self.ifos,
                                              update_pdf=False)
                    self._clear_completed_processes()
                    self.combine_stats_processes.append(proc)
                    # mark to remove collected_fnames
                    for fname in collected_fnames:
                        self.files_being_combined.append(fname)
                    collected_fnames = []
                    collected_fnames.append("%s/%s" %
                                            (self.path, one_bank_fname))
                else:
                    collected_fnames.append("%s/%s" %
                                            (self.path, one_bank_fname))
        return True

    def run_combine_stats(self, timestamp):
        if self.combine_stats_interval is None:
            return

        # Initialization
        if self.last_combine_stats_time is None:
            self.last_combine_stats_time = timestamp

        # Check interval
        duration = timestamp - self.last_combine_stats_time
        if (self.last_combine_stats_time
                is not None) and (duration >= self.combine_stats_interval):
            if self._try_combine_stats():
                delay = timestamp - self.last_combine_stats_time \
                    - self.combine_stats_interval
                if delay > 0:
                    logging.info(
                        "Combine stats launched at '%f' after '%f' second delay."
                        % (timestamp, delay))
                self.last_combine_stats_time = timestamp


class FinalSink(object):

    def __init__(self,
                 channel_dict,
                 process_params,
                 pipeline,
                 need_online_perform,
                 path,
                 output_prefix,
                 output_name,
                 far_factor,
                 cluster_window=0.5,
                 snapshot_interval=None,
                 calcfap_interval=None,
                 cohfar_accumbackground_output_prefix=None,
                 fapupdater_output_fname=None,
                 fapupdater_collect_walltime_string=None,
                 singlefar_veto_thresh=0.01,
                 chisq_ratio_veto_thresh=8.0,
                 gracedb_far_threshold=None,
                 gracedb_group="Test",
                 gracedb_search="LowMass",
                 gracedb_pipeline="spiir",
                 gracedb_service_url="https://gracedb.ligo.org/api/",
                 gracedb_upload_attempts=3,
                 is_offline_analysis=False,
                 output_skymap=0,
                 opa_cohsnr_thresh=8,
                 negative_latency=0,
                 append_psd_to_coincs_doc=True,
                 expected_buffers_per_timestamp=None,
                 feature_best_far=False,
                 feature_best_far_threshold=0,
                 feature_cluster_available_triggers=False,
                 feature_dump_all_zerolags=False,
                 single_trigger_stream_fname=None,
                 feature_dump_stats=False,
                 feature_dump_stats_dir_path='.',
                 feature_subprocesses_can_block=False,
                 verbose=False):
        # feature flags
        self.enable_feature_best_far = feature_best_far
        self.best_far_threshold = feature_best_far_threshold
        self.feature_cluster_available_triggers = feature_cluster_available_triggers
        self.feature_dump_all_zerolags = feature_dump_all_zerolags
        self.single_trigger_stream_fname = single_trigger_stream_fname
        self.single_trigger_stream_seq = 0
        self.single_trigger_stream_fields = [
            "source_kind", "stream_seq", "stream_write_unix",
            "source_file", "source_row", "bank_group", "bankid",
            "event_id", "ifos", "ifo", "is_background", "end_time",
            "end_time_ns", "rho", "snglsnr", "chisq", "cohsnr",
            "cmbchisq", "far", "fap", "far_1d", "far_1w", "far_2h",
            "end_time_sngl_H1", "end_time_ns_sngl_H1",
            "end_time_sngl_L1", "end_time_ns_sngl_L1",
            "snglsnr_H1", "snglsnr_L1", "chisq_H1", "chisq_L1",
            "mass1", "mass2", "mchirp", "tmplt_idx"
        ]
        self.single_trigger_stream_real4_fields = set([
            "rho", "snglsnr", "chisq", "cohsnr", "cmbchisq", "far", "fap",
            "far_1d", "far_1w", "far_2h", "snglsnr_H1", "snglsnr_L1",
            "chisq_H1", "chisq_L1", "mass1", "mass2", "mchirp"
        ])

        #
        # initialize
        #
        self.lock = threading.Lock()
        self.pipeline = pipeline
        self.is_first_event = True
        self.channel_dict = channel_dict
        self.ifos = "".join(
            [ifo for ifo in pipe_macro.IFO_MAP if ifo in channel_dict])

        # Track number of current buffers so we can process early when possible
        self.current_timestamp = None
        self.num_current_buffers = 0
        self.expected_buffers_per_timestamp = expected_buffers_per_timestamp

        # cluster parameters
        self.cluster_window = cluster_window
        self.candidate = None
        self.cluster_boundary = None
        self.negative_latency = negative_latency
        self.cur_event_table = []
        self.chisq_ratio_thresh = chisq_ratio_veto_thresh
        # FIXME: hard-coded the opa_thresh that all triggers less than
        # this thresh will be tested
        # if their cohsnr SNRs are smaller than the given opa_cohsnr_snr
        # if smaller, no uploading.
        # opa_thresh is chosen as 1e-6 as to not launch lalinference jobs
        self.opa_thresh = 1e-6
        self.opa_cohsnr_thresh = opa_cohsnr_thresh

        self.singlefar_veto_thresh = singlefar_veto_thresh

        # gracedb parameters
        self.far_factor = far_factor
        self.gracedb_far_threshold = gracedb_far_threshold
        self.gracedb_group = gracedb_group
        self.gracedb_search = gracedb_search
        self.gracedb_pipeline = gracedb_pipeline
        self.gracedb_service_url = gracedb_service_url
        self.gracedb_upload_attempts = gracedb_upload_attempts
        self.is_offline_analysis = is_offline_analysis  # gracedb 'offline' arg
        self.gracedb_client = None
        if GraceDb:
            self.gracedb_client = GraceDb(gracedb_service_url,
                                          reload_certificate=True)
        self.threads_gracedb_upload = []

        # keep a record of segments and is snapshotted
        # our segments is determined by if incoming buf is GAP
        self.seg_document = SegmentDocument(self.ifos)

        # the postcoh doc stores clustered postcoh triggers and is snapshotted
        self.postcoh_document = PostcohDocument()
        self.zerolags_doc_writer = PostcohDocumentWriter()

        if self.feature_dump_all_zerolags:
            self.all_zerolags_document = PostcohDocument()

        # coinc doc to be uploaded to gracedb
        self.append_psd_to_coincs_doc = append_psd_to_coincs_doc
        # get values needed for skymap accompanying the trigger uploads
        for param in process_params:
            if param == 'cuda_postcoh_detrsp_fname':
                self.cuda_postcoh_detrsp_fname = process_params[param]
            if param == 'cuda_postcoh_output_skymap':
                self.cuda_postcoh_output_skymap = process_params[param]
        # snapshot parameters
        self.path = path
        self.process_params = process_params
        self.output_prefix = output_prefix
        self.output_name = output_name
        self.snapshot_interval = snapshot_interval
        self.thread_snapshot_segment = None
        self.t_snapshot_start = None
        self.last_buffer_timestamp = None
        # set logging to report status
        self.log_fname = "%s/finalsink_debug_log" % (path)
        logging.basicConfig(filename=self.log_fname,
                            format='%(asctime)s %(message)s',
                            level=logging.DEBUG)

        # background updater
        stats_dump_dir_path = feature_dump_stats_dir_path if feature_dump_stats else None
        self.fapupdater = FAPUpdater(
            path=path,
            input_prefix_list=cohfar_accumbackground_output_prefix,
            output_list_string=fapupdater_output_fname,
            collect_walltime_string=fapupdater_collect_walltime_string,
            ifos=self.ifos,
            calcfap_interval=calcfap_interval,
            combine_stats_interval=snapshot_interval,
            should_block=feature_subprocesses_can_block,
            stats_dump_dir_path=stats_dump_dir_path,
            verbose=verbose)

        # online information performer
        self.onperformer = OnlinePerformer(
            parent_lock=self.lock, should_record_latencies=need_online_perform)

        # trigger control
        self.trigger_control_doc = "trigger_control.txt"
        if not os.path.exists(self.trigger_control_doc):
            file(self.trigger_control_doc, 'w').close()
        self.last_submitted_trigger = (0, 1)

        # skymap
        self.output_skymap = output_skymap
        self.thread_upload_skymap = None

    def __is_significant_trigger(self, postcoh_inspiral):
        if not self.gracedb_far_threshold:
            return False

        if postcoh_inspiral.far <= 0.0:
            return False

        if postcoh_inspiral.nevent_1w <= 1000000:
            return False

        if ((postcoh_inspiral.far < self.opa_thresh)
                and (postcoh_inspiral.cohsnr < self.opa_cohsnr_thresh)):
            # print "suppressed", postcoh_inspiral.cohsnr, postcoh_inspiral.far
            return False

        # Single far veto for high-significance trigger
        # add a limits for chisq and check chisq ratio for upload.
        ifo_active = [
            chisq > 0.3 and chisq < 3 for chisq in postcoh_inspiral.chisq
        ]
        ifo_fars_ok = [
            far < self.singlefar_veto_thresh and far > 0.
            for far in postcoh_inspiral.far_sngl
        ]

        active_ifos_with_valid_far = [
            i for (i, v) in zip(ifo_fars_ok, ifo_active) if v
        ]
        active_ifo_pairs_within_chisq_ratio = (
            lambda x:
            [i1 / i2 < self.chisq_ratio_thresh for i1 in x for i2 in x])(
                [i for (i, v) in zip(postcoh_inspiral.chisq, ifo_active) if v])
        return postcoh_inspiral.far < self.gracedb_far_threshold and \
            sum(active_ifos_with_valid_far) >= 2 and \
            all(active_ifo_pairs_within_chisq_ratio)

    def _have_latest_buffers(self):
        return self.num_current_buffers == self.expected_buffers_per_timestamp

    # Keep track of the latest timestamp seen on any buffer and number
    # of buffers seen at that timestamp.
    def _update_current_timestamp(self, buffer):
        buf_timestamp = LIGOTimeGPS(0, buffer.timestamp)
        if self.current_timestamp is None \
                or buf_timestamp > self.current_timestamp:
            self.current_timestamp = buf_timestamp
            self.num_current_buffers = 0
        elif buf_timestamp < self.current_timestamp:
            logging.error(
                "Received buffer with old timestamp '%d' after receiving " \
                "receiving buffers with a later timestamp '%d'. " \
                "Attempting to recover by continuing with old timestamp." %
                buf_timestamp, self.current_timestamp)
            self.current_timestamp = buf_timestamp
            self.num_current_buffers = 0

        if buf_timestamp == self.current_timestamp:
            self.num_current_buffers += 1

    # TODO: Refactor/rewrite appsink_new_buffer() and cluster(), see #36
    def appsink_new_buffer(self, elem):
        with self.lock:
            buf = elem.emit("pull-buffer")

            # Parse buffer
            self._update_current_timestamp(buf)

            is_gap = buf.flag_is_set(gst.BUFFER_FLAG_GAP)

            newevents = []
            if not is_gap:
                newevents = postcohtable.from_buffer(buf)

            heartbeat = None
            if len(newevents) > 0:
                # FIXME: the first entry is used to add to the segments,
                #   but its not really an event
                heartbeat = newevents[0]
                newevents = newevents[1:]
            self.cluster_and_process_significant_triggers(
                buf.duration, newevents)

            if self._have_latest_buffers():
                self.fapupdater.run_calcfap(self.current_timestamp)
                self.fapupdater.run_combine_stats(self.current_timestamp)
                self.run_snapshot()

            if not is_gap and heartbeat is not None:
                self.add_segments(heartbeat, buf.duration)

            if is_gap:
                logging.info("buf gap at timestamp %d" %
                             self.current_timestamp)
            elif heartbeat is None:
                logging.warning(
                    "Not in gap and no heartbeat at timestamp %d." %
                    self.current_timestamp)

            # Delete finished threads from list
            self.threads_gracedb_upload = [
                t for t in self.threads_gracedb_upload if t.is_alive()
            ]

    # Records the candidate to gracedb, disk, trigger control, and
    # the online monitor depending on its significance.
    def record_candidate(self):
        postcoh_inspiral = self.candidate.postcoh_inspiral
        is_trigger_submitted = False
        if self.__is_significant_trigger(postcoh_inspiral):
            if not self.__is_trigger_control_required(postcoh_inspiral):
                coincs_document = self.__create_coinc_document(self.candidate)
                filename = "%s_%s_%d_%d.xml" % (
                    postcoh_inspiral.ifos, postcoh_inspiral.end_time,
                    postcoh_inspiral.bankid, postcoh_inspiral.tmplt_idx)
                self.__do_gracedb_alert(postcoh_inspiral, coincs_document,
                                        filename, self.gracedb_upload_attempts)
                self.__write_coinc_to_disk(coincs_document, filename)
                # close the xmldoc for garbage collection
                coincs_document.close()
                is_trigger_submitted = True
            self.__append_to_trigger_control(
                postcoh_inspiral, is_trigger_submitted=is_trigger_submitted)
        if not is_trigger_submitted or self.gracedb_client is None:
            self.onperformer.update_eye_candy(postcoh_inspiral)

    # This is named verbosely pending a refactor
    # It should return a list of significant triggers to be processed
    # in another function
    def cluster_and_process_significant_triggers(self, duration, newevents):
        # If we have as many buffers as
        # we are expecting we can process them now rather than waiting for
        # the next buffer.
        if self.feature_dump_all_zerolags:
            self.all_zerolags_document.table.extend(
                [event.postcoh_inspiral for event in newevents])

        max_cluster_boundary = self.current_timestamp
        if self._have_latest_buffers():
            max_cluster_boundary = self.current_timestamp + LIGOTimeGPS(
                0, duration)
            self.cur_event_table.extend(newevents)

        # The max (upper) bound of any cluster we are willing to process.
        # Assume we have all buffers from before the start of this buffer
        # Event end times are offset by negative latency if we are
        # running early warning
        max_cluster_boundary = max_cluster_boundary + self.negative_latency
        if self.is_first_event and len(newevents) > 0:
            self.cluster_boundary = (max_cluster_boundary +
                                     self.cluster_window)
            self.is_first_event = False

        # NOTE: only consider clustered trigger for uploading to gracedb
        # check if the newevents is over boundary
        # this loop will exit when the cluster_boundary is incremented
        # to be > the max_cluster_boundary

        while ((self.cluster_window > 0) and (self.cluster_boundary)
               and (max_cluster_boundary >= self.cluster_boundary)):
            if self.try_get_cluster_candidate():
                self.__set_far(self.candidate.postcoh_inspiral)
                self.postcoh_document.table.append(
                    self.candidate.postcoh_inspiral)
                self._append_single_trigger_stream_rows(
                    [self.candidate.postcoh_inspiral])
                self.record_candidate()
                self.candidate = None
            if self.feature_cluster_available_triggers:
                iterutils.inplace_filter(
                    lambda row: row.postcoh_inspiral.end > self.
                    cluster_boundary, self.cur_event_table)
                self.cluster_boundary = self.cluster_boundary + self.cluster_window

        # extend newevents to cur_event_table
        # Has to be done after processing pre-existing events, because this
        # buffer may contain events with end time before the buffer start
        # Will have been done above if we could process this buffer early,
        # so don't double up events if so.
        if not self._have_latest_buffers():
            self.cur_event_table.extend(newevents)

        if self.cluster_window == 0:
            output_rows = [event.postcoh_inspiral for event in newevents]
            self.postcoh_document.table.extend(
                output_rows)
            self._append_single_trigger_stream_rows(output_rows)
            del self.cur_event_table[:]

    def add_segments(self, heartbeat, duration):
        participating_ifos = re.findall('..', heartbeat.postcoh_inspiral.ifos)
        buf_seg = segments.segment(
            self.current_timestamp,
            self.current_timestamp + LIGOTimeGPS(0, duration))
        for segtype, one_type_dict in self.seg_document.seglistdict.items():
            for ifo in one_type_dict.keys():
                if ifo in participating_ifos:
                    this_seglist = one_type_dict[ifo]
                    this_seglist = this_seglist + segments.segmentlist(
                        [buf_seg])
                    this_seglist.coalesce()
                    one_type_dict[ifo] = this_seglist
        self._append_single_trigger_stream_boundaries(
            participating_ifos, self.current_timestamp + LIGOTimeGPS(0, duration))

    def _single_trigger_stream_enabled(self):
        return bool(self.single_trigger_stream_fname)

    def _stringify_single_trigger_value(self, field, value):
        if value is None:
            return ""
        if field in self.single_trigger_stream_real4_fields:
            try:
                value = np.float32(value)
            except (TypeError, ValueError):
                return str(value)
            if np.isfinite(value):
                return "{0:.8g}".format(value)
        try:
            if isinstance(value, np.generic):
                value = value.item()
        except Exception:
            pass
        return str(value)

    def _single_trigger_row_attr(self, row, name):
        try:
            return getattr(row, name)
        except Exception:
            return ""

    def _gps_parts_for_stream(self, gps):
        if gps is None:
            return "", ""
        seconds = getattr(gps, "gpsSeconds", None)
        nanoseconds = getattr(gps, "gpsNanoSeconds", None)
        if seconds is not None:
            return seconds, nanoseconds or 0
        try:
            return int(gps), 0
        except Exception:
            return "", ""

    def _ensure_single_trigger_stream_parent(self):
        dirname = os.path.dirname(self.single_trigger_stream_fname)
        if dirname and not os.path.isdir(dirname):
            os.makedirs(dirname)

    def _write_single_trigger_stream_dicts(self, rows):
        if not self._single_trigger_stream_enabled() or not rows:
            return
        self._ensure_single_trigger_stream_parent()
        write_header = (
            (not os.path.exists(self.single_trigger_stream_fname))
            or os.path.getsize(self.single_trigger_stream_fname) == 0)
        with open(self.single_trigger_stream_fname, "ab") as output_file:
            writer = csv.DictWriter(
                output_file, fieldnames=self.single_trigger_stream_fields)
            if write_header:
                writer.writeheader()
            for row in rows:
                writer.writerow(row)
            output_file.flush()

    def _single_trigger_stream_row(self, postcoh_inspiral):
        self.single_trigger_stream_seq += 1
        row = dict((field, "") for field in self.single_trigger_stream_fields)
        row.update({
            "source_kind": "trigger",
            "stream_seq": self.single_trigger_stream_seq,
            "stream_write_unix": "%.6f" % time.time(),
            "bankid": self._single_trigger_row_attr(postcoh_inspiral, "bankid"),
            "event_id": self._single_trigger_row_attr(postcoh_inspiral, "event_id"),
            "ifos": self._single_trigger_row_attr(postcoh_inspiral, "ifos"),
            "is_background": self._single_trigger_row_attr(
                postcoh_inspiral, "is_background"),
            "end_time": self._single_trigger_row_attr(postcoh_inspiral, "end_time"),
            "end_time_ns": self._single_trigger_row_attr(
                postcoh_inspiral, "end_time_ns"),
            "cohsnr": self._single_trigger_row_attr(postcoh_inspiral, "cohsnr"),
            "cmbchisq": self._single_trigger_row_attr(postcoh_inspiral, "cmbchisq"),
            "far": self._single_trigger_row_attr(postcoh_inspiral, "far"),
            "fap": self._single_trigger_row_attr(postcoh_inspiral, "fap"),
            "far_1d": self._single_trigger_row_attr(postcoh_inspiral, "far_1d"),
            "far_1w": self._single_trigger_row_attr(postcoh_inspiral, "far_1w"),
            "far_2h": self._single_trigger_row_attr(postcoh_inspiral, "far_2h"),
            "mass1": self._single_trigger_row_attr(postcoh_inspiral, "mass1"),
            "mass2": self._single_trigger_row_attr(postcoh_inspiral, "mass2"),
            "mchirp": self._single_trigger_row_attr(postcoh_inspiral, "mchirp"),
            "tmplt_idx": self._single_trigger_row_attr(postcoh_inspiral, "tmplt_idx"),
        })
        for ifo in ("H1", "L1"):
            for base in ("end_time_sngl", "end_time_ns_sngl", "snglsnr", "chisq"):
                column = "%s_%s" % (base, ifo)
                row[column] = self._single_trigger_row_attr(postcoh_inspiral, column)
        return dict((key, self._stringify_single_trigger_value(key, value))
                    for key, value in row.items())

    def _append_single_trigger_stream_rows(self, postcoh_rows):
        if not self._single_trigger_stream_enabled():
            return
        self._write_single_trigger_stream_dicts([
            self._single_trigger_stream_row(row)
            for row in postcoh_rows
        ])

    def _append_single_trigger_stream_boundaries(self, ifos, boundary_gps):
        if not self._single_trigger_stream_enabled():
            return
        end_time, end_time_ns = self._gps_parts_for_stream(boundary_gps)
        rows = []
        for ifo in ifos:
            self.single_trigger_stream_seq += 1
            row = dict((field, "") for field in self.single_trigger_stream_fields)
            row.update({
                "source_kind": "chunk_boundary",
                "stream_seq": self.single_trigger_stream_seq,
                "stream_write_unix": "%.6f" % time.time(),
                "ifos": ifo,
                "ifo": ifo,
                "is_background": "empty",
                "end_time": end_time,
                "end_time_ns": end_time_ns,
            })
            row["end_time_sngl_%s" % ifo] = end_time
            row["end_time_ns_sngl_%s" % ifo] = end_time_ns
            rows.append(dict((key, self._stringify_single_trigger_value(key, value))
                             for key, value in row.items()))
        self._write_single_trigger_stream_dicts(rows)

    def run_snapshot(self):
        # Initialization
        if self.t_snapshot_start is None:
            self.t_snapshot_start = self.current_timestamp
        # Check interval
        duration = self.current_timestamp - self.t_snapshot_start
        if ((self.snapshot_interval is not None)
                and (duration >= self.snapshot_interval)):
            self.snapshot_segment_file(self.t_snapshot_start, duration)
            zerolag_snapshot_filestem = self.get_zerolags_filestem(
                self.output_prefix, self.output_name, self.t_snapshot_start,
                duration)
            self._snapshot_zerolags(zerolag_snapshot_filestem)
            self.t_snapshot_start = self.current_timestamp

        # Record the last timestamp so remaining stats can be dumped on program end.
        self.last_buffer_timestamp = self.current_timestamp

    def try_get_cluster_candidate(self):
        # Select the best trigger with end time up to the cluster boundary

        # Compare cohsnr for statistical significance, with tie-breaks. See #45
        def is_better_event(lhs, rhs):
            if lhs.cohsnr != rhs.cohsnr:
                return lhs.cohsnr > rhs.cohsnr
            if lhs.end != rhs.end:
                return lhs.end < rhs.end
            # Each bank has a set of templates, forming a unique 'composite key'
            if lhs.bankid != rhs.bankid:
                return lhs.bankid < rhs.bankid
            return lhs.tmplt_idx < rhs.tmplt_idx

        peak_event = None
        # find the max cohsnr event within the boundary of cur_event_table
        # FIXME: SPEEDUP
        for row in filter(
                lambda row: row.postcoh_inspiral.end <= self.cluster_boundary,
                self.cur_event_table):
            if peak_event is None or is_better_event(
                    row.postcoh_inspiral, peak_event.postcoh_inspiral):
                peak_event = row

        if self.feature_cluster_available_triggers:
            self.candidate = peak_event
            return self.candidate is not None
        else:
            # cur_table is empty and we do have a candidate,
            # so need to check the candidate
            if peak_event is None:
                # no event within boundary, candidate is the peak, update boundary
                self.cluster_boundary = self.cluster_boundary + self.cluster_window
                return self.candidate is not None

            if self.candidate is None or is_better_event(
                    peak_event.postcoh_inspiral,
                    self.candidate.postcoh_inspiral):
                # slide window so the centre becomes the peak_event
                self.candidate = peak_event
                iterutils.inplace_filter(
                    lambda row: row.postcoh_inspiral.end > self.
                    cluster_boundary, self.cur_event_table)
                # update boundary
                # NOTE: cluster boundary does not necessarily align with
                #   buffer boundary
                self.cluster_boundary = self.candidate.postcoh_inspiral.end + self.cluster_window
                return False
            else:
                # FIXME: This seems to assume buffer length >= cluster_window
                # pop out candidate for gracedb uploading
                iterutils.inplace_filter(
                    lambda row: row.postcoh_inspiral.end > self.
                    cluster_boundary, self.cur_event_table)
                # update boundary
                self.cluster_boundary = self.cluster_boundary + self.cluster_window
                return True

    def __filter_zero_fars(self, fars):
        filtered_fars = []
        for far in fars:
            if far > 0:
                filtered_fars.append(far)

        if len(filtered_fars) == 0:
            filtered_fars.append(0)

        return filtered_fars

    # Return all non-zero fars where the nevents meet the required threshold
    # If all are zero, return [0]
    def __get_valid_fars(self, fars, nevents):
        gated_fars = [
            fars[i] if nevents[i] > self.best_far_threshold else 0
            for i in range(len(fars))
        ]
        return self.__filter_zero_fars(gated_fars)

    def __get_valid_combined_fars(self, postcoh_inspiral):
        combined_fars = [
            postcoh_inspiral.far_1w, postcoh_inspiral.far_1d,
            postcoh_inspiral.far_2h
        ]
        combined_nevents = [
            postcoh_inspiral.nevent_1w, postcoh_inspiral.nevent_1d,
            postcoh_inspiral.nevent_2h
        ]

        return self.__get_valid_fars(combined_fars, combined_nevents)

    def __get_valid_single_fars(self, postcoh_inspiral):
        ifo_fars = zip(postcoh_inspiral.far_1w_sngl,
                       postcoh_inspiral.far_1d_sngl,
                       postcoh_inspiral.far_2h_sngl)
        ifo_nevents = zip(postcoh_inspiral.nevent_1w_sngl,
                          postcoh_inspiral.nevent_1d_sngl,
                          postcoh_inspiral.nevent_2h_sngl)
        return [
            self.__get_valid_fars(ifo_fars[i], ifo_nevents[i])
            for i in range(len(ifo_fars))
        ]

    def __set_far(self, postcoh_inspiral):
        if self.enable_feature_best_far:
            valid_combined_fars = self.__get_valid_combined_fars(
                postcoh_inspiral)
            valid_single_fars = self.__get_valid_single_fars(postcoh_inspiral)

            postcoh_inspiral.far = min(valid_combined_fars) * self.far_factor
            far_sngl = [
                min(fars) * self.far_factor for fars in valid_single_fars
            ]
        else:
            postcoh_inspiral.far = (max(
                postcoh_inspiral.far_2h, postcoh_inspiral.far_1d,
                postcoh_inspiral.far_1w)) * self.far_factor
            far_sngl = [
                (max(fars) * self.far_factor)
                for fars in zip(postcoh_inspiral.far_2h_sngl, postcoh_inspiral.
                                far_1d_sngl, postcoh_inspiral.far_1w_sngl)
            ]
        for ifo_id, ifo in enumerate(pipe_macro.IFO_MAP):
            postcoh_inspiral.far_sngl[ifo_id] = far_sngl[ifo_id]

    def __is_trigger_control_required(self, postcoh_inspiral):
        # do trigger control
        # FIXME: implement a sql solution for node communication ?

        with open(self.trigger_control_doc, "r") as f:
            content = f.read().splitlines()

        if len(content) > 0:
            # index in content array; start at end (-1) and work backwards
            trigger_index = -1
            (last_time, last_far,
             is_submitted) = content[trigger_index].split(",")
            # TODO: Consider comparing against `last_submitted_trigger`
            while is_submitted == "0" and len(content) + trigger_index > 0:
                trigger_index = trigger_index - 1
                (last_time, last_far,
                 is_submitted) = content[trigger_index].split(",")
            last_time = float(last_time)
            last_far = float(last_far)
        else:
            last_time = self.last_submitted_trigger[0]
            last_far = self.last_submitted_trigger[1]

        self.last_submitted_trigger = (last_time, last_far)
        # suppress the trigger
        # if it is not one order of magnitude more significant than the last
        # recent trigger
        is_recent = abs(float(postcoh_inspiral.end) - last_time) < 5
        is_significant_improvement = postcoh_inspiral.far <= last_far

        return is_recent and not is_significant_improvement

    def __append_to_trigger_control(self, postcoh_inspiral,
                                    is_trigger_submitted):
        trigger_control_log = "time %f, FAR %f, " \
                    "last_submitted time %f, last_submitted_far %f" \
                    % (float(postcoh_inspiral.end), postcoh_inspiral.far,
                    self.last_submitted_trigger[0], self.last_submitted_trigger[1])
        if is_trigger_submitted:
            self.last_submitted_trigger = (float(postcoh_inspiral.end),
                                           postcoh_inspiral.far)
            print >> sys.stderr, "trigger passed, %s" \
                    % trigger_control_log
        else:
            print >> sys.stderr, "trigger controlled, %s" \
                    % trigger_control_log

        line = "%f,%e,%d\n" % (float(
            postcoh_inspiral.end), postcoh_inspiral.far, is_trigger_submitted)
        with open(self.trigger_control_doc, "a") as f:
            f.write(line)

    def get_current_lal_psd_frequency_series(self, ifo):
        """Retrieves the mean-psd element from the pipeline for a
        given ifo and constructs a lal.REAL8FrequencySeries
        python object to store the array data.
        
        Parameters
        ---------
        ifo: str
            The interferometer string name for the mean-psd array
            to be retrieved from the pipeline.

        Returns
        -------
        lal.REAL8FrequencySeries
        """
        lal_whiten_element = self.pipeline.get_by_name("lal_whiten_%s" % ifo)
        current_lal_psd = np.array(lal_whiten_element.get_property("mean-psd"))
        psd_frequency_series = lal.CreateREAL8FrequencySeries(
            name="psd",
            epoch=LIGOTimeGPS(lal.UTCToGPS(time.gmtime()), 0),
            f0=0.0,
            deltaF=lal_whiten_element.get_property("delta-f"),
            sampleUnits=lal.Unit("s strain^2"),
            length=len(current_lal_psd))
        psd_frequency_series.data.data = current_lal_psd

        return psd_frequency_series

    def upload_to_gracedb(self, postcoh_inspiral, gracedb_upload_attempts,
                          filename, coinc_message, log_message):
        upload_attempt_limit = gracedb_upload_attempts + 1  # one-indexed
        gracedb_id = None
        t_start_coinc_upload = time.time()

        for i in range(1, upload_attempt_limit):
            try:
                #FIXME: Hardcoded an EARLY_WARNING label for EW runs.
                #FIXME: In the future, when necessary, use a dedicated label argument. See #120.
                label_name = None
                if self.negative_latency != 0:
                    label_name = "EARLY_WARNING"
                resp = self.gracedb_client.createEvent(
                    self.gracedb_group,
                    self.gracedb_pipeline,
                    filename,
                    filecontents=coinc_message.getvalue(),
                    search=self.gracedb_search,
                    offline=self.is_offline_analysis,
                    labels=label_name)
                resp_json = resp.json()
                if resp.status != httplib.CREATED:
                    gracedb_msg = "[%d/%d] graceid upload '%s' failed" % \
                        (i, upload_attempt_limit, filename)
                    print >> sys.stderr, gracedb_msg
                else:
                    gracedb_id = resp_json["graceid"]
                    gracedb_msg = "[%d/%d] graceid upload '%s' succeeded with id '%s'" % \
                        (i, upload_attempt_limit, filename, gracedb_id)
                    print >> sys.stderr, gracedb_msg
                    break
            except Exception as e:
                print >> sys.stderr, e

        t_end_coinc_upload = time.time()

        coinc_message.close()

        if gracedb_id is not None:

            for i in range(1, upload_attempt_limit):
                try:
                    resp = self.gracedb_client.writeLog(
                        gracedb_id,
                        log_message,
                        filename=None,
                        tagname="analyst_comments")
                    if resp.status != httplib.CREATED:
                        gracedb_msg = "[%d/%d] gracedb upload of log failed" % \
                            (i, upload_attempt_limit)
                        print >> sys.stderr, gracedb_msg
                    else:
                        gracedb_msg = "[%d/%d] gracedb upload of log succeeded" % \
                            (i, upload_attempt_limit)
                        print >> sys.stderr, gracedb_msg
                        break
                except Exception as e:
                    print >> sys.stderr, e
        else:
            print >> sys.stderr, "gracedb upload of '%s' failed completely" % filename

        t_end_log_upload = time.time()
        coinc_upload_latency = t_end_coinc_upload - t_start_coinc_upload
        log_upload_latency = t_end_log_upload - t_end_coinc_upload
        self.onperformer.update_eye_candy(postcoh_inspiral,
                                          coinc_upload_latency,
                                          log_upload_latency)

    def __create_coinc_document(self, trigger):
        coincs_document = CoincsDocFromPostcoh(self.path, self.process_params,
                                               self.channel_dict)
        # TODO: Remove conditional bool here and in __init__ after tests
        if self.append_psd_to_coincs_doc:
            psds = {
                ifo: self.get_current_lal_psd_frequency_series(ifo)
                for ifo in re.findall("..", trigger.postcoh_inspiral.ifos)
            }
        else:
            psds = None

        coincs_document.assemble_ligolw_xmldoc(trigger, psds)
        return coincs_document

    def __write_coinc_to_disk(self, coincs_document, filename):
        print >> sys.stderr, "writing %s to disk ..." % filename
        ligolw_utils.write_filename(coincs_document.xmldoc,
                                    filename,
                                    gz=False,
                                    trap_signals=None)

    def __do_gracedb_alert(self,
                           postcoh_inspiral,
                           coincs_document,
                           filename,
                           gracedb_upload_attempts=3):
        if self.gracedb_client is not None:
            # Construct message and send to gracedb.
            # We go through the intermediate step of first writing the xmldoc
            # into a string buffer incase there is some safety in doing so in
            # the event of a malformed document; instead of writing directly
            # into gracedb's  input pipe and crashing part way through.
            coinc_message = StringIO.StringIO()
            ligolw_utils.write_fileobj(coincs_document.xmldoc,
                                       coinc_message,
                                       gz=False)

            print >> sys.stderr, "sending %s to gracedb ..." % filename

            log_message = "Optimal ra and dec from this coherent pipeline: \
                (%f, %f) in degrees" \
                    % (postcoh_inspiral.ra, postcoh_inspiral.dec)

            gracedb_upload_thread = threading.Thread(
                target=self.upload_to_gracedb,
                args=(postcoh_inspiral, gracedb_upload_attempts, filename,
                      coinc_message, log_message))
            gracedb_upload_thread.start()
            self.threads_gracedb_upload.append(gracedb_upload_thread)

    def get_zerolags_filestem(self, output_prefix, output_name,
                              t_snapshot_start, snapshot_duration):
        if output_prefix is not None:
            fname = "%s_%d_%d" % (output_prefix, t_snapshot_start,
                                  snapshot_duration)
            return fname
        assert output_name is not None
        return output_name

    def snapshot_segment_file(self, t_snapshot_start, duration, verbose=False):
        filename = "%s/%s_SEGMENTS_%d_%d.xml.gz" % (self.path, self.ifos,
                                                    t_snapshot_start, duration)
        logging.info("snapshotting %s" % filename)
        # make sure the last round of output dumping is finished
        if ((self.thread_snapshot_segment is not None)
                and (self.thread_snapshot_segment.isAlive())):
            self.thread_snapshot_segment.join()

        # free thread context
        del self.thread_snapshot_segment

        self.seg_document.filename = filename
        self.thread_snapshot_segment = threading.Thread(
            target=self.seg_document.write_output_file,
            args=(self.seg_document, ))
        self.thread_snapshot_segment.start()

        #  NOTE: del may not be necessary, as we unlink after thread completion
        del self.seg_document
        self.seg_document = SegmentDocument(self.ifos)

    def _snapshot_zerolags(self, filestem, verbose=False):
        if filestem is None or filestem.isspace():
            logging.error(
                "Cannot snapshot zerolags file as filestem '%s' is invalid." %
                filestem)

        zerolags_filepath = "%s.xml.gz" % filestem
        self.zerolags_doc_writer.write_output_file(self.postcoh_document,
                                                   zerolags_filepath, verbose)
        self.postcoh_document = PostcohDocument()

        if self.feature_dump_all_zerolags:
            all_zerolags_filepath = "%s_all.xml.gz" % filestem
            self.zerolags_doc_writer.write_output_file(
                self.all_zerolags_document, all_zerolags_filepath, verbose)
            self.all_zerolags_document = PostcohDocument()

    def __wait_internal_process_finish(self):
        if ((self.thread_snapshot_segment is not None)
                and (self.thread_snapshot_segment.isAlive())):
            self.thread_snapshot_segment.join()

        if ((self.thread_upload_skymap is not None)
                and (self.thread_upload_skymap.isAlive())):
            self.thread_upload_skymap.join()

        for thread in self.threads_gracedb_upload:
            if thread.isAlive():
                thread.join()

        self.fapupdater.await_processes()

        self.zerolags_doc_writer.await_writes()

    def __write_output_file(self,
                            zerolags_filestem=None,
                            verbose=False,
                            cleanup=False):
        self._snapshot_zerolags(zerolags_filestem, verbose)
        # FIXME: hard-coded segment filename
        if self.last_buffer_timestamp and self.t_snapshot_start:
            duration = self.last_buffer_timestamp - self.t_snapshot_start
            seg_filename = "%s/%s_SEGMENTS_%d_%d.xml.gz" % (
                self.path, self.ifos, self.t_snapshot_start, duration)
        else:
            seg_filename = "%s/%s_SEGMENTS.xml.gz" % (self.path, self.ifos)
        self.seg_document.filename = seg_filename
        self.seg_document.write_output_file(verbose=verbose, cleanup=cleanup)

    # This may be run from the launch script once the run is finished.
    def write_output_file(self,
                          zerolags_filestem=None,
                          verbose=False,
                          cleanup=False):
        self.__write_output_file(zerolags_filestem,
                                 verbose=verbose,
                                 cleanup=cleanup)
        self.__wait_internal_process_finish()


class CoincsDocFromPostcoh(object):
    sngl_inspiral_columns = ("process_id", "ifo", "end_time", "end_time_ns",
                             "eff_distance", "coa_phase", "mass1", "mass2",
                             "snr", "chisq", "chisq_dof", "bank_chisq",
                             "bank_chisq_dof", "sigmasq", "spin1x", "spin1y",
                             "spin1z", "spin2x", "spin2y", "spin2z",
                             "event_id", "Gamma0", "Gamma1")

    def __init__(self,
                 url,
                 process_params,
                 channel_dict,
                 comment=None,
                 verbose=False):
        #
        # build the XML document
        #

        self.channel_dict = channel_dict
        self.url = url
        self.xmldoc = ligolw.Document()
        self.xmldoc.appendChild(ligolw.LIGO_LW())
        self.process = ligolw_process.register_to_xmldoc(
            self.xmldoc,
            u"gstlal_inspiral_postcohspiir_online",
            process_params,
            comment=comment,
            ifos=channel_dict.keys())

        self.xmldoc.childNodes[-1].appendChild(
            lsctables.New(lsctables.SnglInspiralTable,
                          columns=self.sngl_inspiral_columns))
        self.xmldoc.childNodes[-1].appendChild(
            lsctables.New(lsctables.CoincDefTable))
        self.xmldoc.childNodes[-1].appendChild(
            lsctables.New(lsctables.CoincTable))
        self.xmldoc.childNodes[-1].appendChild(
            lsctables.New(lsctables.CoincMapTable))
        self.xmldoc.childNodes[-1].appendChild(
            lsctables.New(lsctables.TimeSlideTable))
        self.xmldoc.childNodes[-1].appendChild(
            lsctables.New(lsctables.CoincInspiralTable))
        self.xmldoc.childNodes[-1].appendChild(
            lsctables.New(postcoh_table_def.PostcohInspiralTable))

    def close(self):
        self.xmldoc.unlink()

    def assemble_ligolw_xmldoc(self, trigger, psds=None):
        postcoh_inspiral = trigger.postcoh_inspiral
        self.assemble_snglinspiral_table(postcoh_inspiral)
        coinc_def_table = lsctables.CoincDefTable.get_table(self.xmldoc)
        coinc_table = lsctables.CoincTable.get_table(self.xmldoc)
        coinc_inspiral_table = lsctables.CoincInspiralTable.get_table(
            self.xmldoc)
        postcoh_table = postcoh_table_def.PostcohInspiralTable.get_table(
            self.xmldoc)

        row = coinc_def_table.RowType()
        row.search = "inspiral"
        row.description = "sngl_inspiral<-->sngl_inspiral coincidences"
        row.coinc_def_id = "coinc_definer:coinc_def_id:3"
        row.search_coinc_type = 0
        coinc_def_table.append(row)

        row = coinc_table.RowType()
        row.coinc_event_id = "coinc_event:coinc_event_id:1"
        row.instruments = ','.join(re.findall(
            '..',
            postcoh_inspiral.ifos))  #FIXME: for more complex detector names
        row.nevents = 2
        row.process_id = self.process.process_id
        row.coinc_def_id = "coinc_definer:coinc_def_id:3"
        row.time_slide_id = "time_slide:time_slide_id:6"
        row.likelihood = 0
        coinc_table.append(row)

        row = coinc_inspiral_table.RowType()
        row.false_alarm_rate = postcoh_inspiral.fap
        row.mchirp = postcoh_inspiral.mchirp
        row.minimum_duration = postcoh_inspiral.template_duration
        row.mass = postcoh_inspiral.mtotal
        row.end_time = postcoh_inspiral.end_time
        row.coinc_event_id = "coinc_event:coinc_event_id:1"

        # add to sngl_inspiral table the network SNR = sqrt(H**2 + L**2 + V**2)
        row.snr = np.sqrt(
            np.sum([
                postcoh_inspiral.snglsnr[pipe_macro.get_ifo_id(ifo)]**2
                for ifo in re.findall("..", postcoh_inspiral.ifos)
            ]))
        row.end_time_ns = postcoh_inspiral.end_time_ns
        row.combined_far = postcoh_inspiral.far
        #FIXME: for more complex detector names
        row.ifos = ','.join(re.findall('..', postcoh_inspiral.ifos))
        coinc_inspiral_table.append(row)

        self.assemble_coinc_map_table(postcoh_inspiral)
        self.assemble_time_slide_table(postcoh_inspiral)
        self.assemble_ligolw_snr_series_arrays(trigger.snr_series_list)

        if psds is not None:
            self.assemble_ligolw_psd_arrays(psds)

        postcoh_table.append(postcoh_inspiral)

    def assemble_coinc_map_table(self, trigger):

        coinc_map_table = lsctables.CoincMapTable.get_table(self.xmldoc)
        for trigger_ifo_id, ifo in enumerate(re.findall('..', trigger.ifos)):
            row = coinc_map_table.RowType()
            row.event_id = "sngl_inspiral:event_id:%d" % trigger_ifo_id
            row.table_name = "sngl_inspiral"
            row.coinc_event_id = "coinc_event:coinc_event_id:1"
            coinc_map_table.append(row)

    def assemble_time_slide_table(self, trigger):

        time_slide_table = lsctables.TimeSlideTable.get_table(self.xmldoc)
        # FIXME: hard-coded ifo length
        for ifo in re.findall('..', trigger.ifos):
            row = time_slide_table.RowType()
            row.instrument = ifo
            row.time_slide_id = "time_slide:time_slide_id:6"
            row.process_id = self.process.process_id
            row.offset = 0
            time_slide_table.append(row)

    def assemble_snglinspiral_table(self, postcoh_inspiral):
        sngl_inspiral_table = lsctables.SnglInspiralTable.get_table(
            self.xmldoc)
        for standard_column in (
                "process_id", "ifo", "search", "channel", "end_time",
                "end_time_ns", "end_time_gmst", "impulse_time",
                "impulse_time_ns", "template_duration", "event_duration",
                "amplitude", "eff_distance", "coa_phase", "mass1", "mass2",
                "mchirp", "mtotal", "eta", "kappa", "chi", "tau0", "tau2",
                "tau3", "tau4", "tau5", "ttotal", "psi0", "psi3", "alpha",
                "alpha1", "alpha2", "alpha3", "alpha4", "alpha5", "alpha6",
                "beta", "f_final", "snr", "chisq", "chisq_dof", "bank_chisq",
                "bank_chisq_dof", "cont_chisq", "cont_chisq_dof", "sigmasq",
                "rsqveto_duration", "Gamma0", "Gamma1", "Gamma2", "Gamma3",
                "Gamma4", "Gamma5", "Gamma6", "Gamma7", "Gamma8", "Gamma9",
                "spin1x", "spin1y", "spin1z", "spin2x", "spin2y", "spin2z",
                "event_id"):
            try:
                sngl_inspiral_table.appendColumn(standard_column)
            except ValueError:
                # already has it
                pass

        for trigger_ifo_id, ifo in enumerate(
                re.findall('..', postcoh_inspiral.ifos)):
            ifo_id = pipe_macro.get_ifo_id(ifo)
            row = sngl_inspiral_table.RowType()
            # Setting the individual row
            row.process_id = self.process.process_id
            row.ifo = ifo
            row.search = self.url
            row.channel = self.channel_dict[ifo]
            row.end_time = postcoh_inspiral.end_time_sngl[ifo_id]
            row.end_time_ns = postcoh_inspiral.end_time_ns_sngl[ifo_id]
            row.end_time_gmst = 0
            row.impulse_time = 0
            row.impulse_time_ns = 0
            row.template_duration = postcoh_inspiral.template_duration
            row.event_duration = 0
            row.amplitude = 0
            row.eff_distance = postcoh_inspiral.deff[ifo_id]
            row.coa_phase = postcoh_inspiral.coaphase[ifo_id]
            row.mass1 = postcoh_inspiral.mass1
            row.mass2 = postcoh_inspiral.mass2
            row.mchirp = postcoh_inspiral.mchirp
            row.mtotal = postcoh_inspiral.mtotal
            row.eta = postcoh_inspiral.eta
            row.kappa = 0
            row.chi = 1
            row.tau0 = 0
            row.tau2 = 0
            row.tau3 = 0
            row.tau4 = 0
            row.tau5 = 0
            row.ttotal = 0
            row.psi0 = 0
            row.psi3 = 0
            row.alpha = 0
            row.alpha1 = 0
            row.alpha2 = 0
            row.alpha3 = 0
            row.alpha4 = 0
            row.alpha5 = 0
            row.alpha6 = 0
            row.beta = 0
            row.f_final = postcoh_inspiral.f_final
            row.snr = postcoh_inspiral.snglsnr[ifo_id]
            row.chisq = postcoh_inspiral.chisq[ifo_id]
            row.chisq_dof = 4
            row.bank_chisq = 0
            row.bank_chisq_dof = 0
            row.cont_chisq = 0
            row.cont_chisq_dof = 0
            row.sigmasq = 0
            row.rsqveto_duration = 0
            row.Gamma0 = 0
            row.Gamma1 = 0
            row.Gamma2 = 0
            row.Gamma3 = 0
            row.Gamma4 = 0
            row.Gamma5 = 0
            row.Gamma6 = 0
            row.Gamma7 = 0
            row.Gamma8 = 0
            row.Gamma9 = 0
            row.spin1x = postcoh_inspiral.spin1x
            row.spin1y = postcoh_inspiral.spin1y
            row.spin1z = postcoh_inspiral.spin1z
            row.spin2x = postcoh_inspiral.spin2x
            row.spin2y = postcoh_inspiral.spin2y
            row.spin2z = postcoh_inspiral.spin2z
            row.event_id = "sngl_inspiral:event_id:%d" % trigger_ifo_id
            sngl_inspiral_table.append(row)

    def assemble_ligolw_psd_arrays(self, psds):
        """Assembles a LIGO_LW REAL8FrequencySeries Array from a
        dictionary where keys are ifo strings and values are a
        lal.REAL8FrequencySeries object of the mean-psd for each
        ifo already retrieved from the pipeline.

        The PSD LIGO_LW element will then be appended to the xmldoc
        with both the REAL8FrequencySeries Array object and a 
        corresponding Param object that specifies the ifo string.

        Parameters
        ----------
        psds: dict[str, lal.REAL8FrequencySeries]
            A dictionary of frequency series objects that refer to
            the mean-psd for each ifo key.
        """
        ligolw_psds_container = ligolw.LIGO_LW(attrs={"Name": "psd"})
        for ifo, psd in psds.items():
            ligolw_psd_element = lal.series.build_REAL8FrequencySeries(psd)
            ligolw_psd_element.appendChild(
                ligolw_param.Param.build(u"instrument", u"lstring", ifo))
            ligolw_psds_container.appendChild(ligolw_psd_element)
        self.xmldoc.childNodes[-1].appendChild(ligolw_psds_container)

    def assemble_ligolw_snr_series_arrays(self, snr_series_list):
        """Assembles LIGO_LW COMPLEX8TimeSeries arrays that
        contain the SNR series for each ifo at the time
        of the candidate trigger.
        
        We loop through each ifo present in the candidate trigger and
        construct a COMPLEX8TimeSeries Array object paired with
        a corresponding Param object that points to an event_id
        present in a previously constructed SnglInspiralTable 
        in the same XML Document. Both the Array and Param
        objects are contained together in their own LIGO_LW
        element, separately for each ifo.


        Parameters
        ----------
        snr_series_list: SNRSeries[]
            The snr_series of each ifo.
        """

        # Append snr_series data into XML document
        for trigger_ifo_id, snr_series in enumerate(snr_series_list):
            if snr_series:
                epoch = LIGOTimeGPS(snr_series.epoch_gpsSeconds,
                                    snr_series.epoch_gpsNanoSeconds)
                # Convert c-based snr_series into swig-based snr_series that ligolw is familliar with
                snr_time_series = lal.CreateCOMPLEX8TimeSeries(
                    name=snr_series.name,
                    epoch=epoch,
                    f0=snr_series.f0,
                    deltaT=snr_series.deltaT,
                    sampleUnits=snr_series.sampleUnits,
                    length=snr_series.data_length)
                snr_time_series.data.data = snr_series.data

                ligolw_snr_series_element = lal.series.build_COMPLEX8TimeSeries(
                    snr_time_series)
                # Add event_id into the snr_time_series_element
                event_id = "sngl_inspiral:event_id:%d" % trigger_ifo_id
                ligolw_snr_series_element.appendChild(
                    ligolw_param.Param.build(u"event_id", u"ilwd:char",
                                             event_id))
                self.xmldoc.childNodes[-1].appendChild(
                    ligolw_snr_series_element)


def call_plot_fits_func(pngname,
                        fitsname,
                        labelname,
                        contour=None,
                        colormap="cylon"):
    cmd = []
    cmd += ["bayestar_plot_allsky_postcohspiir"]
    cmd += ["-o", pngname]
    cmd += ["--label", labelname]
    cmd += [fitsname]
    cmd += ["--colorbar"]
    cmd += ["--colormap", colormap]
    if contour:
        cmd += ["--contour", str(contour)]
    print cmd
    proc = subprocess.Popen(cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    proc_out, proc_err = proc.communicate()
    return proc.returncode


def call_fits_skymap_func(out_cohsnr_fits,
                          out_prob_fits,
                          pipe_skymap_name,
                          event_id,
                          event_time,
                          cuda_postcoh_detrsp_fname,
                          verbose=False):
    input_fname = pipe_skymap_name
    cmd = []
    cmd += ["gstlal_postcoh_skymap2fits"]
    cmd += ["--output-cohsnr", out_cohsnr_fits]
    cmd += ["--output-prob", out_prob_fits]
    cmd += ["--cuda-postcoh-detrsp-fname", cuda_postcoh_detrsp_fname]
    cmd += ["--event-id", event_id]
    cmd += ["--event-time", str(event_time)]
    cmd += [input_fname]
    if verbose:
        print cmd
    proc = subprocess.Popen(cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    proc_out, proc_err = proc.communicate()
    if verbose:
        print >> sys.stderr, "skymap2fits return code", proc.returncode
    return proc.returncode


def upload_skymap(gracedb_client, gid, ifos, skymap_fname, end_time,
                  cuda_postcoh_output_skymap, cuda_postcoh_detrsp_fname,
                  verbose):
    if not os.path.exists('gracedb'):
        os.mkdir('gracedb')

    try:
        os.mkdir('gracedb/' + gid)
    except OSError:
        pass

    out_cohsnr_fits = 'gracedb/%s/%s_cohsnr_skymap.fits.gz' % (gid, ifos)
    out_cohsnr_png = 'gracedb/%s/%s_cohsnr_skymap.png' % (gid, ifos)
    # follow Leo Single's email that fits name needs to be fixed
    out_prob_fits = 'gracedb/%s/spiir.fits.gz' % gid
    out_prob_png = 'gracedb/%s/spiir.png' % gid
    msg = ""

    try:
        copied_name = 'gracedb/%s/%s' % (gid, os.path.split(skymap_fname)[-1])
        if verbose:
            print("copy pipeline skymap %s" % skymap_fname)
        copyfile(skymap_fname, copied_name)
    except:
        msg += "no skymap generated in %s" % copied_name

    returncode = call_fits_skymap_func(out_cohsnr_fits,
                                       out_prob_fits,
                                       skymap_fname,
                                       gid,
                                       end_time,
                                       cuda_postcoh_detrsp_fname,
                                       verbose=verbose)

    if returncode == 0:
        if verbose:
            print("Uploading %s for event %s" % (out_prob_fits, gid))

        gracedb_client.writeLog(gid,
                                "%s prob skymap, with 90 percent contour" %
                                ifos,
                                filename=out_prob_fits,
                                filecontents=open(out_prob_fits).read(),
                                tag_name="sky_loc")
    else:
        msg += " can not make fits"

    returncode = call_plot_fits_func(out_cohsnr_png,
                                     out_cohsnr_fits,
                                     "Coherent SNR",
                                     contour=None,
                                     colormap="spectral")
    returncode = returncode & call_plot_fits_func(
        out_prob_png, out_prob_fits, "Prob", contour=90,
        colormap="cylon")  # default colormap
    if returncode == 0:
        if verbose:
            print("Uploading %s, %s for %s, %s " %
                  (out_prob_png, out_cohsnr_png, gid, msg))
        gracedb_client.writeLog(gid,
                                "%s prob skymap" % ifos,
                                filename=out_prob_png,
                                filecontents=open(out_prob_png).read(),
                                tag_name="sky_loc")
        gracedb_client.writeLog(gid,
                                "%s cohsnr skymap" % ifos,
                                filename=out_cohsnr_png,
                                filecontents=open(out_cohsnr_png).read(),
                                tag_name="sky_loc")
    else:
        msg += " can not plot fits to pngs"
        gracedb_client.writeLog(
            gid,
            "%s, check if it is due to that the trigger single SNR is below \
                %s in the postcoh element for a skymap output" \
            % (msg, str(cuda_postcoh_output_skymap)),
            filename=None,
            tag_name="sky_loc")
