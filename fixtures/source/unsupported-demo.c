/* SPDX-License-Identifier: MIT */
/* Used only to prove that Flexy rejects unsupported packages safely. */
#include <stdio.h>

int main(void) {
    puts("This fixture should never be built by Flexy.");
    return 0;
}
