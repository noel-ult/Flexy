/* SPDX-License-Identifier: MIT */
/* A deliberately tiny, headless fixture for Flexy's exact-recipe flow. */
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--version") == 0) {
        puts("flexy-demo 1.0.0");
        return 0;
    }
    if (argc == 2 && strcmp(argv[1], "--self-test") == 0) {
        puts("flexy-demo self-test: ok");
        return 0;
    }

    fputs("Usage: flexy-demo [--version|--self-test]\n", stderr);
    return 64;
}
