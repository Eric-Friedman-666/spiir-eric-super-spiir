#include <assert.h>
#include <math.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>

#define MAX_NIFO 4
typedef unsigned int ifo_set_type;

int ifo_set__count(const ifo_set_type ifos) { return __builtin_popcount(ifos); }

// We can determine if the IFO at ifo_id is in the ifo_set by
// checking if that power of two exists in the ifo_set
int ifo_set__contains(const ifo_set_type ifos, const int ifo_id) {
    return ifos & (1 << ifo_id);
}

#ifndef NDEBUG
static void assert_subset(const ifo_set_type subset_ifos,
                          const ifo_set_type superset_ifos) {
    for (int ifo_id = 0; ifo_id < MAX_NIFO; ifo_id++) {
        assert(ifo_set__contains(superset_ifos, ifo_id)
               || (!ifo_set__contains(superset_ifos, ifo_id)
                   && !ifo_set__contains(subset_ifos, ifo_id)));
    }
}
#endif

unsigned int ifo_set__renumber(const ifo_set_type src_ifos,
                               const ifo_set_type ifos_mask) {
#ifndef NDEBUG
    assert_subset(src_ifos, ifos_mask);
#endif

    unsigned int ifos    = 0;
    int renumbered_index = 0;
    for (int ifo_id = 0; ifo_id < MAX_NIFO; ifo_id++) {
        if (ifo_set__contains(ifos_mask, ifo_id)) {
            if (ifo_set__contains(src_ifos, ifo_id)) {
                ifos |= 1 << renumbered_index;
            }
            renumbered_index++;
        }
    }

    return ifos;
}

static void test__ifo_set__renumber() {
    // When mask is contiguous
    //      Returns src_ifos
    for (int src_ifos = 0; src_ifos < pow(2, MAX_NIFO); src_ifos++) {
        assert(ifo_set__renumber(src_ifos, 0b1111) == src_ifos);
    }

    // Some more examples:
    assert(ifo_set__renumber(0b0000, 0b0000) == 0b0000);
    assert(ifo_set__renumber(0b0010, 0b0011) == 0b0010);

    // When mask is not contiguous
    //      Returns intersection renumbered
    assert(ifo_set__renumber(0b0000, 0b1101) == 0b000);
    assert(ifo_set__renumber(0b0001, 0b1101) == 0b001);
    assert(ifo_set__renumber(0b0100, 0b1101) == 0b010);
    assert(ifo_set__renumber(0b1000, 0b1101) == 0b100);
    assert(ifo_set__renumber(0b1101, 0b1101) == 0b111);
    assert(ifo_set__renumber(0b0000, 0b1100) == 0b00);
    assert(ifo_set__renumber(0b0100, 0b1100) == 0b01);
    assert(ifo_set__renumber(0b1000, 0b1100) == 0b10);
    assert(ifo_set__renumber(0b1100, 0b1100) == 0b11);
}

// FIXME: Replace with a proper testing framework for c and cuda, see #38
bool ifo_set__renumbered_contains(const ifo_set_type ifos,
                                  const ifo_set_type ifos_mask,
                                  const int renumbered_index) {
    assert(renumbered_index < ifo_set__count(ifos_mask));

    unsigned int renumbered_ifos = ifo_set__renumber(ifos, ifos_mask);
    return renumbered_ifos & (1 << renumbered_index);
}

static void test__ifo_set__renumbered_contains() {
    // When src_ifos contains the ifo in mask
    //      Returns true in a contiguous mask
    assert(ifo_set__renumbered_contains(0b1111, 0b1111, 0) == true);
    assert(ifo_set__renumbered_contains(0b1111, 0b1111, 1) == true);
    assert(ifo_set__renumbered_contains(0b1111, 0b1111, 2) == true);
    assert(ifo_set__renumbered_contains(0b1111, 0b1111, 3) == true);
    assert(ifo_set__renumbered_contains(0b0001, 0b1111, 0) == true);
    assert(ifo_set__renumbered_contains(0b0010, 0b1111, 1) == true);
    assert(ifo_set__renumbered_contains(0b0100, 0b1111, 2) == true);
    assert(ifo_set__renumbered_contains(0b1000, 0b1111, 3) == true);
    //      Returns true in a truncated mask
    assert(ifo_set__renumbered_contains(0b0001, 0b0001, 0) == true);
    assert(ifo_set__renumbered_contains(0b0010, 0b0011, 1) == true);
    assert(ifo_set__renumbered_contains(0b0100, 0b0111, 2) == true);
    //      Returns true in a non contiguous mask
    assert(ifo_set__renumbered_contains(0b0001, 0b1101, 0) == true);
    assert(ifo_set__renumbered_contains(0b0100, 0b1101, 1) == true);
    assert(ifo_set__renumbered_contains(0b1000, 0b1101, 2) == true);
    assert(ifo_set__renumbered_contains(0b0100, 0b1100, 0) == true);
    assert(ifo_set__renumbered_contains(0b1000, 0b1100, 1) == true);

    // When src_ifos does not contain ifo in mask
    //      Returns false in a contiguous mask
    assert(ifo_set__renumbered_contains(0b0000, 0b1111, 0) == false);
    assert(ifo_set__renumbered_contains(0b0000, 0b1111, 1) == false);
    assert(ifo_set__renumbered_contains(0b0000, 0b1111, 2) == false);
    assert(ifo_set__renumbered_contains(0b0000, 0b1111, 3) == false);
    assert(ifo_set__renumbered_contains(0b1110, 0b1111, 0) == false);
    assert(ifo_set__renumbered_contains(0b1101, 0b1111, 1) == false);
    assert(ifo_set__renumbered_contains(0b1011, 0b1111, 2) == false);
    assert(ifo_set__renumbered_contains(0b0111, 0b1111, 3) == false);
    //      Returns false in a truncated mask
    assert(ifo_set__renumbered_contains(0b0000, 0b0001, 0) == false);
    assert(ifo_set__renumbered_contains(0b0001, 0b0011, 1) == false);
    assert(ifo_set__renumbered_contains(0b0011, 0b0111, 2) == false);
    //      Returns false in a non contiguous mask
    assert(ifo_set__renumbered_contains(0b1100, 0b1101, 0) == false);
    assert(ifo_set__renumbered_contains(0b1001, 0b1101, 1) == false);
    assert(ifo_set__renumbered_contains(0b0101, 0b1101, 2) == false);
    assert(ifo_set__renumbered_contains(0b1000, 0b1100, 0) == false);
    assert(ifo_set__renumbered_contains(0b0100, 0b1100, 1) == false);
}

int main() {
    test__ifo_set__renumber();
    test__ifo_set__renumbered_contains();

    // Note assertions are not testable, and shouldn't be tested.
    // Ideally, it would be replaced with tests on the functions that call it
    // while tests on ring_index make the code contract explicit.
    // See https://blog.odd-e.com/basvodde/2011/01/c-assert-and-unit-tests.html

    printf("ALL YOUR TESTS PASSED! CONGRATULATIONS! HAVE A COOKIE!\n");

    return 0;
}
