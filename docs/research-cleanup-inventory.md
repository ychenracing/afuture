# Research cleanup inventory

Final review removes only experiments that failed their economic promotion gate and were never wired into production behavior.

Removed experimental families:

- 60-minute BU/FU + PP/V intraday research (`0/24` pre-OOS profiles passed);
- structural multi-leg research (soybean crush, steel/coke margin and related structures did not provide a stable return level sufficient to justify three-leg production complexity).

Their evidence and rejection rationale are preserved in `docs/research-final-evidence.md`. Production code, risk permissions, account/order/fill semantics and the validated corrected M/OI / broad L3 / specific-contract daily pair research chain are not deleted by this cleanup.
