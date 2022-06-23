#include <assert.h>
#include <stddef.h>
#include <stdio.h>

// FIXME: Replace with a proper testing framework for c and cuda, see #38
// NOTE: The production version of this function runs as device code in a cuda 
//      kernel (postcoh_kernel.cu)
//      assert is supported by devices with compute capability 2.x and higher
static inline size_t ring_index(ptrdiff_t ind, ptrdiff_t len) {
  assert(len > 0);
  return ((ind % len) + len) % len;
}

int main() {
    assert(ring_index(0, 1) == 0);
    assert(ring_index(0, 10) == 0);

    assert(ring_index(1, 1) == 0);
    assert(ring_index(2, 2) == 0);
    assert(ring_index(4, 2) == 0);
    assert(ring_index(-2, 2) == 0);
    assert(ring_index(-4, 2) == 0);

    assert(ring_index(1, 2) == 1);
    assert(ring_index(3, 10) == 3);
    assert(ring_index(11, 10) == 1);
    assert(ring_index(21, 10) == 1);

    assert(ring_index(-1, 2) == 1);
    assert(ring_index(-3, 10) == 7);
    assert(ring_index(-11, 10) == 9);
    assert(ring_index(-21, 10) == 9);

    // The assertion itself is not testable, and it shouldn't be tested.
    // Ideally, it would be replaced with tests on the functions that call it
    // while tests on ring_index make the code contract explicit.
    // See https://blog.odd-e.com/basvodde/2011/01/c-assert-and-unit-tests.html

    printf("ALL YOUR TESTS PASSED! CONGRATULATIONS! HAVE A COOKIE!\n");

    return 0;
}
