# tRNA 3′ end comparison

The figure compares physical RNA ends in the supplied aligned coordinate frame. All five structures are retained. Panel a uses C3′ backbone traces; b–e use distance-inferred covalent heavy-atom sticks for the final six residues, with translucent ring footprints on the final three bases. All close-ups use identical orthographic projection and scale; no local fitting or distortion was applied. Colour denotes identity, not confidence.

Reference: C71–C72–G73–C74–C75–A76. Actual model residue numbers are retained. DL2150 ends in C82–C83–A84; DX1480 ends in C72–A73–A74 (CAA). These are physical end comparisons, not validated homologous mappings.

The supplied alignment has no documented fitting-residue/atom selection. This is a reviewable visualization of existing coordinates, not a verified core-only fit. One selected AF3 model per sequence is shown, without ensemble uncertainty or statistical claims.

Bond criteria: intra-residue heavy-atom distances 0.8–1.9 Å; inter-residue O3′–P distance <2.1 Å. Base footprints are projected convex hulls of ring atoms. The whole-structure view intentionally has a different scale from the four close-ups.

Reproduce using render_cca.py with numpy, scipy, matplotlib and the nature-figure alignment auditor. PDF/SVG have editable text; PNG is 300 dpi, TIFF 600 dpi. residue_labels.csv and projection.json record exact selections and projection.
