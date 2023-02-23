#ifndef __IFOMAP_HEADER__
#define __IFOMAP_HEADER__

// Standard includes
#include <stdbool.h>

const char *get_ifo_string(int ifo_id);

/* try_get_ifo_id
 *
 * Attempt to convert `ifo_string` to an IFO id. Both arguments must not be
 * NULL.
 *
 * On success, i.e. if `ifo_string` is a recognised IFO name, returns `true` and
 * `*ifo_id_out` is set to the IFO ID. Otherwise, returns false and
 * `*ifo_id_out` is undisturbed.
 */

bool try_get_ifo_id(const char *ifo_string, int *ifo_id_out);

#endif /* __IFOMAP_HEADER__ */
