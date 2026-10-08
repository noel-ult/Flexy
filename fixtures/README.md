# Flexy fixtures

`flexy-demo_1.0.0_amd64.deb` is a tiny MIT-licensed Linux x86_64 command-line
program used to exercise the only supported conversion recipe in v1. It has no
maintainer scripts and declares `libc6 (>= 2.37)`, which the recipe maps to
Arch Linux `glibc`.

`unsupported-demo_1.0.0_amd64.deb` deliberately declares an unmapped
dependency and contains a `postinst` script. It is a safety fixture: analysis
must reject it, and no build job may run.

Regenerate both packages with `python3 scripts/build_fixtures.py`. The output
is deterministic for a given compiler/toolchain but the supported recipe pins
the generated executable hash, so regenerate its manifest with the same tool.
