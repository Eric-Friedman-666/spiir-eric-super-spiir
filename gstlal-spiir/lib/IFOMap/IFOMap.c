// Standard includes
#include <assert.h>
#include <stdbool.h>
#include <string.h>

// Our includes
#include <IFOMap.h>
#include <pipe_macro.h>

// FIXME: Rename to ifo_names, see #65
static const char *IFOMap[MAX_NIFO] = {
    "H1", // 1 << 0 = 1
    "L1", // 1 << 1 = 2
    "V1", // 1 << 2 = 4
    "K1", // 1 << 3 = 8
};

const char *get_ifo_string(int ifo_id) { return IFOMap[ifo_id]; }

bool try_get_ifo_id(const char *ifo_string, int *ifo_id_out) {
    assert(ifo_string[IFO_LEN] == '\0');

    for (int ifo_id = 0; ifo_id < MAX_NIFO; ifo_id++) {
        if (strcmp(ifo_string, IFOMap[ifo_id]) == 0) {
            *ifo_id_out = ifo_id;
            return true;
        }
    }

    return false;
}
