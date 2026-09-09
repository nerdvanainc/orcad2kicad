# How orcad2kicad Works

A developer-facing overview of the architecture, coordinate math, verification model, and module
layout. For end-user instructions, see `Quick_Start.en.md` and `User_Manual.en.md`. This document
is derived from the project's internal design notes and the module docstrings under
`src/orcad2kicad/`.

## What this tool does

orcad2kicad converts a schematic drawn in OrCAD Capture into a KiCad project
(`.kicad_sym`/`.kicad_sch`/`.kicad_pro`, and optionally `.kicad_pcb`), and verifies that the
conversion preserved net connectivity exactly. Conversion and verification are entirely
deterministic — the same input always produces the same output, and the whole pipeline works with
no API key and no internet connection. An optional AI backend can propose fixes for ambiguous
cases (pin electrical types, footprint mapping, board/schematic mismatches), but it only ever
*suggests*; a human always decides what gets applied.

There are two independent entry points for reading the source design, converging on the same
downstream pipeline:

1. **EDIF 2.0.0** exported from OrCAD Capture (`File → Export Design...`). A hand-written
   deterministic reader parses this directly, which is what enables the two strongest verification
   stages ([1] and [2] below) — no other path can do this, because no other path has an independent
   second read of the original design geometry.
2. **OrCAD `.DSN`**, read natively by a KiCad *nightly* build (10.99+, an unreleased development
   branch) via `kicad-cli sch import --format orcad`. This requires no OrCAD Capture installation
   at all — just the `.DSN` file. Because the nightly importer is a black box to this project, the
   `.DSN` path skips [1]/[2] and relies on [3]/[4] only. A third variant, `--kicad-project`, takes
   an already-imported KiCad project directly, skipping the import step entirely.

Both paths converge on the same downstream steps: KiCad project output, `kicad-cli`-driven
verification/ERC/PDF, and (optionally) PADS board import and footprint linking.

## Architecture

```
                      ┌─────────────────────┐
   OrCAD Capture ───▶ │  EDIF 2.0.0 (.EDF)  │──▶ edif_reader.py ──▶ model.Design
                      └─────────────────────┘                          │
                                                                        ├──▶ verify.py    [1] EDIF join vs PADS netlist
                                                                        ├──▶ geometry.py  [2] geometry-derived nets vs PADS netlist
                                                                        │
   OrCAD .DSN ───▶ KiCad nightly (kicad-cli import) ──▶ kicad_sch_reader.py ──▶ SchematicView
                                                              │
                                                              └──▶ kicad_stable.py (restructure nightly
                                                                   format 20260830 → stable 20260306)
                                                                        │
             (either path) ──────────────────────────────────────────▶│
                                                                        ▼
                                                              kicad_writer.py (EDIF path)
                                                              or the restructured project (DSN path)
                                                                        │
                                                                        ▼
                                                        .kicad_sym / .kicad_sch / .kicad_pro
                                                                        │
                                                                        ▼
                                                        kicad_netlist.py → kicad-cli:
                                                          sch export netlist   [3] kicad-cli netlist vs PADS
                                                          sch erc               (ERC report)
                                                          sch export pdf        (schematic PDF)
                                                                        │
                                          PADS board (.asc or .kicad_pcb) │
                                                     ▼                    ▼
                                            kicad_board.py: import, footprint
                                            library extraction, board rewrite  [4] board vs schematic
                                                                        │
                                                                        ▼
                                                        agents.py (optional AI suggestions,
                                                        applied only if a human selects them)
```

All three front ends — the tkinter GUI (`gui.py`), the CLI (`cli.py`), and the MCP server
(`mcp_server.py`) — are thin wrappers around one function: `pipeline.run_pipeline(PipelineOptions)
-> PipelineResult`. This is the single source of truth for what a conversion does; nothing in the
GUI or CLI re-implements pipeline logic.

## Coordinate system rules

This is the one piece of math that touches nearly every module, so it is worth stating precisely.

- **1 EDIF unit = 0.254 mm** (i.e. 10 mil). This comes directly from the EDIF header
  (`numberDefinition (scale 1 (e 254 -6) (unit DISTANCE))`) and is constant across the whole
  format.
- **EDIF is y-up**: increasing y goes up the page. Page content lives in the y ≤ 0 region (origin
  at the top-left of the page).
- **`.kicad_sch` (schematic) is y-down**: increasing y goes down the page, matching screen
  coordinates. Converting EDIF → schematic requires negating y: `y_mm = -y_edif * 0.254`.
- **`.kicad_sym` (symbol library) is y-up**, matching EDIF, so no sign flip is needed there:
  `y_mm = y_edif * 0.254`. This matters because a symbol's internal geometry (drawn once in the
  library) and its placement on a sheet (via `.kicad_sch`, y-down) use opposite y conventions —
  get this backwards and every symbol renders upside down relative to its pins.
- **x is unchanged** in both conversions: `x_mm = x_edif * 0.254`.
- **Pin pitch**: a standard 10-EDIF-unit pin pitch becomes exactly 2.54 mm, which lands precisely
  on KiCad's default grid — this is not a coincidence, it's why 0.254 mm was chosen as the unit in
  the first place (10 mil × 25.4 = 254, so 1 EDIF unit is exactly 1/100 inch).
- **Orientation (rotation/mirroring)**: EDIF instance transforms (`R0`/`R90`/`R180`/`R270`/`MX`/
  `MY`/`MXR90`/`MYR90`) are mapped to KiCad's `(at x y rot)` + `(mirror x|y)` representation via a
  small lookup table derived by matching rotation/mirror matrices (mirror applied before rotation
  in both systems). For power symbols specifically, KiCad requires the pin to sit at symbol-local
  (0,0), so the library graphic is built pre-shifted by the pin's offset, and the placed instance's
  world position is corrected by the same transformed offset so the final rendered position still
  matches the EDIF connection point.
- **Pin direction**: a KiCad pin's `(at x y angle)` encodes the *connection point* at (x,y) and the
  angle the pin body extends from it (0° = body to the right, 90° = up, 180° = left, 270° = down).
  This is derived from the EDIF pin's path (a vector from the connection dot to the body) plus the
  distance between those two points for pin length.

## The four verification stages

The core design goal is that a conversion bug should be *impossible to ship silently* — every stage
compares two independently derived views of net connectivity, expressed as REF.PIN sets (e.g.
`R1.2`), so a name mismatch never masks a real difference and a real difference never hides behind
a name coincidence.

| Stage | Compares | Needs kicad-cli? | Available on |
|---|---|---|---|
| [1] EDIF join vs. PADS | Nets built by directly joining the EDIF connectivity graph (pin-to-net edges) against the reference PADS2000 `.asc` netlist | No | EDIF input only |
| [2] Geometry vs. PADS | Nets derived independently from schematic geometry — wire endpoints, pin world coordinates, label positions — via union-find, against the same reference netlist | No | EDIF input only |
| [3] kicad-cli netlist vs. PADS | The netlist `kicad-cli sch export netlist` extracts from the *actual written* `.kicad_sch`, against the reference netlist | Yes | All input paths |
| [4] Board vs. schematic | Nets read from the PADS board (imported `.kicad_pcb`) against the schematic-side baseline (the PADS netlist if given, else the kicad-cli-extracted schematic netlist) | `.asc` boards: yes. `.kicad_pcb`: no | All input paths, only if `--board` given |

Why two independent stages ([1] and [2]) exist on the EDIF path instead of one: [1] validates that
the reader correctly interpreted EDIF's *explicit* connectivity data (the `joined` port references
inside each `net` form). [2] validates something orthogonal — that the *geometric* interpretation
(wire coordinates, orientation matrices, pin placement, junction inference) independently produces
the same answer. If [1] passes but [2] fails, the bug is in geometry handling even though the
connectivity graph is right; the converse points at the EDIF parser itself. Passing both, on a
design with hundreds of nets, is strong evidence the reader and the coordinate math are both
correct — deriving the same netlist from the connectivity list and from raw wire geometry
independently agreeing is a much stronger signal than either check alone.

[3] exists because [1] and [2] only prove the *intermediate model* is right — they say nothing
about whether `kicad_writer.py` serialized that model into a `.kicad_sch` KiCad itself parses back
into the same connectivity. Only [3] catches format-level writer bugs.

[4] is the only stage that touches the PCB side at all: it confirms the actual routed board (parts,
footprints, pin-to-pad mapping, net membership) agrees with the schematic, so "Update PCB from
Schematic" in KiCad won't silently drop or re-place anything.

Every stage reports each net as `PASS/OK`, `DIFF` (REF.PIN set differs — see `missing`/`extra`),
`ONLY_OURS` (present only in our output), or `ONLY_REF` (present only in the reference — the most
serious, meaning a connection is missing entirely). A stage's overall `RESULT: PASS` requires every
net in it to be OK.

## Module map

All modules live under `src/orcad2kicad/`.

| Module | Role |
|---|---|
| `sexp.py` | Minimal S-expression tokenizer/parser shared by both EDIF and KiCad file formats. |
| `model.py` | The intermediate representation: `Design`/`Page`/`Symbol`/`Pin`/`Instance`/`Wire`/`Label`/`PowerPort`/`OffPage`/`Text`/`Net` dataclasses. EDIF units throughout (1 unit = 10 mil = 0.254 mm), y-up. |
| `edif_reader.py` | Parses an OrCAD EDIF 2.0.0 export into a `model.Design`. |
| `geometry.py` | Orientation matrices, world-coordinate pin placement, wire-topology union-find — derives nets purely from drawing geometry (verification stage [2]). |
| `pads_netlist.py` | Parses a PADS2000 ASCII netlist (`.asc`, `*PADS2000*`/`*PART*`/`*NET*`/`*SIGNAL*` sections) into `{net_name: {REF.PIN, ...}}`. |
| `verify.py` | Union-find based net comparator and report generator shared by all four verification stages. |
| `kicad_writer.py` | `model.Design` → KiCad files (`.kicad_sym`, per-page `.kicad_sch`, root sheet, `.kicad_pro`), format version 20231120 baseline. EDIF input path only. |
| `kicad_netlist.py` | `kicad-cli` discovery (stable vs. nightly, version/OrCAD-import-support detection), netlist export/normalization, ERC, PDF — stage [3]. |
| `kicad_board.py` | PADS board import via `kicad-cli`, `.kicad_pcb` reading, footprint library extraction (`PADS.pretty`), board rewriting, and stage [4] comparison. Supports duck-typed `SchematicView` from either input path. |
| `kicad_sch_reader.py` | Reads an already-nightly-imported `.kicad_pro`/`.kicad_sch` project into a `SchematicView` (reference → pins/footprint/symbol path). Fills empty footprint fields with minimal, targeted text edits (not full re-serialization) to preserve everything else in the file byte-for-byte. Extracts embedded `lib_symbols` into a standalone `orcad_import.kicad_sym` + `sym-lib-table`. |
| `kicad_stable.py` | Restructures a KiCad-nightly importer's output (multiple top-level sheets, format e.g. `20260830`) into the root-sheet + sub-sheet hierarchy that stable KiCad 10.0 (format ceiling `20260306`) can open. Applied by default on the `.DSN`/`--kicad-project` paths; disable with `--nightly-format` to keep the raw nightly output. Edits are done as targeted string substitution on copied files, not tree re-serialization, to preserve the nightly importer's original formatting. |
| `kicad_portable.py` | Prepares a portable KiCad nightly build with no installer ever run: downloads the official nightly installer, extracts it (self-provisioning a 7-Zip console build if no archive tool is present), strips unneeded assets, and verifies `kicad-cli` runs and supports OrCAD import. |
| `pipeline.py` | The single orchestrator (`run_pipeline(PipelineOptions) -> PipelineResult`) that the GUI, CLI, and MCP server all call — the one place where EDIF/`.DSN`/`.DSN`-via-nightly reading, verification stages [1]–[4], KiCad output, board linking, and result formatting come together. |
| `agents.py` | AI backend abstraction (`none`/`api`/`claude-cli`/`codex-cli`) and suggestion generation. Suggestions are always advisory — applying one is a separate, explicit, human-triggered step. Uses only the standard library `urllib` for the `api` backend (no SDK dependency). |
| `explain.py` | Turns a `PipelineResult` into a plain-language summary (what was checked, what ERC violation types mean and whether they need action, which KiCad build to open the result with). |
| `gui.py` | The tkinter front end. All actual conversion work happens on a worker thread; the Tk thread only ever reads/writes the result via a `queue.Queue`, polled by `after(100, ...)`. Table logic is factored into plain functions (`diff_rows`, `resolutions_from_rows`, `verification_rows`) so it's testable without a window. |
| `cli.py` | Argument parsing → `PipelineOptions` → `run_pipeline` → text output. Exit codes: 0 = all verification passed (or none requested), 1 = a verification stage failed, 2 = I/O or `kicad-cli` execution error. |
| `mcp_server.py` | A stdio, JSON-RPC 2.0 MCP server implemented with no SDK — one JSON message per line on stdin/stdout, all logging to stderr. Exposes `convert`/`verify`/`board_diff`/`list_issues`/`read_file`/`find_kicad_cli`/`prepare_kicad_nightly`/`suggest` as tools. |
| `branding.py` | The single place product/company name strings are defined, shared by CLI/GUI/MCP/doc generation. |

## Running the tests

```bash
O2K_SKIP_BUILD=1 python -m unittest discover -s tests -t .
```
(PowerShell: `$env:O2K_SKIP_BUILD='1'; python -m unittest discover -s tests -t .`)

`O2K_SKIP_BUILD=1` skips `test_packaging.py`'s real PyInstaller build (which otherwise takes
several minutes) — leave it out only when you specifically want to verify the packaged exe itself.

Tests that depend on external tools are skipped automatically when those tools aren't present on
the machine running them: `kicad-cli`, the `claude` CLI, and so on. To also exercise the
nightly-`kicad-cli`-dependent tests (the `.DSN` import path, the format-20260830 schematic
handling), point `O2K_KICAD_NIGHTLY` at a nightly `kicad-cli` executable:

```bash
O2K_SKIP_BUILD=1 O2K_KICAD_NIGHTLY=<path-to-nightly-kicad-cli> python -m unittest discover -s tests -t .
```

Most of the test suite is regression testing against a small set of sample inputs checked into
`samples/`: a 9-page reference board (134 nets, 187 parts). Tests that need `samples/` skip cleanly
if that directory is absent (for example, in a checkout that intentionally excludes it) — the
sample files ship in this repository, but if you fork the project and remove them, expect the
sample-dependent tests to skip rather than fail.

## Contributing

Issues and pull requests are welcome via the project's GitHub repository.

A hard rule for anyone contributing: **never commit real customer or project schematic/board data**
into this repository, its tests, or its documentation. The `samples/` directory ships with one
sanitized reference design used for regression testing; new test fixtures should follow the same
pattern — synthetic or explicitly-cleared-for-publication data only. If you're debugging an issue
against your own design, keep those files local and describe the problem in the issue instead of
attaching the design.

The deterministic pipeline is expected to stay deterministic: given the same input files and the
same options, `run_pipeline` should always produce byte-identical output. If you're adding a
feature that depends on something non-deterministic (timestamps, random IDs, iteration order over
an unordered collection), find a way to make it stable before merging — this property is what makes
the four-stage verification model meaningful in the first place.
