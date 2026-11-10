# Changelog

한국어: [CHANGELOG.md](CHANGELOG.md) · English (this page)

This file records the changes in each orcad2kicad release.

## 1.0.8 (2026-11-11)

Fixes the GUI keeping the old output folder and project name when the input file changes.

- **Fields follow a new input file**: dropping or picking another design from the same folder left the output folder and
  project name of the previous design. Fields that still hold the values auto-filled from the previous file (output folder,
  project name, netlist, board) now follow the new file, and values you edited are kept. Values restored from saved settings
  are judged the same way.

## 1.0.7 (2026-11-04)

Fixes bottom-only pads lost by the PADS importer and PADS unit handling.

- **`.pcb` board message**: giving a PADS binary `.pcb` to `--board` ended with an exception. It now ends with exit code 2 and
  a message to export ASCII in PADS.
- **PADS unit handling fixes**: `INCHES` files were not recognized, which left the importer's 76.2 mm mask clearance in place.
  CAM `DOC_PAD_OVERSIZE` is in internal units in every file, but it was converted with the file unit, so the advice for METRIC
  files came out as hundreds of thousands of mm. Decal values now use the decal's own unit in files other than BASIC.
- **PADS bottom-only pad restoration** (`--board .asc`, on by default, `--keep-board-pads` turns it off): SMD pads whose pad
  stack has top size 0 and bottom size > 0 (for example hot-swap sockets soldered on the back) were created by the KiCad PADS
  importer as 0.001 mm pads on the part side. They now get their real layer and size from the per-pin pad stacks in the `.asc`
  (applies to both the output `.kicad_pcb` and `PADS.pretty`). Non-plated holes and drilled pads that import at the same size
  are left alone. `kicad_board.pads_bottom_only_pins` / `fix_bottom_only_pads`, `result.board_pads`, a `PCB:` entry in the
  automatic changes list. Measured: DRC unconnected items on two boards went from 5 to 0 and from 2 to 0; no pad was changed
  by mistake on 31 private production PADS boards.

## 1.0.6 (2026-10-29)

When a PADS board is supplied, the solder mask that the KiCad PADS importer transfers incorrectly is corrected from the CAM settings in the PADS file.

- **PADS board solder mask cleanup** (`--board .asc`, on by default, `--keep-board-masks` turns it off): fixes three KiCad
  PADS importer defects right after import (applies to both the output `.kicad_pcb` and `PADS.pretty`; pad sizes, holes,
  routing and nets are unchanged).
  (1) The importer does not read the CAM "include pads in solder mask" setting, so through-hole pad mask openings were
  missing or missing on the back side only. `F.Mask`/`B.Mask` are restored from the CAM solder mask document in the `.asc`
  (vias get an opening only on sides where the CAM document draws copper vias). (2) The importer writes the number 3 into
  the board clearance `pad_to_mask_clearance` in file units (METRIC 3 mm, BASIC 0.000002 mm); this is now set to 0. The
  PADS CAM pad oversize is not applied and is reported in the log and `auto_changes.txt` (as a per-side value; applying it
  can remove the mask between fine-pitch IC pins). `kicad_board.pads_mask_cam` / `restore_pad_mask_openings` /
  `fix_importer_mask_clearance` / `mask_clearance_from_cam`, `result.board_mask` (`cam_clearance`), and a `PCB:` entry in the
  automatic change list (the EDIF input path now reports automatic changes too). Measured: all 31 private production PADS
  boards were affected (14 METRIC boards had the 3 mm clearance), and opening positions matched the PADS fabrication Gerbers
  90/90. (3) The importer sends UNASSIGNED PADS layers 25/28 to F.Mask/B.Mask, which turned decal documentation drawings
  into mask openings. When the CAM mask document does not draw that layer, only drawings whose coordinates and line width
  match a decal piece are moved to `Cmts.User` (`move_spurious_mask_graphics`, 10 in the sample; KiCad DRC mask violations
  199 → 0).
  When a `.kicad_pcb` is given directly, nothing is changed and only the number of pads without openings is reported as a
  warning.

## 1.0.5 (2026-10-29)

Fixes a connectivity error in `.DSN` import results (nets merged through a bus), explains the remaining hidden
(transparent) labels and lets you choose how to handle them, and reports everything the tool changes automatically.

- **Footprint fields stay empty when only `--netlist` is given without a board** (`.DSN`/KiCad project input): previously
  the netlist's footprint names were filled in without a library (`0603`), producing one KiCad ERC `footprint_link_issues`
  per part. They are now left empty and reported with one summary issue line
  (`footprints left empty (no board): N refs ...`) and in the result summary (`result.footprint_names_unset`). With a board
  (`--board`) they are filled as `PADS:<decal>` as before.
- **Splitting nets merged by junctions on a bus (changes connectivity)**: where a wire touches a bus directly without a bus
  entry, the nightly importer places a junction, and KiCad then merges the wires touching that bus into one net. Those
  junctions are removed so the nets are split as they were (`kicad_cleanup.remove_bus_wire_junctions` /
  `split_bus_wire_junctions_guarded`). Bus branch junctions are kept. The change is kept only if the netlist **only splits**,
  and is reverted otherwise. Because connectivity changes, it is reported first in `AUTO CHANGES`, with
  `WARNING net '...' split into ...` for each split net (included in issues), in `[나뉜 넷]` in `auto_changes.txt`, and in the
  result summary. `result.import_cleanup['bus_split']`. Use `--keep-bus-junctions` to turn it off.
  Wire ends touching a bus without an entry are then redrawn the KiCad way (wire shortened by 2.54 mm + 45-degree
  `bus_entry`) (`draw_bus_entries`, only when netlist names and pins are identical; this removes the ERC
  `unconnected_wire_endpoint` that appeared after the junctions were deleted). Measured: occurred in 2 of 113 private
  production designs; after removal both matched the PADS reference netlist 100% (before, 7 to 17 nets were merged into
  one), and the other 111 did not change.
- **Wider hidden label deduplication**: transparent global labels sitting directly on a same-named power symbol pin without
  a wire, and transparent **local** labels with a same-named local label on the same wire, are now removed as duplicates
  (checked against netlist names and pins). The labels that remain are the ones that carry a real connection.
- **`--hidden-labels {keep,show,shrink}`** (default `keep`, compatible with the earlier `keep`/`shrink`): `show` turns the
  remaining labels into visible global labels (an OrCAD alias label on the same wire becomes a global label, and one in the
  middle of a wire moves to a free wire end; ERC `same_local_global_label` and `unconnected_wire_endpoint` drop accordingly).
  `shrink` reduces the text to 0.01 mm to hide it in PDF/SVG as well. Both compare net names and pins in the netlist and
  revert on any difference.
- **Remaining label report**: the log shows `hidden labels: N left - ...` with name, page, kind
  (`alias`/`renamed`/`wire`/`free`/`local`), reason and handling result, and `result.hidden_labels` (including JSON/MCP)
  holds the same data.
- **GUI**: the checkbox became a three-way choice ("Keep as is"/"Make visible"/"Hide in PDF too"). The blue description line
  adds what the chosen mode looks like (`.DSN`/KiCad project input only), and the result summary shows the remaining count,
  five names, the reason, the handling result, and alternatives when `keep` is selected. Old saved settings are still read.
- **MCP**: `hidden_labels` in `convert` accepts `keep`/`show`/`shrink`.
- **Bus bug fix**: moving labels to wire ends mistook a bus end for a free wire end and moved net labels onto the bus, which
  merged the bus member nets. Fixed (occurred in 2 of 113 private production designs measured, 0 after the fix).
- **Netlist-checked cleanup (`cleanup_project_guarded`)**: each cleanup step for `.DSN`/KiCad project input compares the
  kicad-cli netlist (net names + pins). If it differs, cleanup falls back in this order: cleanup without moving labels to
  wire ends, safe steps only, revert everything. Reported through `result.import_cleanup['guard']`
  (`level`/`verified`/`tried`), the prefix of the `import cleanup:` log line, and a plain explanation in the result summary.
- **H1 hidden power labels to power symbols**: hidden power-name labels sitting directly on part pins (for example GND) are
  replaced by a power symbol of the same name (netlist-checked). `result.hidden_labels['power_symbols_placed']`, log
  `hidden labels: N hidden power labels sitting on part pins replaced by power symbols`.
- **H2 hidden label name conflict warning**: when a hidden label shares a wire with a power symbol or visible global label of
  another name, KiCad merges the two nets and ERC shows only a `multiple_net_names` warning. This is now reported through
  the log `WARNING hidden label name conflicts`, `result.hidden_labels['conflicts']`, the warning area of the result summary
  (conclusion "items to check") and the GUI issues tab. It can also be used to re-check a project you edited by running it
  through `--kicad-project`.
- **Label tidy**: EDIF output moves alias labels to free wire ends (log `label tidy:`). Both paths also tidy labels on the
  pin-side end of a continuing wire, and off-page global labels 0.254 mm short of a wire end are snapped to the end
  (`import_cleanup['globals_snapped']`).
- **`--duplicate-alias-labels {keep,remove}`** (default `keep`): removes local alias labels with the same name as a visible
  global label on the same wire (ERC `same_local_global_label` disappears, and one text item per label goes away from the
  drawing; netlist-checked). `result.duplicate_alias_removed`. GUI checkbox "Remove duplicate alias labels", MCP `convert`
  option `duplicate_alias_labels`.
- **Hidden local label removal**: in net name mode `kicad`, hidden *local* labels are removed too (checked by pin grouping).
  The kind `local` in the remaining list means "the only hidden local label giving that wire its in-sheet name".
- Log wording change: `hidden global labels removed` -> `importer hidden labels removed` (`net names: kicad - N ...`).
- **Documentation**: added the troubleshooting entry "If a power symbol's net name looks wrong / two power nets are merged"
  and descriptions of the features above to the user manual (Korean and English).
- **Hidden labels no longer cover wires in PDF** (default stays `keep`): hidden labels that are invisible in KiCad but print
  black in PDF/SVG are turned away from the wire or moved to a free end of the same wire (netlist-checked).
  `import_cleanup['hidden_oriented']`.
- **Second deduplication pass after net name cleanup**: duplicate hidden labels protected by their names in the first pass
  are removed in a second pass after net name cleanup (netlist-checked). Log
  `hidden labels: N more redundant hidden labels removed after net-name cleanup ...`.
- **`--hidden-labels show` appearance**: labels whose text was invisible in OrCAD become small light gray global labels
  (`--hidden-label-style subtle`, default; `normal` is the regular look, CLI only), and OrCAD alias text that was visible
  becomes a regular label. Revealed labels face away from the wire and text size is limited to 1.27 mm so that neighbors at
  2.54 mm spacing do not overlap.
- **Labels sitting directly on part pins without a wire**: text with no justify was centered on the pin end. Rotation and
  justification now face away from the part body (the connection point does not move). `import_cleanup['pin_labels_faced']`.
- **Pin number text fit**: pin numbers longer than the pin (the importer copies 1.7 mm text as is) overflowed onto pin names
  and labels. They are reduced to 70% of the pin length (minimum 0.6 mm). Both the sheet's embedded `lib_symbols` and the
  `.kicad_sym` library are changed so that ERC `lib_symbol_mismatch` does not appear (KiCad cannot move the number position
  per pin, so only the size can be adjusted). `import_cleanup['pin_numbers_fitted']`.
- **Wider off-page connector recognition**: single-line chevrons (2.54 mm stem + two diagonal pieces), labels with a wrong
  importer angle or angle 0 on vertical connectors (own angle first, then other directions), and vertical connectors with
  centered horizontal text are now recognized and turned into global labels. If the connector text **differs** from the
  hidden label (the real net) name (OrCAD gave one net two connector names), converting would split the net, so it is **not
  converted** and stays a drawing. This is reported with `WARNING off-page connector '...' (...) left as a drawing: its net
  is '...'` in the log, issue list, result summary and `auto_changes.txt` (`import_cleanup['offpage_unconverted']`).
- **Automatic change report**: everything the tool changes in the schematic is reported, through the log `AUTO CHANGES:`
  block (one `  - N ...` line per item), `auto_changes.txt` in the output folder (UTF-8 BOM; items, counts, what to check, a
  list of renamed nets as `old -> new`, connectors left as drawings), an "Automatic changes" area right below the input line
  of the result summary, a closing sentence on the conclusion line, and JSON/MCP `auto_changes`.
  The 9 items for the public Raspberry Pi CMIO V3 design (default options, development build): 108 off-page connectors ->
  global labels, 285 crossing cuts merged, 117 labels moved to free wire ends, 235 hidden labels turned away from wires,
  1,071 duplicate hidden labels removed, 941 wires set to default width, blank drawing sheet, 152 importer hidden global
  labels removed (net name mode `kicad`), 84 nets renamed.
  ERC 68 (`power_pin_not_driven` 14, `same_local_global_label` 54), 4 hidden labels remaining (`CD0_SCL`, `CD0_SDA`,
  `CD1_SCL`, `CD1_SDA`, kind `alias`).

## 1.0.4 (2026-10-28)

Stronger cleanup of `.DSN` import results (off-page connectors, wire width, black hidden labels in PDF), Korean text display, a net name policy change, and GUI options.

- **Net name default behavior changed** (the previous behavior is available as an option):
  - `--net-names` now defaults to `kicad` with or without a board. OrCAD page aliases become local labels (KiCad net name
    `/USB/DTR#`) and unnamed nets get KiCad automatic names. Aliases and automatic nets are no longer turned into global
    labels. This also works for `.DSN`/KiCad project input, removing the transparent global labels the importer added (net
    pin grouping is checked against the kicad-cli netlist, nets that would lose their name are protected, and a failure
    reverts the change). The previous behavior is `--net-names keep`.
  - `--board-net-names schematic` (default): with a board, net names in the output board are aligned with the schematic so
    that Update PCB does not rename nets (the match count is reported in `[4b]`). Use `--board-net-names pads` to keep the
    PADS names.
  - `--hidden-labels shrink`: hides remaining transparent labels that would print black in PDF/SVG by shrinking the text
    (default `keep`).
  - GUI: added the options "Net names" (KiCad style / keep PADS and OrCAD names), "Board net names" (match schematic / keep
    PADS), "Hide hidden labels in PDF" and "Korean font", plus a description line showing the result of the selected
    combination (example net names, and whether they match when PADS is imported separately). The result summary shows the
    number and examples of changed net names and the board/schematic net name match count. MCP `convert` has the same
    options.
- Korean text that was invisible or broken:
  - EDIF and `.DSN`: text, labels, properties and pin names containing Korean get a Korean font (default `Malgun Gothic`).
    The KiCad default font does not draw Korean, and Latin fonts such as `Arial` draw boxes. Change it with
    `--korean-font NAME` and turn it off with `--korean-font ""`.
  - `.DSN`: strings broken by the importer reading CP949 Korean as Latin-1 (`´Â Ä¿³ØÅÍ`) are restored (valid Latin-1 strings
    are left alone).
- Three additions to importer result cleanup for `.DSN`/KiCad project input (on by default):
  - Off-page connectors to KiCad global labels: the importer turned OrCAD off-page connectors into a red chevron drawing,
    red text and a transparent global label, so the net was global but only the drawing and text showed on screen. The
    transparent label is made visible and the drawing and text are removed (net connections do not change).
  - Removal of duplicate transparent labels that printed black in PDF/SVG: the KiCad netlist before and after removal is
    compared down to net names and pins; on a difference that name is kept, and if it still differs the change is reverted.
  - Wire width to the KiCad default: the importer wrote a 0.254 mm width on every wire, so they looked thicker than newly
    drawn wires. They are set to the default width (0). Use `--keep-wire-width` to keep the original width.

## 1.0.3 (2026-10-20)

Bug fixes found while converting real designs (EDIF, `.DSN`, PADS boards), and GUI drag and drop.

- Bug fix: stable KiCad 10.0 could not open the `.kicad_pcb` made from EDIF input. When a portable nightly had been prepared
  in the GUI and the kicad-cli field pointed to it, the board was imported with the nightly too and became
  `(version 20260831)`. Boards are now imported with stable kicad-cli regardless of the input type.
- Bug fix: some nets were broken in the schematic with EDIF input. Alias labels of nets with special characters in their
  names, such as `RTS#` and `DTR#`, and aliases with a display name different from the net name (`NET_NAME_OVERRIDE`, for
  example `nRST` on net `ETH_NRST`) could not be read, so the labels were missing. Both forms now become labels.
- Bug fix: `.DSN` conversion failed with "kicad-cli import failed" when the project name contained a dot (for example
  `ABC_1.3`).
- GUI: dragging several files or a folder from Explorer onto the window fills the matching input fields by extension (the
  header for `.asc`) (Windows). For a folder only the files directly inside are used, and if both EDIF and `.DSN` are present,
  EDIF mode is used.

## 1.0.2 (2026-09-11)

- The reference netlist (`--netlist`, GUI "PADS netlist (.asc) or IPC-D-356", `netlist` in MCP `convert`, `verify` and
  `board_diff`) now accepts IPC-D-356/IPC-D-356A netlists exported from Cadence Allegro and similar tools
  (`.ipc`/`.356`/`.d356`, also detected from content when the extension is missing or different) as well as PADS2000 ASCII
  `.asc` (`src/orcad2kicad/ipc356.py`, `pads_netlist.load_reference_netlist`). [3] verification and other existing consumers
  work without changes. Confirmed by measurement (Raspberry Pi CMIO board, IPC-D-356 with 152 multi-pin nets).

## 1.0.1 (2026-09-11)

Cleanup of nightly OrCAD importer defects, GUI usability improvements, and a fix for converting again into the same folder.

- Importer defect post-processing (`kicad_cleanup`, on by default, `--no-import-cleanup` turns it off): for `.DSN`/`.kicad_pro`
  input, the "hop gaps" where the nightly importer cuts wires at each crossing and fills them with graphic lines are merged
  into a single wire (netlist identical before and after; ERC `unconnected_wire_endpoint` and `endpoint_off_grid` greatly
  reduced). The overlap of the OrCAD title block and the KiCad default drawing sheet is resolved with a blank drawing sheet
  (`blank.kicad_wks`). Net alias labels sitting in the middle of wires are moved to free wire ends and rotated to match
  (`snap_labels_to_wire_ends`), which brought the `unconnected_wire_endpoint` left after hop gap merging down to 0 in the
  measured project.
- Bug fix: converting a `.DSN` again into the same output folder left the first page file of the previous run, so
  reconstruction to the stable format was skipped (the root stayed in nightly format and KiCad 10.0 could not open it) and the
  first page kept its old content. The format is now judged by the highest version among the sheets, and previous sheet files
  are deleted before re-importing.
- GUI: added "Auto (system language)" to the language selection (default; the OS locale is detected at each start and the
  result is not saved), per-type log line colors, tab highlighting and item counts, emphasized status text with per-status
  colors, an "items to check" status even when the exit code is 0, a detail window for the selected row in the board
  difference tab, a font and spacing theme (Malgun Gothic/Segoe UI 10pt), and app and exe icons.
- Removed the company contact line at the end of the result summary.
- Verification labels: "vs PADS" in the log, summary and verification tab became "vs reference netlist (format)" so that it is
  also correct for an IPC-D-356 reference (`PipelineResult.ref_format`, JSON `ref_format`).
- Results tab: the summary uses the remaining space, has a scrollbar and is aligned so that the conclusion line is visible.
  When all ERC items are informational they are summarized in one line instead of per-type lines. Default window height 960.
- Manual: added a note to open results through `.kicad_pro` (opening a sub-sheet file alone overlays the KiCad default drawing
  sheet).


## 1.0.0 (2026-09-09)

First public release. The source code is published on GitHub under the MIT license, together with a single-file exe release
for Windows.

- Source release: published on GitHub (`github.com/nerdvanainc/orcad2kicad`) under the MIT license, alongside Windows exe
  releases
- Input modes: EDIF 2.0.0 export, native OrCAD `.DSN`, or a `.kicad_pro` project already imported into KiCad
- Deterministic EDIF → KiCad conversion pipeline that writes `.kicad_sym`, `.kicad_sch` and `.kicad_pro` directly
- Verification against a PADS `.asc` reference netlist: three independent checks (connectivity join, wire geometry, and
  comparison with the netlist exported by kicad-cli)
- PADS board link: footprint library extraction, board vs schematic reference/pin comparison, and a fourth verification pass
- Portable KiCad nightly preparation: downloads and extracts only, without running an installer, to set up an environment
  that can read `.DSN` files without OrCAD
- Automatic reconstruction of nightly-imported projects into the stable format, so they open in stable KiCad 10.0
- GUI (tkinter) with verification results, issues, board differences and agent suggestions tabs
- CLI for scripts and automation
- MCP server (stdio, JSON-RPC) so that AI coding tools such as Claude Code can call conversion and verification as tools
- Optional AI backends (Claude API / claude-cli / codex-cli) for pin type guessing, net difference analysis and review
  suggestions. They only make suggestions; nothing is applied automatically, and the user reviews and decides in the GUI
- Windows single-file exe packaging (a version with the GUI and a CLI-only version)
- GUI in Korean and English (i18n), English user documents (`docs/Quick_Start.en.md`, `docs/User_Manual.en.md`), and a
  developer overview (`docs/HOW_IT_WORKS.md`)
