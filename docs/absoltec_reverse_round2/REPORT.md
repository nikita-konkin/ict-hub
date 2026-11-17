# AbsolTEC reverse engineering — round 2

## Finding

The six confirmed double-to-single-to-double rounding sites in the original
linear solver do **not** explain the dominant restored/original mismatch on
the five available AKSU days. Removing those instructions in an isolated copy
does not make the restored results pass strict compatibility.

This is a causal binary experiment, not a new decompilation or a production fix.
The original executable and native scientific implementation were unchanged.

## Procedure

Re-examined the saved REA/Ghidra pseudocode and x86 instruction export for
procedures at 0x401000 and 0x401d80. Confirmed six adjacent `cvtsd2ss` / `cvtss2sd`
pairs at 0x401304, 0x4015cd, 0x4018ef, 0x401b55, 0x4020b7, and 0x402364.
The pairs round a double to single precision and widen it back to double.

In a separate executable copy, replaced only these 48 instruction bytes with
NOPs. Exact expected instruction bytes were asserted before each replacement;
both executable hashes and file offsets are in [patch-manifest.json](patch-manifest.json).
Pivot-selection rounding and the original singularity test were deliberately
retained, so this is not a fully double-precision version of the original.

Ran the experimental executable and the unchanged native compatibility model
on identical GPS/GLONASS inputs for AKSU 2025/032–036, with 0.5-hour spacing,
10-degree elevation cutoff, and correction coefficient 0.97. Each original
process ran in a fresh isolated directory. Compared the probe outputs with
the previously saved unmodified-original outputs and with fresh native outputs.

## Interpretation

Strict parity remains failed on every complete day. Sparse day 034 remains
partial and cannot establish full-day agreement. The narrow intervention does
not remove the VTEC discrepancy or the DCB discrepancy. It therefore weakens
the earlier hypothesis that these solver stores are the dominant cause.
It does not rule out other precision effects, upstream single-precision values,
or errors in the recovered preprocessing rules.

The next useful experiment is to capture and compare intermediate leveled TEC,
geometry, weights, and assembled equations before solving. This can distinguish
an input-equation discrepancy from a remaining solver discrepancy. No empirical
offset or calibration was added to make the curves agree.

## Evidence and limits

- [Summary](summary.json): changes in printed coefficients and before/after VTEC statistics.
- [Probe validation](probe-validation.json): coverage and all seven coefficient/DCB checks.
- [Run manifest](probe-manifest.json): source identities, parameters, and versions.
- [Evidence archive](probe-evidence.zip): outputs, logs, and per-case manifests; executable and raw inputs omitted.

All five probe processes completed. The original executable's SHA-256 was
rechecked after the experiment. Numerical unit tests were not rerun because no
scientific implementation was changed; the probe validation labels them
`not_run`. This experiment neither certifies the modified executable nor proves
physical accuracy. Input-header adaptation, one-station coverage, missing
neighboring-day context, and three-decimal legacy output remain limitations.
