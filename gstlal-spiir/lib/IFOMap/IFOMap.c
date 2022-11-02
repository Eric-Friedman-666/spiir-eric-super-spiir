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
