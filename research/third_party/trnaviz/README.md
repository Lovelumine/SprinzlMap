# tRNAviz Numbering Models

This directory contains unmodified covariance-model files used by the direct Sprinzl-numbering backend.

- Upstream: https://github.com/UCSC-LoweLab/tRNAviz
- Upstream commit: `22d3e524d24975d644f782ac06ad589df954506c`
- License: GNU General Public License v3; see [LICENSE](./LICENSE)
- Runtime engine: Infernal `cmalign`

Model SHA-256 values:

```text
all-num.cm  55a17fb2a3f62cff92f01f084c3347c080a0023cee9bb47e6d110f14deda73bf
arch-num.cm e9d86b32b2f5dbf690b32eb083546fb100ea66aa1f990bacd752bd0d0bfc67b8
bact-num.cm 48f36f67e0380ea7172bec173f3b977e6e9695c6a78658449505248c6b2a5624
euk-num.cm  2e6ee14930be8e537e0d5054cd86216a1c135901cf3beb6d8fe974c0d2a0349b
```

The application does not copy or import the upstream Python position parser. It derives the 98 ordered Sprinzl match states and insertion labels directly from Infernal's `SS_cons` alignment annotation. Tests verify the expected model hashes, match-state count, base preservation, monotonic labels, and representative output.
