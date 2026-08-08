#
# adapted from postcohtable.py
# Copyright (C) 2016,2017  Kipp Cannon, Leo Singer
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

from ligo.lw import lsctables
from gstlal_spiir.pipemodules import pipe_macro
from gstlal_spiir.pipemodules.postcohtable import _postcohtable

__all__ = ["PostcohInspiral", "SNRSeries", "PostcohTrigger", "from_buffer"]

from_buffer = _postcohtable.from_buffer
SNRSeries = _postcohtable.SNRSeries
PostcohTrigger = _postcohtable.PostcohTrigger


class PostcohInspiral(_postcohtable.PostcohInspiral):
    __slots__ = ()

    end = lsctables.gpsproperty("end_time", "end_time_ns")

    def __eq__(self, other):
        return not cmp(
            (
                self.ifo,
                self.end,
                self.mass1,
                self.mass2,
                self.spin1,
                self.spin2,
                self.search,
            ),
            (
                other.ifo,
                other.end,
                other.mass1,
                other.mass2,
                other.spin1,
                other.spin2,
                other.search,
            ),
        )

    @property
    def end_time_sngl(self):
        return self._end_time_sngl[:, 0]

    @property
    def end_time_ns_sngl(self):
        return self._end_time_sngl[:, 1]

    def __getattribute__(self, name):
        try:
            return super(PostcohInspiral, self).__getattribute__(name)
        except AttributeError:
            pass
        found_ifo = None
        for ifo_id, ifo in enumerate(pipe_macro.IFO_MAP):
            if name.endswith(ifo):
                found_ifo = ifo_id
                # removesuffix() in python 3.9+
                name = name[:-len('_' + ifo)]
                break
        if found_ifo is None:
            return super(PostcohInspiral, self).__getattribute__(name)
        return super(PostcohInspiral, self).__getattribute__(name)[found_ifo]
