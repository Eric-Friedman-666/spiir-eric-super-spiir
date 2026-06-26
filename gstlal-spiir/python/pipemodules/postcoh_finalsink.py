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
from io import BytesIO
from shutil import copyfile
import six.moves.http_client
import subprocess
import re
import time
import numpy as np
import os
import logging

import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst

Gst.init(None)

from six.moves import zip

try:
    from ligo.gracedb.rest import GraceDb
except ImportError:
    print("warning: gracedb import failed", file=sys.stderr)
    GraceDb = None

from glue import iterutils
from ligo import segments
from ligo.lw import ligolw
from ligo.lw import lsctables
from ligo.lw import array as ligolw_array
from ligo.lw import param as ligolw_param
from ligo.lw import utils as ligolw_utils

from ligo.lw.utils import process as ligolw_process
from ligo.lw.utils import segments as ligolw_segments

import lal
from lal import LIGOTimeGPS

from gstlal import bottle
from gstlal_spiir.pipemodules.postcohtable import postcoh_table_def
from gstlal_spiir.pipemodules.postcohtable import postcohtable
from gstlal_spiir.pipemodules import pipe_macro

lsctables.LIGOTimeGPS = LIGOTimeGPS

logger = logging.getLogger(__name__)


def _env_truthy(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip() in ("1", "true", "TRUE", "yes", "YES", "on", "ON")


def _normalize_int_text(value):
    if value in (None, ""):
        return ""
    try:
        return str(int(float(value)))
    except (TypeError, ValueError):
        return str(value).strip()


def _normalize_sidecar_ifo(value):
    return str(value or "").strip()


def _float_or_none(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if np.isfinite(out) else None


def _parse_ifo_mask(text):
    raw = str(text or "").strip().upper()
    if raw in ("", "NONE", "OFF", "0"):
        return set()
    raw = raw.replace("+", ",").replace("|", ",").replace("/", ",")
    if "," in raw:
        tokens = [item.strip() for item in raw.split(",") if item.strip()]
    elif raw in ("HL", "LH"):
        tokens = list(raw)
    else:
        tokens = [raw]

    out = set()
    for token in tokens:
        if token in ("H", "H1"):
            out.add("H1")
        elif token in ("L", "L1"):
            out.add("L1")
        elif token in ("V", "V1"):
            out.add("V1")
        elif token in ("K", "K1"):
            out.add("K1")
    return out


class SingleOutputPolicy(object):
    """Controls when detector-local single FARs are allowed into final output."""

    def __init__(self, mode=None, schedule=None):
        self.mode = (mode or os.environ.get("SINGLE_OUTPUT_MODE")
                     or "single-only").strip().lower().replace("_", "-")
        if self.mode in ("singleonly", "single_only"):
            self.mode = "single-only"
        self.schedule_text = (
            schedule
            if schedule is not None
            else os.environ.get("SINGLE_OUTPUT_ACTIVE_IFO_SCHEDULE")
            or os.environ.get("SINGLE_OUTPUT_DETECTOR_SCHEDULE")
            or ""
        )
        self.schedule = self._parse_schedule(self.schedule_text)

    @staticmethod
    def _parse_schedule(text):
        windows = []
        for item in str(text or "").replace(";", ",").split(","):
            item = item.strip()
            if not item:
                continue
            parts = item.split(":")
            if len(parts) != 3:
                continue
            try:
                start = float(parts[0])
                end = float(parts[1])
            except ValueError:
                continue
            if end <= start:
                continue
            windows.append((start, end, _parse_ifo_mask(parts[2])))
        return windows

    @staticmethod
    def _row_gps(postcoh_inspiral, ifo_id):
        for attr in ("end_time_sngl", "end_time"):
            try:
                value = getattr(postcoh_inspiral, attr)
            except AttributeError:
                continue
            if attr == "end_time_sngl":
                try:
                    value = value[ifo_id]
                except (TypeError, IndexError):
                    continue
            gps = _float_or_none(value)
            if gps is not None:
                return gps
        return None

    @staticmethod
    def _present_ifos(postcoh_inspiral):
        present = set()
        for ifo_id, ifo in enumerate(pipe_macro.IFO_MAP):
            snr = None
            chisq = None
            try:
                snr = _float_or_none(postcoh_inspiral.snglsnr[ifo_id])
            except (AttributeError, TypeError, IndexError):
                pass
            try:
                chisq = _float_or_none(postcoh_inspiral.chisq[ifo_id])
            except (AttributeError, TypeError, IndexError):
                pass
            if (snr is not None and snr > 0.0) or (
                    chisq is not None and chisq > 0.0):
                present.add(ifo)
        return present

    def active_ifos(self, postcoh_inspiral, ifo_id):
        gps = self._row_gps(postcoh_inspiral, ifo_id)
        if gps is not None:
            for start, end, ifos in self.schedule:
                if start <= gps < end:
                    return set(ifos)
        return self._present_ifos(postcoh_inspiral)

    def allows(self, postcoh_inspiral, ifo_id, ifo):
        if self.mode in ("all", "always", "legacy"):
            return True
        if self.mode in ("none", "never", "off"):
            return False
        active = self.active_ifos(postcoh_inspiral, ifo_id)
        return len(active) == 1 and ifo in active


class SidecarSingleFarLookup(object):
    """Cached lookup for single-detector FARs assigned by the sidecar ledger."""

    def __init__(self, paths, check_interval=1.0, output_policy=None):
        self.paths = [path for path in paths if path]
        self.check_interval = max(0.1, float(check_interval))
        self.next_check = 0.0
        self.file_state = {}
        self.lookup = {}
        self.rows_loaded = 0
        self.output_policy = output_policy or SingleOutputPolicy()

    @classmethod
    def from_environment(cls):
        single_kind = os.environ.get("SINGLE_INPUT_KIND", "zerolag")
        crashcar_enabled = os.environ.get("CRASHCAR_ENABLE", "0") == "1"
        default_enabled = (
            (single_kind in ("singlecsv", "singletriggers"))
            and not crashcar_enabled
        )
        if not _env_truthy("SIDECAR_PRESERVE_TABLE_SINGLE_FAR", default_enabled):
            return None

        run_dir = os.environ.get("RUN_DIR") or os.getcwd()
        worker = (
            os.environ.get("SINGLE_WORKER_GROUP")
            or os.environ.get("SINGLE_WORKER_ID")
            or os.environ.get("SLURM_ARRAY_TASK_ID")
            or ""
        )
        worker_padded = ""
        if worker != "":
            try:
                worker_padded = "%03d" % int(worker)
            except ValueError:
                worker_padded = str(worker)

        def expand(path):
            if not path:
                return path
            return os.path.abspath(os.path.join(
                run_dir,
                path.replace("{worker03d}", worker_padded)
                    .replace("{worker}", str(worker))
                    .replace("%03d", worker_padded)
                    .replace("%d", str(worker))))

        paths = []
        explicit = os.environ.get("SIDECAR_SINGLE_FAR_LEDGER", "")
        if explicit:
            paths.extend(expand(path) for path in explicit.split(":") if path)
        if worker != "":
            paths.append(expand(
                "single_branch/worker_{worker}/single_final_far_all.csv"))
            if worker_padded and worker_padded != str(worker):
                paths.append(expand(
                    "single_branch/worker_{worker03d}/single_final_far_all.csv"))
        paths.append(expand("single_branch/single_final_far_all.csv"))

        interval = float(os.environ.get(
            "SIDECAR_SINGLE_FAR_LOOKUP_INTERVAL_SECONDS", "1.0") or 1.0)
        return cls(paths, check_interval=interval,
                   output_policy=SingleOutputPolicy())

    @staticmethod
    def key_from_row(row):
        return (
            _normalize_sidecar_ifo(row.get("ifo") or row.get("category")),
            _normalize_int_text(row.get("end_time")),
            _normalize_int_text(row.get("end_time_ns")),
            _normalize_int_text(row.get("bankid")),
            _normalize_int_text(row.get("tmplt_idx")),
        )

    @staticmethod
    def key_from_postcoh(postcoh_inspiral, ifo_id, ifo):
        try:
            end_time = postcoh_inspiral.end_time_sngl[ifo_id]
            end_time_ns = postcoh_inspiral.end_time_ns_sngl[ifo_id]
        except (AttributeError, TypeError, IndexError):
            end_time = postcoh_inspiral.end_time
            end_time_ns = postcoh_inspiral.end_time_ns
        return (
            ifo,
            _normalize_int_text(end_time),
            _normalize_int_text(end_time_ns),
            _normalize_int_text(postcoh_inspiral.bankid),
            _normalize_int_text(postcoh_inspiral.tmplt_idx),
        )

    @staticmethod
    def row_far(row):
        for field in ("assigned_far", "far", "calculated_far", "direct_far"):
            value = row.get(field)
            if value in (None, ""):
                continue
            try:
                far = float(value)
            except (TypeError, ValueError):
                continue
            if np.isfinite(far) and far > 0.0:
                return far
        return None

    def refresh(self, force=False):
        now = time.time()
        if not force and now < self.next_check:
            return
        self.next_check = now + self.check_interval

        state = {}
        changed = False
        for path in self.paths:
            try:
                stat = os.stat(path)
            except OSError:
                continue
            state[path] = (stat.st_mtime, stat.st_size)
            if self.file_state.get(path) != state[path]:
                changed = True

        if not changed and state == self.file_state:
            return

        lookup = {}
        rows_loaded = 0
        for path in self.paths:
            if path not in state:
                continue
            try:
                with open(path, newline="") as handle:
                    for row in csv.DictReader(handle):
                        far = self.row_far(row)
                        if far is None:
                            continue
                        key = self.key_from_row(row)
                        if all(key):
                            lookup[key] = far
                            rows_loaded += 1
            except OSError:
                continue
        self.lookup = lookup
        self.file_state = state
        self.rows_loaded = rows_loaded

    def get_far(self, postcoh_inspiral, ifo_id, ifo):
        if not self.allows_output(postcoh_inspiral, ifo_id, ifo):
            return None
        self.refresh()
        return self.lookup.get(self.key_from_postcoh(
            postcoh_inspiral, ifo_id, ifo))

    def allows_output(self, postcoh_inspiral, ifo_id, ifo):
        return self.output_policy.allows(postcoh_inspiral, ifo_id, ifo)

#
# =============================================================================
#
#						 ligo.lw Content Handlers
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

    def __init__(self, ifos, verbose=False):

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
        self.process.set_end_time_now()
        ligolw_utils.write_filename(self.xmldoc,
                                    self.filename,
                                    verbose=verbose,
                                    trap_signals=None)
        if cleanup:
            self.close()


#
class PostcohDocument(object):

    def __init__(self, verbose=False):

        self.filename = None

        #
        # build the XML document
        #

        self.xmldoc = ligolw.Document()
        self.xmldoc.appendChild(ligolw.LIGO_LW())

        # FIXME: process table, search summary table
        # FIXME: should be implemented as lsctables.PostcohInspiralTable
        self.xmldoc.childNodes[-1].appendChild(
            lsctables.New(postcoh_table_def.PostcohInspiralTable))

    def close(self):
        self.xmldoc.unlink()

    def write_output_file(self, verbose=False, cleanup=True):
        assert self.filename is not None
        ligolw_utils.write_filename(self.xmldoc,
                                    self.filename,
                                    verbose=verbose,
                                    trap_signals=None)
        if cleanup:
            self.close()


class OnlinePerformer(object):

    def __init__(self, parent_lock):
        # setup bottle routes
        bottle.route("/latency_history.txt")(self.web_get_latency_history)

        self.latency_history = deque(maxlen=1000)
        self.parent_lock = parent_lock

    def web_get_latency_history(self):
        with self.parent_lock:
            # first one in the list is sacrificed for a time stamp
            for time, latency, cohsnr, cmbchisq in self.latency_history:
                yield "%f %e %f %f\n" % (time, latency, cohsnr, cmbchisq)

    def update_eye_candy(self, postcoh_inspiral):
        latency_val = (
            float(postcoh_inspiral.end),
            float(lal.UTCToGPS(time.gmtime()) - postcoh_inspiral.end),
            postcoh_inspiral.cohsnr, postcoh_inspiral.cmbchisq)
        self.latency_history.append(latency_val)


def gst_buffer_flag_is_set(buf, flags):
    # FIXME: Copied directly from GSTLAL, this should not be needed.
    # figure out how GST_BUFFER_FLAG_IS_SET() is exported via gir
    return buf.mini_object.flags & flags == flags


class FAPUpdater(object):

    def __init__(self,
                 path,
                 input_prefix_list,
                 ifos,
                 calcfap_interval,
                 combine_stats_interval,
                 output_list_string=None,
                 collect_walltime_string=None,
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
        self.rm_fnames = []

        self.last_calfap_time = None
        self.last_combine_stats_time = None
        self.collect_walltimes = []
        self.calcfap_processes = []
        if collect_walltime_string is not None:
            times = collect_walltime_string.split(",")
            for itime in times:
                self.collect_walltimes.append(int(itime))
                self.calcfap_processes.append(None)

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
                f"number of input walltimes does match the number of " \
                f"input filenames: {collect_walltime_string} does not "\
                f"match {output_list_string}")

        self.verbose = verbose

    def await_process(self, process):
        if process is not None and process.poll() is None:
            (stdoutdata, stderrdata) = process.communicate()
            if process.returncode != 0:
                logger.warning(
                    f"last process return code {process.returncode}")
                logger.warning(stderrdata)

    def await_and_clear_processes(self, processes):
        if len(processes) > 0:
            for process in processes:
                self.await_process(process)
        del processes[:]

    def get_running_processes(self, processes):
        return [
            process for process in processes
            if process is not None and process.poll() is None
        ]

    def get_available_filenames(self, keyword):
        # both calcfap and combine_stats need to access latest cleaned
        # stats files
        # make sure need-to-remove files have been removed
        self.combine_stats_processes = self.get_running_processes(
            self.combine_stats_processes)
        if len(self.combine_stats_processes) > 0:
            return None

        # remove files that have been combined from last process
        # TODO: Implement more robust file removal system for stat files
        while self.rm_fnames:
            cur_file = self.rm_fnames.pop(0)
            try:
                os.remove(cur_file)
            except Exception as exc:
                logger.warning(f"remove file failed: {cur_file}; exc: {exc}")
                return None

        ls_fnames = sorted(os.listdir(str(self.path)))
        grep_fnames = [fname for fname in ls_fnames if keyword in fname]
        # remove file names that contain "next" which are temporary files
        valid_fnames = [
            one_fname for one_fname in grep_fnames
            if not re.search("next", one_fname)
        ]
        return valid_fnames

    def get_valid_bankstats(self, ls_fnames, boundary):
        valid_fnames = []
        for ifname in ls_fnames:
            ifname_split = ifname.split("_")
            # FIXME: This assumes the format of the stats filename
            #   e.g. bank16_stats_1187008882_1800.xml.gz
            if len(ifname_split) > 1 and ifname[
                    -4:] != "next" and ifname_split[-2].isdigit() and int(
                        ifname_split[-2]) > boundary:
                valid_fnames.append("%s/%s" % (self.path, ifname))
        return valid_fnames

    def get_available_bankstats_filenames(self, boundary):
        ls_fnames = self.get_available_filenames("stats")
        if ls_fnames is None:
            return None
        if len(ls_fnames) == 0:
            return ls_fnames
        # find the files within the collection time
        bankstats_filenames = self.get_valid_bankstats(ls_fnames, boundary)

        # reach the limit for maximum input string length
        # when the number of banks reaches 140,
        # it will give you a signal 7 error in OPA2
        if len(bankstats_filenames) > self.max_nstats_for_marignalization:
            logger.info(f"update fap: {len(bankstats_filenames)} stats files "
                        "for marginalization, over the input string length "
                        "limit, combining...")
            self.try_combine_stats()
            return None
        return bankstats_filenames

    def call_calcfap(self,
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
        logger.debug(cmd)
        proc = subprocess.Popen(cmd,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        return proc

    def try_run_calcfap(self, timestamp):
        if len(self.get_running_processes(self.calcfap_processes)) > 0:
            return False

        bankstats_filenames = []
        for i, collect_walltime in enumerate(self.collect_walltimes):
            bankstats_filenames.append(
                self.get_available_bankstats_filenames(timestamp -
                                                       collect_walltime))

        # Only launch calcfap if no other process is modifying bankstats
        if any([filenames is None for filenames in bankstats_filenames]):
            return False

        for (i, collect_walltime) in enumerate(self.collect_walltimes):
            # execute the cmd in a different process
            self.calcfap_processes[i] = self.call_calcfap(
                self.output[i], bankstats_filenames[i], self.ifos)
            logging.info(f"update '{collect_walltime}' fap '{timestamp}'")
        return True

    def run_calcfap(self, timestamp):
        if self.calcfap_interval is None:
            return

        # Initialization
        if self.last_calfap_time is None:
            self.last_calfap_time = timestamp
        # Check interval
        duration = timestamp - self.last_calfap_time
        if (self.last_calfap_time is not None) and (duration
                                                    >= self.calcfap_interval):
            if self.try_run_calcfap(timestamp):
                self.last_calfap_time = timestamp

    # combine stats every day
    def try_combine_stats(self):
        ls_fnames = self.get_available_filenames("bank")
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

        if '' in stats_dict:
            del stats_dict['']

        for bankid, bank_fnames in stats_dict.items():
            collected_fnames = []
            for one_bank_fname in bank_fnames:
                # TODO: Implement more robust file removal system for stat files
                try:
                    this_walltime = int(
                        one_bank_fname.split('.')[-3].split('_')[-1])
                except ValueError as exc:
                    raise ValueError(
                        f"we've broken the {one_bank_fname} file, " \
                            f"stats_dict: {stats_dict}: {exc}")
                total_collected_walltime = sum([
                    int(os.path.split(x)[-1].split('_')[-1].split('.')[0])
                    for x in collected_fnames
                ])
                if this_walltime >= self.combine_duration:
                    continue
                elif ((len(collected_fnames) >= self.max_nstats_perbank)
                      or (total_collected_walltime >= self.combine_duration)):
                    start_banktime = int(
                        os.path.split(collected_fnames[0])[-1].split('_')[-2])
                    fout = f"{self.path}/bank{bankid}_stats_" \
                        f"{start_banktime}_{total_collected_walltime}.xml.gz"

                    proc = self.call_calcfap(fout,
                                             collected_fnames,
                                             self.ifos,
                                             update_pdf=False)
                    self.combine_stats_processes.append(proc)
                    # mark to remove collected_fnames
                    for frm in collected_fnames:
                        self.rm_fnames.append(frm)
                    collected_fnames = []
                collected_fnames.append(f"{self.path}/{one_bank_fname}")
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
            if self.try_combine_stats():
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
                 cohfar_accumbackground_output_name=None,
                 fapupdater_output_fname=None,
                 fapupdater_collect_walltime_string=None,
                 singlefar_veto_thresh=0.01,
                 chisq_ratio_veto_thresh=8.0,
                 gracedb_far_threshold=None,
                 gracedb_group="Test",
                 gracedb_search="LowMass",
                 gracedb_pipeline="spiir",
                 gracedb_service_url="https://gracedb.ligo.org/api/",
                 gracedb_upload_attempts: int = 3,
                 is_offline_analysis: bool = False,
                 output_skymap=0,
                 superevent_thresh=3.8e-7,
                 opa_cohsnr_thresh=8,
                 negative_latency=0,
                 append_psd_to_coincs_doc=True,
                 expected_buffers_per_timestamp=None,
                 feature_best_far=False,
                 feature_best_far_threshold=0,
                 single_trigger_stream_fname=None,
                 verbose=False):
        # best far
        self.enable_feature_best_far = feature_best_far
        self.best_far_threshold = feature_best_far_threshold
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
        self.superevent_thresh = superevent_thresh
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
        self.sidecar_single_far_lookup = SidecarSingleFarLookup.from_environment()
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
        self.postcoh_table = postcoh_table_def.PostcohInspiralTable.get_table(
            self.postcoh_document.xmldoc)

        # coinc doc to be uploaded to gracedb
        self.append_psd_to_coincs_doc = append_psd_to_coincs_doc
        self.coincs_document = CoincsDocFromPostcoh(path, process_params,
                                                    channel_dict)
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
        self.thread_snapshot = None
        self.thread_snapshot_segment = None
        self.t_snapshot_start = None
        self.last_buffer_timestamp = None

        # background updater
        self.fapupdater = FAPUpdater(
            path=path,
            input_prefix_list=cohfar_accumbackground_output_prefix,
            output_list_string=fapupdater_output_fname,
            collect_walltime_string=fapupdater_collect_walltime_string,
            ifos=self.ifos,
            calcfap_interval=calcfap_interval,
            combine_stats_interval=snapshot_interval,
            verbose=verbose)

        # online information performer
        self.need_online_perform = need_online_perform
        self.onperformer = OnlinePerformer(parent_lock=self.lock)

        # trigger control
        self.trigger_control_doc = "trigger_control.txt"
        if not os.path.exists(self.trigger_control_doc):
            open(self.trigger_control_doc, 'w').close()
        self.last_trigger = []
        self.last_submitted_trigger = []
        self.last_trigger.append((0, 1))
        self.last_submitted_trigger.append((0, 1))

        # skymap
        self.output_skymap = output_skymap
        self.thread_upload_skymap = None

    def __pass_test(self, postcoh_inspiral):
        if postcoh_inspiral.far <= 0.0:
            return False

        # just submit it if is a low-significance trigger
        if ((postcoh_inspiral.far < self.gracedb_far_threshold)
                and (postcoh_inspiral.far > self.superevent_thresh)):
            return True

        if ((postcoh_inspiral.far < self.opa_thresh)
                and (postcoh_inspiral.cohsnr < self.opa_cohsnr_thresh)):
            return False

        # FIXME: any two of the sngl fars need to be < singlefar_veto_thresh
        # single far veto for high-significance trigger
        # add an upper limit for the chisq for uploaded event compared to the
        # last line, hardcoded to have uploaded event with chisq < 3
        ifo_active = [
            chisq != 0 and chisq < 3 for chisq in postcoh_inspiral.chisq
        ]
        ifo_fars_ok = [
            far < self.singlefar_veto_thresh and far > 0.
            for far in postcoh_inspiral.far_sngl
        ]
        if postcoh_inspiral.far < self.superevent_thresh:
            return sum([
                i for (i, v) in zip(ifo_fars_ok, ifo_active) if v
            ]) >= 2 and all(
                (lambda x:
                 [i1 / i2 < self.chisq_ratio_thresh for i1 in x for i2 in x])([
                     i
                     for (i, v) in zip(postcoh_inspiral.chisq, ifo_active) if v
                 ]))

    # TODO: Refactor/rewrite appsink_new_buffer() and cluster(), see #36
    def appsink_new_buffer(self, elem):
        with self.lock:
            # TODO: Consider a refactor of these GStreamer 1.0 changes
            #       They were made quickly while debugging.
            buf = elem.emit("pull-sample").get_buffer()

            # Parse buffer
            buf_timestamp = LIGOTimeGPS(0, buf.pts)
            is_gap = gst_buffer_flag_is_set(buf, Gst.BufferFlags.GAP)

            newevents = []
            if not is_gap:
                (result, mapinfo) = buf.map(Gst.MapFlags.READ)
                assert result

                newevents = postcohtable.from_buffer(mapinfo.data.tobytes())

                buf.unmap(mapinfo)

            heartbeat = None
            if len(newevents) > 0:
                # FIXME: the first entry is used to add to the segments,
                #   but its not really an event
                heartbeat = newevents[0]
                newevents = newevents[1:]
            self.cluster_and_process_significant_triggers(
                buf_timestamp, buf.duration, newevents)

            self.fapupdater.run_calcfap(buf_timestamp)

            self.fapupdater.run_combine_stats(buf_timestamp)

            if not is_gap and heartbeat is not None:
                self.add_segments(heartbeat, buf_timestamp, buf.duration)

            self.run_snapshot(buf_timestamp)

            if is_gap:
                logging.info(f"buf gap at timestamp {buf_timestamp}.")
            elif heartbeat is None:
                logging.warning(
                    f"Not in gap and no heartbeat at timestamp {buf_timestamp}."
                )

            # Delete finished threads from list
            self.threads_gracedb_upload = [
                t for t in self.threads_gracedb_upload if t.is_alive()
            ]

    # This is named verbosely pending a refactor
    # It should return a list of significant triggers to be processed instead of setting self.candidate
    def cluster_and_process_significant_triggers(self, buf_timestamp, duration,
                                                 newevents):
        # Keep track of the latest timestamp seen on any buffer and number
        # of buffers seen at that timestamp. If we have as many buffers as
        # we are expecting we can process them now rather than waiting for
        # the next buffer.
        if self.current_timestamp is None \
                or buf_timestamp > self.current_timestamp:
            self.current_timestamp = buf_timestamp
            self.num_current_buffers = 0

        if buf_timestamp == self.current_timestamp:
            self.num_current_buffers += 1

        have_latest_buffers = \
            self.num_current_buffers == self.expected_buffers_per_timestamp

        max_cluster_boundary = buf_timestamp
        if have_latest_buffers:
            max_cluster_boundary = buf_timestamp + LIGOTimeGPS(0, duration)
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
        # to be > the max_cluster_boundary, see diagram in self.cluster()

        while ((self.cluster_window > 0) and (self.cluster_boundary)
               and (max_cluster_boundary > self.cluster_boundary)):
            if self.try_get_cluster_candidate():
                self.__set_far(self.candidate.postcoh_inspiral)
                if self.gracedb_far_threshold and self.__pass_test(
                        self.candidate.postcoh_inspiral):
                    self.__do_gracedb_alert(self.candidate,
                                            self.gracedb_upload_attempts)

                self.postcoh_table.append(self.candidate.postcoh_inspiral)
                self._append_single_trigger_stream_rows(
                    [self.candidate.postcoh_inspiral])

                if self.need_online_perform:
                    self.onperformer.update_eye_candy(
                        self.candidate.postcoh_inspiral)
                self.candidate = None

        # extend newevents to cur_event_table
        # Has to be done after processing pre-existing events, because this
        # buffer may contain events with end time before the buffer start
        # Will have been done above if we could process this buffer early,
        # so don't double up events if so.
        if not have_latest_buffers:
            self.cur_event_table.extend(newevents)

        if self.cluster_window == 0:
            output_rows = [event.postcoh_inspiral for event in newevents]
            self.postcoh_table.extend(
                output_rows)
            self._append_single_trigger_stream_rows(output_rows)
            del self.cur_event_table[:]

    def add_segments(self, heartbeat, buf_timestamp, duration):
        participating_ifos = re.findall('..', heartbeat.postcoh_inspiral.ifos)
        buf_seg = segments.segment(buf_timestamp,
                                   buf_timestamp + LIGOTimeGPS(0, duration))
        for segtype, one_type_dict in self.seg_document.seglistdict.items():
            for ifo in one_type_dict.keys():
                if ifo in participating_ifos:
                    this_seglist = one_type_dict[ifo]
                    this_seglist = this_seglist + segments.segmentlist(
                        [buf_seg])
                    this_seglist.coalesce()
                    one_type_dict[ifo] = this_seglist
        self._append_single_trigger_stream_boundaries(
            participating_ifos, buf_timestamp + LIGOTimeGPS(0, duration))

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
        with open(self.single_trigger_stream_fname, "a", newline="") as output_file:
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
            "source_kind": "postcoh_trigger",
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

    def run_snapshot(self, timestamp):
        # Initialization
        if self.t_snapshot_start is None:
            self.t_snapshot_start = timestamp
        # Check interval
        duration = timestamp - self.t_snapshot_start
        if ((self.snapshot_interval is not None)
                and (duration >= self.snapshot_interval)):
            self.snapshot_segment_file(self.t_snapshot_start, duration)
            zerolag_snapshot_filename = self.get_output_filename(
                self.output_prefix, self.output_name, self.t_snapshot_start,
                duration)
            self.snapshot_output_file(zerolag_snapshot_filename)
            self.t_snapshot_start = timestamp

        # Record the last timestamp so remaining stats can be dumped on program end.
        self.last_buffer_timestamp = timestamp

    def try_get_cluster_candidate(self):
        # send candidate to be gracedb checked only when:
        # timestamp small ->->->-> large
        #                 |max_cluster_boundary
        #      ___________(cur_table)
        #          |boundary
        #       |candidate to check = end time of cur_table peak < boundary
        #            |candidate remain = end time of cur_table peak > boundary
        # afterwards:
        #                     |max_cluster_boundary
        #                 ____(cur_table cleaned)
        #                           |boundary incremented

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

        # cur_table is empty and we do have a candidate,
        # so need to check the candidate
        if peak_event is None:
            # no event within boundary, candidate is the peak, update boundary
            self.cluster_boundary = self.cluster_boundary + self.cluster_window
            return self.candidate is not None

        if self.candidate is None or is_better_event(
                peak_event.postcoh_inspiral, self.candidate.postcoh_inspiral):
            # slide window so the centre becomes the peak_event
            self.candidate = peak_event
            iterutils.inplace_filter(
                lambda row: row.postcoh_inspiral.end > self.cluster_boundary,
                self.cur_event_table)
            # update boundary
            # NOTE: cluster boundary does not necessarily align with
            #   buffer boundary
            self.cluster_boundary = self.candidate.postcoh_inspiral.end + self.cluster_window
            return False
        else:
            # FIXME: This seems to assume buffer length >= cluster_window
            # pop out candidate for gracedb uploading
            iterutils.inplace_filter(
                lambda row: row.postcoh_inspiral.end > self.cluster_boundary,
                self.cur_event_table)
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

        ifo_fars = list(
            zip(postcoh_inspiral.far_1w_sngl, postcoh_inspiral.far_1d_sngl,
                postcoh_inspiral.far_2h_sngl))
        ifo_nevents = list(
            zip(postcoh_inspiral.nevent_1w_sngl,
                postcoh_inspiral.nevent_1d_sngl,
                postcoh_inspiral.nevent_2h_sngl))

        return [
            self.__get_valid_fars(ifo_fars[i], ifo_nevents[i])
            for i in range(len(ifo_fars))
        ]

    def __set_far(self, postcoh_inspiral):
        preserve_crashcar_single_far = (
            os.environ.get("CRASHCAR_ENABLE", "0") == "1"
            and os.environ.get("CRASHCAR_PRESERVE_TABLE_SINGLE_FAR", "1") != "0")
        crashcar_single_far = None
        if preserve_crashcar_single_far:
            crashcar_single_far = list(postcoh_inspiral.far_sngl)

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
            if (crashcar_single_far is not None
                    and crashcar_single_far[ifo_id] > 0.0):
                postcoh_inspiral.far_sngl[ifo_id] = crashcar_single_far[ifo_id]
            else:
                postcoh_inspiral.far_sngl[ifo_id] = far_sngl[ifo_id]
        self.__apply_sidecar_single_far(postcoh_inspiral)

    def __apply_sidecar_single_far(self, postcoh_inspiral):
        if self.sidecar_single_far_lookup is None:
            return 0
        updated = 0
        for ifo_id, ifo in enumerate(pipe_macro.IFO_MAP):
            if not self.sidecar_single_far_lookup.allows_output(
                    postcoh_inspiral, ifo_id, ifo):
                postcoh_inspiral.far_sngl[ifo_id] = 0.0
                postcoh_inspiral.far_1w_sngl[ifo_id] = 0.0
                postcoh_inspiral.far_1d_sngl[ifo_id] = 0.0
                postcoh_inspiral.far_2h_sngl[ifo_id] = 0.0
                continue
            far = self.sidecar_single_far_lookup.get_far(
                postcoh_inspiral, ifo_id, ifo)
            if far is None:
                continue
            postcoh_inspiral.far_sngl[ifo_id] = far
            postcoh_inspiral.far_1w_sngl[ifo_id] = far
            postcoh_inspiral.far_1d_sngl[ifo_id] = far
            postcoh_inspiral.far_2h_sngl[ifo_id] = far
            updated += 1
        return updated

    def __apply_sidecar_single_far_to_table(self):
        if self.sidecar_single_far_lookup is None:
            return
        self.sidecar_single_far_lookup.refresh(force=True)
        updated = 0
        for postcoh_inspiral in self.postcoh_table:
            updated += self.__apply_sidecar_single_far(postcoh_inspiral)
        if updated:
            logger.info(
                "sidecar single FAR zerolag fill: updated %d detector rows "
                "from %d ledger rows",
                updated, self.sidecar_single_far_lookup.rows_loaded)

    def __read_trigger_control(self):
        with open(self.trigger_control_doc, "r") as f:
            content = f.read().splitlines()

        if len(content) > 0:
            (last_time, last_far, is_submitted) = content[-1].split(",")
            cur_idx = -1
            while is_submitted == "0" and len(content) + cur_idx > 0:
                cur_idx = cur_idx - 1
                (last_time, last_far,
                 is_submitted) = content[cur_idx].split(",")
            if is_submitted == "1":
                return float(last_time), float(last_far)
        return self.last_trigger[-1][0], self.last_trigger[-1][1]

    def __need_trigger_control(self, trigger):
        # Suppress the trigger if a recent, better upload has been completed.
        # FIXME: implement a sql solution for node communication ?
        last_time = 0
        last_far = 0
        is_read_successful = False
        gracedb_upload_attempts = self.gracedb_upload_attempts

        for i in range(gracedb_upload_attempts):
            try:
                (last_time, last_far) = self.__read_trigger_control()
                is_read_successful = True

            except Exception as e:
                # Log a message, but no need to wait before retrying,
                # the file was most likely in the process of being written to.
                # It should finish very quickly.
                msg = f"[{i+1}/{gracedb_upload_attempts}]"\
                                f"failed with error: '{e}'."
                logger.info(msg)

        if not is_read_successful:
            last_time = self.last_trigger[-1][0]
            last_far = self.last_trigger[-1][1]

        last_submitted_time = last_time
        last_submitted_far = last_far

        # suppress the trigger
        # if it is not one order of magnitude more significant than the last
        # trigger or if it not more significant the last submitted trigger
        # FIXME: what if there are two adjacent significant events
        trigger_control_log = f"time {float(trigger.end)}, " \
            f"FAR {trigger.far}, " \
            f"last_submitted time {last_submitted_time}, " \
            f"last_submitted far {last_submitted_far}"
        if ((abs(float(trigger.end) - last_time) < 50
             and abs(trigger.far / last_far) > 0.5)) or (
                 abs(float(trigger.end) - float(last_submitted_time)) < 100
                 and trigger.far > last_submitted_far * 0.5):
            trigger_is_submitted = 0
            logger.info(f"trigger controlled, {trigger_control_log}")
            self.last_trigger.append((trigger.end, trigger.far))
            line = f"{float(trigger.end)},{trigger.far},{trigger_is_submitted}\n"
            with open(self.trigger_control_doc, "a") as f:
                f.write(line)
            return True

        logger.info(f"trigger passed, {trigger_control_log}")

        trigger_is_submitted = 1
        #self.last_trigger.append((trigger.end, trigger.far))
        #self.last_submitted_trigger.append((trigger.end, trigger.far))
        line = f"{float(trigger.end)},{trigger.far},{trigger_is_submitted}\n"
        with open(self.trigger_control_doc, "a") as f:
            f.write(line)

        return False

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

    def upload_to_gracedb(self, gracedb_upload_attempts, filename,
                          coinc_message, log_message):
        # TODO: Review indexing for non-active ifos in snr_series_list

        gracedb_id = None

        for i in range(gracedb_upload_attempts):
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
                log_prefix = f"[{i+1}/{gracedb_upload_attempts}]"\
                                f" graceid upload '{filename}'"
                if resp.status == six.moves.http_client.CREATED:
                    gracedb_id = resp_json["graceid"]
                    logger.info(
                        f"{log_prefix} succeeded with id '{gracedb_id}'")
                    break
                else:
                    logger.info(f"{log_prefix} failed")
            except Exception as e:
                logger.info(e)

        coinc_message.close()

        if gracedb_id is not None:

            for i in range(gracedb_upload_attempts):
                try:
                    resp = self.gracedb_client.writeLog(
                        gracedb_id,
                        log_message,
                        filename=None,
                        tagname="analyst_comments")
                    log_prefix = f"[{i+1}/{gracedb_upload_attempts}]"\
                                " gracedb upload of log"
                    if resp.status == six.moves.http_client.CREATED:
                        logger.info(f"{log_prefix} succeeded")
                        break
                    else:
                        logger.info(f"{log_prefix} failed")
                except Exception as e:
                    logger.info(e)
        else:
            logger.info(f"gracedb upload of '{filename}' failed completely")

    def __do_gracedb_alert(self, trigger, gracedb_upload_attempts=3):

        postcoh_inspiral = trigger.postcoh_inspiral

        if self.__need_trigger_control(postcoh_inspiral):
            return

        # TODO: Remove conditional bool here and in __init__ after tests
        if self.append_psd_to_coincs_doc:
            psds = {
                ifo: self.get_current_lal_psd_frequency_series(ifo)
                for ifo in re.findall("..", postcoh_inspiral.ifos)
            }
        else:
            psds = None

        self.coincs_document.assemble_ligolw_xmldoc(trigger, psds)
        filename = "%s_%s_%d_%d.xml" % (
            postcoh_inspiral.ifos, postcoh_inspiral.end_time,
            postcoh_inspiral.bankid, postcoh_inspiral.tmplt_idx)

        logger.info(f"writing {filename} to disk ...")
        ligolw_utils.write_filename(self.coincs_document.xmldoc,
                                    filename,
                                    trap_signals=None)

        if self.gracedb_client is not None:
            # Construct message and send to gracedb.
            # We go through the intermediate step of first writing the xmldoc
            # into a string buffer incase there is some safety in doing so in
            # the event of a malformed document; instead of writing directly
            # into gracedb's  input pipe and crashing part way through.
            coinc_message = BytesIO()
            ligolw_utils.write_fileobj(self.coincs_document.xmldoc,
                                       coinc_message)

            logger.info(f"sending '{filename}' to gracedb ...")

            log_message = f"Optimal ra and dec from this coherent pipeline: " \
                f"({postcoh_inspiral.ra}, {postcoh_inspiral.dec}) in degrees"

            gracedb_upload_thread = threading.Thread(
                target=self.upload_to_gracedb,
                args=(gracedb_upload_attempts, filename, coinc_message,
                      log_message))
            gracedb_upload_thread.start()
            self.threads_gracedb_upload.append(gracedb_upload_thread)

        # close the xmldoc for garbage collection and prepare a new empty one
        self.coincs_document.close()

        # NOTE: It may make more sense to create this doc earlier elsewhere
        #   rather than at the end of this function (separation of concerns)
        self.coincs_document = CoincsDocFromPostcoh(self.path,
                                                    self.process_params,
                                                    self.channel_dict)

    def get_output_filename(self, output_prefix, output_name, t_snapshot_start,
                            snapshot_duration):
        if output_prefix is not None:
            fname = "%s_%d_%d.xml.gz" % (output_prefix, t_snapshot_start,
                                         snapshot_duration)
            return fname
        assert output_name is not None
        return output_name

    def snapshot_segment_file(self, t_snapshot_start, duration, verbose=False):
        filename = "%s/%s_SEGMENTS_%d_%d.xml.gz" % (self.path, self.ifos,
                                                    t_snapshot_start, duration)
        logger.info(f"snapshotting {filename}")
        # make sure the last round of output dumping is finished
        if ((self.thread_snapshot_segment is not None)
                and (self.thread_snapshot_segment.is_alive())):
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

    def snapshot_output_file(self, filename, verbose=False):
        # make sure the last round of output dumping is finished
        logger.info(f"snapshotting {filename}")
        if self.thread_snapshot is not None and self.thread_snapshot.is_alive(
        ):
            self.thread_snapshot.join()

        self.__apply_sidecar_single_far_to_table()
        self.postcoh_document.filename = filename
        # free thread context
        del self.thread_snapshot
        self.thread_snapshot = threading.Thread(
            target=self.postcoh_document.write_output_file,
            args=(self.postcoh_document, ))
        self.thread_snapshot.start()

        #  NOTE: del may not be necessary, as we unlink after thread completion
        del self.postcoh_table
        del self.postcoh_document
        self.postcoh_document = PostcohDocument()
        self.postcoh_table = postcoh_table_def.PostcohInspiralTable.get_table(
            self.postcoh_document.xmldoc)

    def __wait_internal_process_finish(self):
        if self.thread_snapshot is not None and self.thread_snapshot.is_alive(
        ):
            self.thread_snapshot.join()

        if ((self.thread_snapshot_segment is not None)
                and (self.thread_snapshot_segment.is_alive())):
            self.thread_snapshot_segment.join()

        if ((self.thread_upload_skymap is not None)
                and (self.thread_upload_skymap.is_alive())):
            self.thread_upload_skymap.join()

        for thread in self.threads_gracedb_upload:
            if thread.is_alive():
                thread.join()

        self.fapupdater.await_and_clear_processes(
            self.fapupdater.calcfap_processes)
        self.fapupdater.await_and_clear_processes(
            self.fapupdater.combine_stats_processes)

    # This may be run from the launch script once the run is finished.
    def write_output_file(self, filename=None, verbose=False, cleanup=False):
        self.__wait_internal_process_finish()
        self.__write_output_file(filename, verbose=verbose, cleanup=cleanup)

    def __write_output_file(self, filename=None, verbose=False, cleanup=False):
        if filename is not None:
            self.postcoh_document.filename = filename
        self.postcoh_document.write_output_file(verbose=verbose,
                                                cleanup=cleanup)
        # FIXME: hard-coded segment filename
        if self.last_buffer_timestamp and self.t_snapshot_start:
            duration = self.last_buffer_timestamp - self.t_snapshot_start
            seg_filename = "%s/%s_SEGMENTS_%d_%d.xml.gz" % (
                self.path, self.ifos, self.t_snapshot_start, duration)
        else:
            seg_filename = "%s/%s_SEGMENTS.xml.gz" % (self.path, self.ifos)
        self.seg_document.filename = seg_filename
        self.seg_document.write_output_file(verbose=verbose, cleanup=cleanup)


class CoincsDocFromPostcoh(object):
    sngl_inspiral_columns = ("process:process_id", "ifo", "end_time",
                             "end_time_ns", "eff_distance", "coa_phase",
                             "mass1", "mass2", "snr", "chisq", "chisq_dof",
                             "bank_chisq", "bank_chisq_dof", "sigmasq",
                             "spin1x", "spin1y", "spin1z", "spin2x", "spin2y",
                             "spin2z", "event_id", "Gamma0", "Gamma1")

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
            instruments=channel_dict.keys())

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
        row.coinc_def_id = 3
        row.search_coinc_type = 0
        coinc_def_table.append(row)

        row = coinc_table.RowType()
        row.coinc_event_id = 1
        row.instruments = ','.join(re.findall(
            '..',
            postcoh_inspiral.ifos))  #FIXME: for more complex detector names
        row.nevents = 2
        row.process_id = self.process.process_id
        row.coinc_def_id = 3
        row.time_slide_id = 6
        row.likelihood = 0
        coinc_table.append(row)

        row = coinc_inspiral_table.RowType()
        row.false_alarm_rate = postcoh_inspiral.fap
        row.mchirp = postcoh_inspiral.mchirp
        row.minimum_duration = postcoh_inspiral.template_duration
        row.mass = postcoh_inspiral.mtotal
        row.end_time = postcoh_inspiral.end_time
        row.coinc_event_id = 1

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
            row.event_id = trigger_ifo_id
            row.table_name = "sngl_inspiral"
            row.coinc_event_id = 1
            coinc_map_table.append(row)

    def assemble_time_slide_table(self, trigger):

        time_slide_table = lsctables.TimeSlideTable.get_table(self.xmldoc)
        # FIXME: hard-coded ifo length
        for ifo in re.findall('..', trigger.ifos):
            row = time_slide_table.RowType()
            row.instrument = ifo
            row.time_slide_id = 6
            row.process_id = self.process.process_id
            row.offset = 0
            time_slide_table.append(row)

    def assemble_snglinspiral_table(self, postcoh_inspiral):
        sngl_inspiral_table = lsctables.SnglInspiralTable.get_table(
            self.xmldoc)
        for standard_column in (
                "process:process_id", "ifo", "search", "channel", "end_time",
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
            row.event_id = trigger_ifo_id
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
    logger.debug(cmd)
    proc = subprocess.Popen(cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    proc_out, proc_err = proc.communicate()
    logger.debug(f"bayestar_plot_allsky return code: {proc.returncode}")

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

    logger.debug(cmd)
    proc = subprocess.Popen(cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    proc_out, proc_err = proc.communicate()
    logger.debug(f"skymap2fits return code: {proc.returncode}")

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

        logger.debug(f"copy pipeline skymap {skymap_fname}")
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
        logger.debug(f"Uploading {out_prob_fits} for event {gid}")

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
        logger.debug(
            f"Uploading {out_prob_png}, {out_cohsnr_png} for {gid}, {msg}")
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
            "%s, check if it is due to that the trigger single SNR is " \
                "below %s in the postcoh element for a skymap output" \
            % (msg, str(cuda_postcoh_output_skymap)),
            filename=None,
            tag_name="sky_loc")
