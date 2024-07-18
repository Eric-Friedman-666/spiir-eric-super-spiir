#!/usr/bin/env python

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GObject, Gst

Gst.init(None)

# This is for parsing the ligolw file
from glue.ligolw import ligolw, lsctables, array, param, utils, types


class DefaultContentHandler(ligolw.LIGOLWContentHandler):
    pass


array.use_in(DefaultContentHandler)
param.use_in(DefaultContentHandler)
lsctables.use_in(DefaultContentHandler)

from glue import segmentsUtils
from pylal.datatypes import LIGOTimeGPS
from gstlal import pipeparts

import pdb
#pdb.set_trace()


def get_maxrate_from_xml(filename,
                         contenthandler=DefaultContentHandler,
                         verbose=False):
    xmldoc = utils.load_filename(filename,
                                 contenthandler=contenthandler,
                                 verbose=verbose)

    for root in (
            elem
            for elem in xmldoc.getElementsByTagName(ligolw.LIGO_LW.tagName)
            if elem.hasAttribute(u"Name")
            and elem.Name == "gstlal_iir_bank_Bank"):

        sample_rates = [
            int(r) for r in param.get_pyvalue(root, 'sample_rate').split(',')
        ]

    return max(sample_rates)


pipeline = Gst.Pipeline("test_multiratespiir")
mainloop = GObject.MainLoop()

# set the snr dump time range in GPS format
nxydump_segment = "0:1"
nxydump_segment, = segmentsUtils.from_range_strings([nxydump_segment],
                                                    boundtype=LIGOTimeGPS)

# make the source
src = pipeparts.mkaudiotestsrc(pipeline, volume=1, wave="sine", freq=10)

# the flowing data rate is determined by the max rate of SPIIR bank
bank_fname = "H1bank.xml"
maxrate = get_maxrate_from_xml(bank_fname)
src = pipeparts.mkcapsfilter(
    pipeline, src, "audio/x-raw, width=32, channels=1, rate=%d" % maxrate)

src = pipeparts.mkcudamultiratespiir(pipeline, src, bank_fname)
#sink = Gst.ElementFactory.make("fakesink", None)
#pipeline.add(sink)
#src.link(sink)

pipeparts.mknxydumpsink(pipeline,
                        src,
                        "snr_gpu_%d_%s.dump" %
                        (nxydump_segment[0], bank_fname[1:5]),
                        segment=nxydump_segment)

if pipeline.set_state(Gst.State.PLAYING) != Gst.GST_STATE_SUCCESS:
    raise RuntimeError, "pipeline did not enter playing state"

mainloop.run()
