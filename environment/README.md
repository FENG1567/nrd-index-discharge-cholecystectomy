# Environment notes

`requirements.txt` pins a practical Python environment for the cohort, statistical, and aggregate-figure pathways. The original licensed analysis environment may use a different operating system, compiler, BLAS implementation, or Matplotlib build. Small last-digit differences in floating-point summaries are possible, but the package fixes the scientific constants, random seeds, estimands, exclusions, bootstrap design, and suppression rule.

The public figure builder uses Matplotlib and may render fonts differently from the original server. The reference PDF/PNG/SVG files under `outputs/figures/` are retained for exact visual comparison.

