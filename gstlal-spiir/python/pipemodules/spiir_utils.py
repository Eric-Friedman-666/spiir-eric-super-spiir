#
# Copyright (C) 2018 Qi Chu
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

import os
import re
from glue.ligolw import ligolw, lsctables, array, param, utils


# FIXME:  require calling code to provide the content handler
class DefaultContentHandler(ligolw.LIGOLWContentHandler):
    pass


array.use_in(DefaultContentHandler)
param.use_in(DefaultContentHandler)
lsctables.use_in(DefaultContentHandler)


def get_bankid_from_bankname(bankname):
    tmp_name = os.path.split(bankname)[-1]
    tmp_name = re.sub(r'[HLVK]1', '', tmp_name)
    search_result = re.search(r'\d{1,4}', tmp_name)
    try:
        bankid = search_result.group()
    except:
        raise ValueError(
            "bankid should be the first 3/4 digits of the given name, could not find the digits from %s"
            % tmp_name)

    bankid_strip = bankid.lstrip('0')
    if bankid_strip is '':
        return 0
    else:
        return int(bankid_strip)


def parse_iirbank_string(bank_string):
    """
    parses strings of form 

    H1:bank1.xml,H2:bank2.xml,L1:bank3.xml,H2:bank4.xml,... 

    into a dictionary of lists of bank files.
    """
    out = {}
    if bank_string is None:
        return out
    for b in bank_string.split(','):
        ifo, bank = b.split(':')
        out.setdefault(ifo, []).append(bank)
    return out


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
            int(float(r))
            for r in param.get_pyvalue(root, 'sample_rate').split(',')
        ]

    return max(sample_rates)


def get_negative_from_xml(filename,
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
        negative_latency = int(param.get_pyvalue(root, 'negative_latency'))

    return negative_latency
