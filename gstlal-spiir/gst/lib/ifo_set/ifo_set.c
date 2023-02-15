// Standard includes
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

// Our includes
#include <IFOMap.h>
#include <ifo_set.h>
#include <pipe_macro.h>

// We can determine if the IFO at ifo_id is in the ifo_set by
// checking if that power of two exists in the ifo_set
int ifo_set__contains(const ifo_set_type ifos, const int ifo_id) {
    return ifos & (1 << ifo_id);
}

int ifo_set__has_shared_ifos(const ifo_set_type ifos_lhs,
                             const ifo_set_type ifos_rhs) {
    return ifos_lhs & ifos_rhs;
}

int ifo_set__count(const ifo_set_type ifos) { return __builtin_popcount(ifos); }

int ifo_set__is_empty(const ifo_set_type ifos) {
    return ifo_set__count(ifos) == 0;
}

// { K1, V1, L1, H1 } bitset - note that H1 is the least-significant bit
#define MAX_IFO_SET 0b1111

// Mapping from IFO set to string representation
// Index is one less than the bitset representation of the set
static const char *IFOComboMap[MAX_IFO_SET] = {
    "H1",   "L1",   "H1L1",   "V1",   "H1V1",   "L1V1",   "H1L1V1",   "K1",
    "H1K1", "L1K1", "H1L1K1", "V1K1", "H1V1K1", "L1V1K1", "H1L1V1K1",
};

const char *ifo_set__get_string(const ifo_set_type ifo_set) {
    return IFOComboMap[ifo_set - 1];
}

// Try to parse the set of IFOs specified by ifo_str of the form "H1V1".
// Simple greedy parser that assumes no IFO name is a prefix of another.
// Empty string is considered invalid input.
// ifos_str: A string representing a set of IFOs, e.g: "H1V1", "V1L1".
// parsed_ifos (output): The set of parsed IFOs, or unmodified on failure.
// return: True iff the parse was successful.
bool ifo_set__try_parse(const char *ifos_str, ifo_set_type *parsed_ifos) {
    // Empty string is invalid input
    if (*ifos_str == '\0') return false;

    ifo_set_type ifos = 0;

    // Read IFO names until we hit end of ifos_str
    // If we fail to find an IFO, we return failure
    const char *ifos_str_end = ifos_str + strlen(ifos_str);
    while (ifos_str < ifos_str_end) {
        bool found_ifo = false;

        // Take first IFO that is a prefix of the remaining string
        for (size_t ifo_id = 0; ifo_id < MAX_NIFO; ++ifo_id) {
            // Don't try to match IFOs already in set, as duplicates are invalid
            if (ifo_set__contains(ifos, ifo_id)) continue;

            const char *ifo_name = get_ifo_string(ifo_id);
            size_t ifo_name_len  = strlen(ifo_name);
            if (!strncmp(ifos_str, ifo_name, ifo_name_len)) {
                // Insert into bitset and progress string
                ifos |= 1 << ifo_id;
                ifos_str += ifo_name_len;
                found_ifo = true;
                break;
            }
        }

        // If none of the IFOs match, parsing has failed
        if (!found_ifo) { return false; }
    }

    *parsed_ifos = ifos;
    return true;
}

// Parse the set of IFOs specified by ifo_str of the form "H1V1".
// Defaults to empty set on failed parse.
// Empty string is considered invalid input.
// Prints an error message to stderr.
// ifos_str: A string representing a set of IFOs, e.g: "H1V1", "V1L1".
// return: The set of parsed IFOs, or the empty set on failure.
ifo_set_type ifo_set__parse_or_empty(const char *ifos_str) {
    ifo_set_type parsed_ifos = 0;
    if (!ifo_set__try_parse(ifos_str, &parsed_ifos)) {
        fprintf(stderr, "ifo_set__try_parse: failed to parse ifo set \"%s\"\n",
                ifos_str);
    }
    return parsed_ifos;
}
