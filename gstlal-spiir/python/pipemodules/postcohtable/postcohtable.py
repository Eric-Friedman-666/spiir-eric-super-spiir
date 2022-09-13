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
import lal
from . import _postcohtable

__all__ = ["GSTLALPostcohInspiral", "ifo_map", "from_buffer"]

ifo_map = _postcohtable.ifo_map
from_buffer = _postcohtable.from_buffer


class GSTLALPostcohInspiral(_postcohtable.GSTLALPostcohInspiral):
    __slots__ = ()

    process_id_type = ilwd.get_ilwdchar_class("process", "process_id")
    event_id_type = ilwd.get_ilwdchar_class("postcoh", "event_id")

    end = lsctables.gpsproperty("end_time", "end_time_ns")

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
