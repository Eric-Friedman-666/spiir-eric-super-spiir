#ifndef __IFO_SET_HEADER__
#define __IFO_SET_HEADER__

#include <stdbool.h>

typedef unsigned int ifo_set_type;

void ifo_set__set(ifo_set_type *ifos, const int ifo_id);

void ifo_set__unset(ifo_set_type *ifos, const int ifo_id);

int ifo_set__contains(const ifo_set_type ifos, const int ifo_id);

int ifo_set__has_shared_ifos(const ifo_set_type ifos_lhs,
                             const ifo_set_type ifos_rhs);

int ifo_set__count(const ifo_set_type ifos);

int ifo_set__is_empty(const ifo_set_type ifos);

const char *ifo_set__get_string(const ifo_set_type ifo_set);

// Parse IFO set from string representation. See implementation for details.
bool ifo_set__try_parse(const char *ifos_str, ifo_set_type *parsed_ifos);
ifo_set_type ifo_set__parse_or_empty(const char *ifos_str);

unsigned int ifo_set__renumber(const ifo_set_type src_ifos,
                               const ifo_set_type ifos_mask);

bool ifo_set__renumbered_contains(const ifo_set_type ifos,
                                  const ifo_set_type ifos_mask,
                                  const int renumbered_index);

#endif /* __IFO_SET_HEADER__ */
