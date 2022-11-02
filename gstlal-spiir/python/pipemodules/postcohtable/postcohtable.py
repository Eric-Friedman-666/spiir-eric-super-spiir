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

from glue.ligolw import ilwd
from glue.ligolw import lsctables
from lal import LIGOTimeGPS
import lal
from . import _postcohtable

__all__ = [
    "PostcohInspiral", "SNRSeries", "PostcohEvent", "ifo_map", "from_buffer"
]

ifo_map = _postcohtable.ifo_map
from_buffer = _postcohtable.from_buffer


class PostcohInspiral(_postcohtable.PostcohInspiral):
    __slots__ = ()

    process_id_type = ilwd.get_ilwdchar_class("process", "process_id")
    event_id_type = ilwd.get_ilwdchar_class("postcoh", "event_id")

    @property
    def end(self):
        if self.end_time is None and self.end_time_ns is None:
            return None
        return LIGOTimeGPS(self.end_time, self.end_time_ns)

    @end.setter
    def end(self, gps):
        if gps is None:
            self.end_time = self.end_time_ns = None
        else:
            self.end_time, self.end_time_ns = gps.gpsSeconds, gps.gpsNanoSeconds

    def __eq__(self, other):
        return not cmp((self.ifo, self.end, self.mass1, self.mass2, self.spin1,
                        self.spin2, self.search),
                       (other.ifo, other.end, other.mass1, other.mass2,
                        other.spin1, other.spin2, other.search))

    @property
    def process_id(self):
        return self.process_id_type(self._process_id)

    @process_id.setter
    def process_id(self, val):
        self._process_id = int(val)

    @property
    def event_id(self):
        return self.event_id_type(self._event_id)

    @event_id.setter
    def event_id(self, val):
        self._event_id = int(val)

    def __getattribute__(self, name):
        found_ifo = None
        for i, ifo in enumerate(_postcohtable.ifo_map):
            if ifo in name:
                found_ifo = i
                name = name.replace('_' + ifo, '')
                break
        if found_ifo is None:
            return super(PostcohInspiral, self).__getattribute__(name)
        if name == "end_time_sngl":
            return self.end_time_sngl[0][found_ifo]
        elif name == "end_time_ns_sngl":
            return self.end_time_sngl[1][found_ifo]
        else:
            return super(PostcohInspiral,
                         self).__getattribute__(name)[found_ifo]

    def __setattr__(self, name, value):
        found_ifo = None
        for i, ifo in enumerate(_postcohtable.ifo_map):
            if ifo in name:
                found_ifo = i
                name = name.replace('_' + ifo, '')
                break
        if found_ifo is None:
            return super(PostcohInspiral, self).__setattr__(name, value)
        if name == "end_time_sngl":
            self.end_time_sngl[0][found_ifo] = value
        elif name == "end_time_ns_sngl":
            self.end_time_sngl[1][found_ifo] = value
        else:
            super(PostcohInspiral,
                  self).__getattribute__(name)[found_ifo] = value


class SNRSeries(_postcohtable.SNRSeries):
    __slots__ = ()


class PostcohEvent(_postcohtable.PostcohEvent):
    __slots__ = ()

    def __getattribute__(self, name):
        if name in ['postcohinspiral', 'snr_series']:
            return super(PostcohEvent, self).__getattribute__(name)
        if 'snr_series_' not in name:
            return getattr(self.postcohinspiral, name)
        name = name.replace('snr_series_', '')
        found_ifo = None
        for i, ifo in enumerate(_postcohtable.ifo_map):
            if ifo in name:
                found_ifo = i
                name = name.replace('_' + ifo, '')
                break
        if found_ifo is None:
            raise AttributeError("IFO not found.")
        return getattr(self.snr_series[found_ifo], name)

    def __setattr__(self, name, value):
        if name in ['postcohinspiral', 'snr_series']:
            return super(PostcohEvent, self).__setattr__(name, value)
        if 'snr_series_' not in name:
            return setattr(self.postcohinspiral, name, value)
        name = name.replace('snr_series_', '')
        found_ifo = None
        for i, ifo in enumerate(_postcohtable.ifo_map):
            if ifo in name:
                found_ifo = i
                name = name.replace('_' + ifo, '')
                break
        if found_ifo is None:
            raise AttributeError("IFO not found.")
        return setattr(self.snr_series[found_ifo], name, value)
