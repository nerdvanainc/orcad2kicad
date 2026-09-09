# orcad2kicad User Manual

Provided by Nerdvana Inc. — https://www.nerdvana.co.kr · Downloads/docs: https://github.com/nerdvanainc/orcad2kicad

A tool that converts schematics drawn in OrCAD Capture into a KiCad project
(`.kicad_sym`/`.kicad_sch`/`.kicad_pro`, and `.kicad_pcb` if needed). The input can be any one of:
the OrCAD `.DSN` file itself (recommended, no OrCAD installation required, Chapter 0), the EDIF
2.0.0 export produced by OrCAD Capture (alternative path, Chapters 1–13), or a project that has
already been imported into KiCad. This document is written so that an engineer who is completely
new to the tool can follow it alone, from installation through checking the results in KiCad. This
document focuses on "how to use it" — the distribution package bundles this document together with
`Quick_Start.md`, `README.md`, `LICENSE`, and `THIRD_PARTY_NOTICES.md`.

## Table of Contents

- [Quick Start (essentials only)](#quick-start-essentials-only)
0. [Converting Without OrCAD (recommended path)](#0-converting-without-orcad-recommended-path)
1. [Overview](#1-overview)
2. [Requirements](#2-requirements)
3. [Installation](#3-installation)
4. [Preparing Input Files](#4-preparing-input-files)
5. [Using the GUI](#5-using-the-gui)
6. [Using the CLI](#6-using-the-cli)
7. [Reading Verification Results](#7-reading-verification-results)
8. [AI Backend Setup](#8-ai-backend-setup)
9. [Registering the MCP Server](#9-registering-the-mcp-server)
10. [Finishing Up in KiCad](#10-finishing-up-in-kicad)
11. [Troubleshooting](#11-troubleshooting)
12. [Known Limitations](#12-known-limitations)
13. [Appendix](#13-appendix)

## Quick Start (essentials only)

The shortest path from a single OrCAD `.DSN` to a fully verified stable KiCad 10.0 project. See the
chapters below for details.

**1. Setup (once)**
- A stable KiCad 10.0 installation is all you need (all editing/verification is done with the
  stable release).
- Run `dist\orcad2kicad.exe` (no installation needed). From source: `python -m orcad2kicad`.
- In the input frame, click **[Prepare KiCad nightly portable (no install)]** once — the importer
  that reads `.DSN` files still exists only in the nightly build. It downloads and extracts the
  archive only (about 300 MB); no installer is ever run. After this, it is auto-detected.

**2. Three input files (place them in the same folder and they auto-fill)**
| File | Required | Where from | Purpose |
|---|---|---|---|
| `name.DSN` | Required | OrCAD project | Original schematic |
| `name.asc` (PADS2000 netlist) | Recommended | OrCAD Create Netlist → PADS | Verification ground truth ([3]). Without it, connectivity cannot be verified |
| Routed PADS Layout ASCII `.asc` | Recommended | PADS Layout File → Export ASCII (all sections) | Footprint library (`PADS.pretty`), board linking, [4] comparison |

**3. Run the GUI**
1. Choose **OrCAD .DSN** as the input type and locate the `.DSN` file. The output folder
   (`<name>_kicad`), project name, and the netlist/board from the same folder are auto-filled into
   any empty fields.
2. **[Run conversion]**. If the status reads `Done (all verification PASS)`, you're finished. If it
   reads `Verification FAIL present`, check the net table in the Verification tab.
3. Read the **"Summary explanation"** in the Result tab — it explains in one place what each
   verification step checked, whether the ERC items need action, and the conclusion.
4. Open with **[Open KiCad project]** in stable KiCad 10.0. The schematic has a root sheet plus a
   sub-sheet per page.

The same, from the CLI:
```
orcad2kicad-cli.exe --dsn name.DSN --netlist name.asc --board board.asc --explain
```
(The output folder defaults to `name_kicad` next to the `.DSN`; override with `-o`.)

**4. Reading the results (summary)**
- `[3] … RESULT: PASS` — the KiCad schematic's connectivity matches the OrCAD netlist 100%. This
  is the key verdict.
- `[4] refs only on board: []`, `BOARD NETS: PASS` — the board and schematic have matching parts
  and nets. If there is a difference, resolve it item by item in the "Board Diff" tab (e.g. add a
  placeholder symbol in the schematic for a part that only exists on the board, or ignore it).
- Hundreds of ERC violations are usually just importer quirks or KiCad conventions (off-grid
  points, no unconnected-flag markers, missing PWR_FLAG) and unrelated to connectivity. The
  summary explanation labels each type as "informational / worth checking / action needed."

**5. Finishing up in KiCad**
- In Pcbnew, run **Update PCB from Schematic (F8)** to link the board and schematic. Footprints
  are linked to the board library (`PADS.pretty`) as `PADS:<decal>`, so parts are not re-placed.
- After that, edit the schematic and repeat F8 as needed. The nightly build is no longer needed.

---

## 0. Converting Without OrCAD (recommended path)

Around July 2026, a **native OrCAD `.DSN` importer** was merged into the KiCad codebase
(`kicad-cli sch import --format orcad`, and `kicad-cli import` for turning a `.DSN` plus a PADS
board into a single project). Thanks to this, you can start a conversion from a single `.DSN` file
**without OrCAD Capture installed at all** — if you're planning to retire OrCAD soon, this path is
now the recommended one, ahead of the "EDIF export" path covered in Chapters 1–13. The EDIF path
(Chapters 1–13) is still fully supported, and if OrCAD is installed and you only use KiCad 8–10
stable releases, you can keep using EDIF exactly as before.

### Why a "nightly" build is needed

This importer is **not in a stable release yet** — it has only been merged into the development
branch (nightly, version `10.99.x`), and it is expected to land in a stable release starting with
**KiCad 11** (as of this writing, KiCad 10.0.6 is the latest stable release). So `--dsn` (reading
the OrCAD `.DSN` file itself) **requires a nightly `kicad-cli`**.

**However, the result afterward is saved in the stable KiCad 10.0 format.** The output the nightly
importer produces (multiple top-level sheets, format `20260830`, etc. — a shape stable releases
cannot open) is automatically restructured by this tool into a root sheet + sub-sheet hierarchy
(format `20260306`) (the default behavior; can be turned off with `--nightly-format`) — so
**editing, [3] verification, ERC, and PDF output all work with nothing but stable KiCad
10.0/kicad-cli.** Board (`.asc`) import also uses the stable kicad-cli when available (to avoid
producing a `.kicad_pcb` version the stable release can't open). In other words, the nightly build
is only needed **for the moment the `.DSN` is first read**, and `--kicad-project` (using a project
already imported into KiCad as input) may not need the nightly build at all (if the input is
already in the stable format, the restructuring step is skipped too).

### Preparing the nightly build — automatic (recommended) / manual / full install

> **This tool never runs an installer.** It downloads the nightly installer (an NSIS
> self-extracting executable) and **only extracts the archive**. Nothing is left behind in the
> registry, Start Menu, or file associations, and it never mixes with the stable KiCad 10 you're
> already using.
>
> **It's fine even on a PC without an archive tool (7-Zip/Bandizip).** On such a PC, it fetches the
> 7-Zip console edition from the official site and **also just extracts it** (about 6 MB, under
> `<install location>\tools\7zip\`). The 7-Zip installer itself is never run either.

**1) Automatic (recommended) — a single button/flag does it all**

- GUI: the **[Prepare KiCad nightly portable (no install)]** button in the "Input" frame. Click
  Continue in the confirmation dialog to download the latest nightly, extract it, then the
  "kicad-cli path" field above is filled in automatically and saved to settings. Progress shows in
  the "Log" tab.
- CLI:
  ```bash
  PYTHONPATH=src python -m orcad2kicad.cli --fetch-kicad-nightly
  ```
  The last line of output is `portable kicad-cli: <path> (10.99.0, orcad import: yes)`. To install
  it elsewhere, append a folder like `--fetch-kicad-nightly D:\kicad-nightly`.
- If you already have a downloaded installer (e.g. on a PC with no network access behind a
  corporate firewall), the download step is skipped:
  ```bash
  PYTHONPATH=src python -m orcad2kicad.cli --fetch-kicad-nightly \
    --kicad-nightly-installer D:\down\kicad-nightly-10.99.0.3703.gaa01e4fd3b-x86_64-lite.exe
  ```
- If you're using the MCP server: the tool `prepare_kicad_nightly` (arguments `root`/`installer`/
  `force`, all optional).

What happens under the hood:

| Step | Description |
|---|---|
| Listing | Reads https://downloads.kicad.org/kicad/windows/explore/nightlies and picks the **latest x86_64 lite** build (the one with the highest build number) |
| Download | Downloads the installer `.exe` (about **234 MB**) into `<install location>\.cache\` (received as `.part` then renamed, so no partially-downloaded file is left behind if interrupted) |
| Extractor | Uses the system's proper 7-Zip (`7z.exe`) or Bandizip if present. If not, it **self-provisions**: `7zr.exe` (about 590 KB, public domain) → used to extract the 7-Zip x64 distribution (about 1.6 MB) → yielding a proper `7z.exe` (+`7z.dll`, `License.txt`, about 5.9 MB). All of this is download + extraction only |
| Extraction | Uses the extractor above to **extract** the nightly installer (the installer itself is never run) |
| Cleanup | Deletes `share\kicad\demos` (about 350 MB), `share\kicad\internat` (about 49 MB), `$PLUGINSDIR`, and `uninstall.exe` → **725 MB → about 326 MB** (measured) |
| Verification | Actually runs `kicad-cli version` and `sch import --help` to confirm the version and OrCAD import support, and records this in `portable.json` |

- **Install location (default)**: `%LOCALAPPDATA%\orcad2kicad\kicad-nightly`
  (typically `C:\Users\<user>\AppData\Local\orcad2kicad\kicad-nightly`).
  Inside it: `bin\kicad-cli.exe`, the record file `portable.json`, and the downloaded installer
  under `.cache\*.exe`.
- **Disk usage**: about **330 MB** after cleanup (+ the 234 MB installer left in the cache — safe
  to delete. If the extractor was self-provisioned, `tools\` adds about 8 MB, of which 6 MB is the
  7-Zip that stays useful going forward).
- **Time required**: extraction takes about **6 seconds** (measured on NVMe). Most of the time is
  spent downloading.
- **Instant from the second run onward** — if it's already prepared, no network access happens and
  the path is simply returned.
- **Updating to the latest**: `--fetch-kicad-nightly --force-fetch` (in the GUI, delete it as
  described below and click the button again). This removes the old `bin`/`lib`/`share`/`etc` and
  extracts fresh.
- **Removal**: deleting the folder above is all that's needed (it was never "installed," so there's
  no uninstaller).
- **You don't need an archive tool.** If the system has a proper 7-Zip (`7z.exe`) or Bandizip
  (`bz.exe`), it uses that (auto-detected on PATH or at `C:\Program Files\7-Zip\7z.exe`,
  `C:\Program Files\Bandizip\bz.exe`; can be forced with `O2K_EXTRACTOR`), and **if not, it
  provisions the 7-Zip console edition itself**:

  1. Downloads `https://www.7-zip.org/a/7zr.exe` (about 590 KB, public domain). This standalone
     edition can't open NSIS archives, but it can extract 7-Zip's own distribution `.exe`.
  2. Downloads `https://www.7-zip.org/a/7zNNNN-x64.exe` (about 1.6 MB) and extracts it with
     `7zr x` → a proper `7z.exe` (+`7z.dll`, `License.txt`, about 5.9 MB) ends up in
     `<install location>\tools\7zip\`. **It is never run as a 7-Zip installer.**
  3. That `7z.exe` is used to extract the KiCad nightly installer.

  The provisioned 7-Zip is reused on subsequent runs. Source/size/license attribution is in
  `<install location>\tools\7zip\SOURCE.txt` and the adjacent `License.txt` (LGPL, including unRAR
  restrictions). If you'd rather not self-provision, use `--no-download-tools` (CLI) or the MCP
  argument `download_tools: false` — in that case, if no archive tool is present, it fails with
  guidance to "install 7-Zip/Bandizip, or extract manually as in 2) below." **In no case is it
  ever told to run an installer.** Windows only.
- **`7za.exe`/`7zr.exe` standalone editions can't open NSIS archives** (no error, just silently
  reports "Files: 0"). So even if only those are on PATH, they are not recognized as a system
  archive tool, and the self-provisioning procedure above kicks in.

**2) Manual (you have an archive tool but want to skip going through the tool's own logic)**

Download the same `.exe`, and **without installing it**, just extract it like an archive with
Bandizip or 7-Zip — `bin\kicad-cli.exe` comes out of it. Example:
```
"C:\Program Files\Bandizip\bz.exe" x kicad-nightly-10.99.0.xxxxxxx-x86_64-lite.exe -o:C:\kicad-nightly
7z x -y -oC:\kicad-nightly kicad-nightly-10.99.0.xxxxxxx-x86_64-lite.exe
```
Result: `C:\kicad-nightly\bin\kicad-cli.exe`. (Bandizip sometimes exits with code 2 even after
extracting everything successfully — if `bin\kicad-cli.exe` exists, it succeeded. This tool judges
success by the same criterion.)

**3) Full install (if you prefer)**

You can download `kicad-nightly-*.exe` from
https://downloads.kicad.org/kicad/windows/explore/nightlies and install it normally. It installs
side by side with stable KiCad in a separate folder, so you don't need to remove your existing
KiCad 10. Note that it leaves traces on the system, though, so 1) or 2) are recommended instead.

**Pointing the tool at the path** — regardless of which method you used:
   - CLI: `--kicad-cli "C:\kicad-nightly\bin\kicad-cli.exe"` (explicit, highest priority)
   - Set the environment variable `O2K_KICAD_NIGHTLY` to the executable path (added to the
     auto-detection candidates, so `--kicad-cli` isn't required) — if you did a full install, it
     also auto-detects paths like `%ProgramFiles%\KiCad Nightly*\bin\kicad-cli.exe` (detection
     order: explicit → environment variable → PATH → OS-standard install locations → the portable
     nightly folder prepared above).
   - GUI: enter it directly in the "Input" frame's "kicad-cli path" field, or leave it blank and
     the auto-detected result fills in. The gray hint text next to it, "The nightly (10.99+) is
     needed only for `.DSN` import. The result is saved in the stable KiCad 10.0 format," tells you
     when the nightly build is actually required.
   - Anything prepared with method 1) above is auto-detected **without specifying anything**
     (`%LOCALAPPDATA%\orcad2kicad\kicad-nightly\bin\kicad-cli.exe` is included among the nightly
     candidates).

### CLI example

```bash
PYTHONPATH=src python -m orcad2kicad.cli --dsn MYBOARD.DSN \
  --board MYBOARD_board.asc \
  --netlist MYBOARD.asc \
  -o out/dsn --project MYBOARD --pdf --issues
```
PowerShell:
```powershell
$env:PYTHONPATH = 'src'
python -m orcad2kicad.cli --dsn MYBOARD.DSN `
  --board MYBOARD_board.asc `
  --netlist MYBOARD.asc `
  -o out/dsn --project MYBOARD --pdf --issues
```

If you already have a KiCad-imported project from a `.DSN` (whether via nightly or another
method), you can feed its `.kicad_pro` directly with `--kicad-project` instead of `--dsn` (no
re-import — only this tool's board linking and verification are applied):

```bash
PYTHONPATH=src python -m orcad2kicad.cli --kicad-project C:\work\MYBOARD\MYBOARD.kicad_pro \
  --board MYBOARD_board.asc \
  --netlist MYBOARD.asc -o out/kp --issues
```

Since neither mode has EDIF, the report header shows `input mode: dsn` (or `kicad-project`) and
`[1]/[2] skipped: no EDIF` — [1] EDIF joining and [2] geometry verification are only possible with
EDIF (see Chapter 7). [3] kicad-cli netlist verification and [4] board comparison run exactly as
they do on the EDIF path.

### GUI usage

At the top of the "Input" frame, the radio buttons **"Input type: EDIF / OrCAD .DSN (KiCad nightly
required) / KiCad project (.kicad_pro)"** enable the matching file-input field for whichever you
choose (the other two fields are disabled). Everything else — netlist/board/output folder, etc. —
is filled in exactly as on the EDIF path; just press "Run conversion." At the top of the
Verification tab, a note appears saying `[1][2] no EDIF — skipped`.

### Measured results (sample board — 9 pages, 134 nets, 187 parts, board also specified)

| Item | Value |
|---|---|
| [3] kicad-cli netlist vs. PADS | 134/134 match, `RESULT: PASS` |
| Footprint fields | All empty strings right after import → filled with `PADS:<decal>` for all 187 references (191 part instances) after our processing |
| `PADS.pretty` | 35 footprints extracted from the board (.kicad_mod) |
| `sym-lib-table`/`fp-lib-table` | Newly generated (50 symbols in `orcad_import.kicad_sym` + `PADS.pretty` registered) |
| ERC violations | **765** right after import (before our processing, including 353 `lib_symbol_issues` because there was no library table) → **412** after passing through our pipeline (`lib_symbol_issues` **0**) |

The remaining 412 (`endpoint_off_grid` 122, `unconnected_wire_endpoint` 177, `pin_not_connected`
63, `same_local_global_label` 26, etc.) are results of the KiCad importer itself and out of scope
here (root-cause analysis is a future task).

### Filling footprint fields: board -> netlist (.asc) -> empty (Phase 3-B, `fp-fallback`)

In `--kicad-project`/`--dsn` input modes, each reference's Footprint field is filled using the
following priority order (in the EDIF input path, an OrCAD Footprint value already exists from the
start, so this fallback only applies to references whose value is **empty**, in the same order):

1. **If a board decal exists, use it** — `PADS:<decal>` (based on the `.kicad_pcb`/`.asc` read via
   `--board`, existing behavior).
2. **If it's not on the board and `--netlist` is given**, look up that reference's footprint name in
   the `*PART*` section of the OrCAD-exported PADS2000 `.asc` (one `REF  FOOTPRINT` line per part).
   - If that name matches a board decal (case-insensitive), it is unified to that decal
     (`PADS:<decal>`) — e.g. netlist name `so8` and board decal `SO8` are treated as the same.
   - If `--board` is given but no matching decal exists (e.g. netlist says `SO8` but the board has
     a differently named `SO8NB`), the netlist name is filled in **as-is, with no library prefix**,
     and an issue is left per reference: `"{REF}: footprint {name} from netlist has no PADS library
     entry"` — meaning there's no decal with this name in the PADS library, so check/assign it
     directly in KiCad.
   - **If only `--netlist` is given, without `--board`**, this step alone fills the field (supports
     running with a netlist only, without board linking) — since there's no board decal to compare
     against in this case, no per-reference issue is left (it would be repetitive and obvious that
     it's "not in the library" for all of them); instead a single summary line is left:
     `"footprints filled from netlist only (no board): N refs; names are bare OrCAD footprint
     names without a library"`.
3. **If neither exists** (not on the board, and not in the netlist either, or its name there is
   empty), the field is left empty and an issue is left: `"{REF}: no footprint source (not on
   board, not in netlist)"`.

This shouldn't happen with a well-formed PADS library, but as a defensive measure, **if two board
decals differ only in case** (e.g. `SO8` vs `so8`), the alphabetically earlier one is picked
deterministically (uppercase sorts before lowercase — `SO8`) and one issue line is left:
`"board decals differ only by case: SO8, so8 (keeping SO8)"`.

The report shows one line, `footprint fields: A set from board, B from netlist, C empty`, with
counts from the three sources. Which reference was filled from which source can be checked via
`PipelineResult.footprint_sources` (`{ref: 'board'|'netlist'|''}`, also included as-is under the
`footprint_sources` key in the MCP/GUI JSON from `result_to_json()`).

The EDIF input path (`kicad_writer.write_project`) fills in a netlist name the same way as step 2
above, but only for references not on the board whose OrCAD Footprint value is **empty** (issue:
`"{REF}: not on board; footprint from netlist: {name}"`). If an OrCAD value already exists (the
common case), the existing behavior of keeping that value is unchanged.

### Limitations (`--dsn`/`--kicad-project` only)

- **Board-only-part auto-add (placeholder symbols) is not supported.** The EDIF path's "Board Diff"
  tab "Add to schematic"/`--add-board-part` is simply ignored (with one issue line left) in these
  two modes (writing a new page into an imported schematic is a future task) — board-only parts
  like J19/J20 can only be viewed as information in the [4] report; reflect them in the schematic
  directly in KiCad, or use the EDIF path.
- **`--footprint-choice`/`--pin-type` are also unsupported in these two modes** (the footprint
  always follows the board, and the pin type stays exactly as imported) — one issue line is left
  and it continues.
- **(Resolved by default behavior) KiCad 10 stable failing to open `--dsn`/`--kicad-project**`
  results.** The nightly importer's output (multiple top-level sheets, format e.g. `20260830`) is
  automatically restructured by this tool into a root sheet + sub-sheet hierarchy (format
  `20260306`, the ceiling for stable 10.0.6), so **running with the defaults**, the result opens and
  edits directly in stable KiCad 10.0 (Phase 3-C-2). Only when `--nightly-format` is given is the
  original nightly format preserved as before, in which case the following still applies: it can
  only be opened and edited with the nightly build, and **installation is still not required**
  (the `bin\kicad.exe` inside the folder extracted by [Prepare KiCad nightly portable] is a
  complete KiCad GUI, with settings stored in a separate folder from the stable release,
  `%APPDATA%\kicad\10.99`, so the two never interfere). The GUI's Result tab [Open KiCad project]
  button checks the format and automatically uses this portable nightly `kicad.exe`; the CLI notes
  the path in the log as `note: schematic format … open with: …`.
- **The nightly build changes daily.** The version (`10.99.x`), schematic format version, and
  command-line options can change without notice — this document's and the code's
  `SCH_FORMAT_CEILING`/`supports_orcad_import` checks are based on measurements against a specific
  nightly build (`10.99.0.3703`, as of 2026-09-09); re-verification may be needed with a different
  build. If reproduction fails, try re-fetching the latest nightly.

### Checklist before removing OrCAD

Before removing OrCAD, it's recommended to do at least the following (there's no way to undo it
once you need OrCAD again):

1. **Always keep the `.DSN` and `.OLB` (library) files** — both this tool and the KiCad nightly
   importer only need the `.DSN`, but without the `.OLB` there's no way to reopen it in OrCAD to
   check anything.
2. **(Recommended safety net) Also export EDIF 2.0.0 and the PADS `.asc` netlist right now**
   (procedures in sections 4.2/4.3) — this becomes the ground truth for verifying the `.DSN` import
   result, and gives you a way back to the existing EDIF path if the nightly importer runs into
   unexpected problems.
3. If you have multiple projects, repeat steps 1 and 2 for each (automating a batch EDIF/netlist
   export with an OrCAD Capture Tcl script has only been considered, not yet implemented).

---

## 1. Overview

- **Input type (choose one of three)**:
  1. **OrCAD `.DSN`** (recommended path, Chapter 0) — start from a single `.DSN` file, without
     OrCAD Capture. Reading the `.DSN` itself is done by the native importer in KiCad nightly
     (10.99+, which can be prepared as a portable install with no installation), but all editing,
     verification, ERC, and PDF work afterward uses stable KiCad 10.0.
  2. **EDIF 2.0.0** (alternative path, full Chapters 1–13) — a schematic exported from OrCAD
     Capture. If OrCAD is installed and you only use the KiCad stable release, just use this path
     as-is. Because the deterministic reader parses it directly, this is the only path that also
     gives you [1] EDIF-join and [2] geometry verification.
  3. **KiCad project (`.kicad_pro`)** — a `.DSN` already imported into KiCad (via nightly or any
     other means) used directly as input, without re-importing.

  All three paths can additionally take an OrCAD-exported PADS2000-format netlist (`.asc`,
  optional — the baseline for automatically checking that net connectivity is correct) and a PADS
  Layout ASCII board file (`.asc` or an already-imported `.kicad_pcb`, optional — used for
  footprint mapping and board/schematic comparison).
- **Output**: a KiCad project (`.kicad_pro`/`.kicad_sch`/`.kicad_sym`), optionally a `.kicad_pcb`
  (PADS board import result + footprint library), a PDF, ERC results, and a netlist extracted by
  kicad-cli. The schematic from a `.DSN`/KiCad-project input is, by default, restructured and saved
  into the root sheet + sub-sheet hierarchy that stable KiCad 10.0 opens (see Chapters 0 and 10 for
  detail).
- **Core principle**: conversion and verification are **entirely deterministic**. The same input
  always produces the same output, and everything works fully without an API key or internet
  connection. AI (agents) are optional and **only ever suggest** — what gets applied is always
  chosen by a person in the GUI table.
- **Three ways to run it**: the tkinter GUI (`python -m orcad2kicad`, for engineers who pick files
  and click buttons), the CLI (`python -m orcad2kicad.cli ...`, for scripts/automation), and the
  MCP server (`python -m orcad2kicad.mcp_server`, for an external AI tool such as Claude Code to
  connect and run conversion/verification directly). All three internally call the same single
  `pipeline.run_pipeline()` function, so results are always identical.

## 2. Requirements

| Item | Detail |
|---|---|
| OS | Windows (development/test environment). Since it only uses tkinter/the standard library, it should in theory work on other OSes too, but this hasn't been verified. Automatic portable-nightly preparation (`--fetch-kicad-nightly`) is Windows-only. |
| Python (when running from source) | 3.10 or later. Runtime dependencies are standard library only (no extra install like the anthropic SDK needed — `agents.py` calls the API directly via the standard library `urllib`). |
| **Stable KiCad (installed, near-mandatory)** | **10.0 series recommended** (`kicad-cli` + the GUI must both be installed). Regardless of input path, editing, ERC, [3] netlist verification, PDF output, and (`.asc`) board import are all done with this one stable release. 8 or later generally works, but all the measured values in this document are based on KiCad 10.0.6. Even without kicad-cli at all, EDIF reading + 1st/2nd verification + `.kicad_sym`/`.kicad_sch` output still work (EDIF path only — the `.DSN`/KiCad project paths need kicad-cli for the import/schematic-reading step itself). |
| **Portable KiCad nightly (not an install, needed only on the `.DSN` path)** | Reading the `.DSN` file itself is a feature that still only exists in the nightly build (10.99+), so it is needed **only when using `--dsn`/`.DSN` input**. One GUI button (or `--fetch-kicad-nightly`) prepares it with no installation, just archive extraction, in a separate folder from stable KiCad so the two never interfere (Chapter 0). If you only ever use `.EDF` or an already-imported `.kicad_pro`, you don't need this nightly build at all. |
| When running as an exe | Of the requirements above, only KiCad (stable-release kicad-cli, plus nightly if using `.DSN`) is still needed. Python is bundled inside the exe, so no separate install is needed. |
| AI agent (optional) | Nothing needs to be installed (the default backend is `none`). See Chapter 8 if you want to use it. |

## 3. Installation

### 3.1 Installing via exe (recommended for general users)

1. Download the latest `orcad2kicad-<version>-win64.zip` from the GitHub releases page
   (https://github.com/nerdvanainc/orcad2kicad/releases). It contains `orcad2kicad.exe` (GUI),
   `orcad2kicad-cli.exe` (CLI/MCP), and documentation files including this manual. You can verify
   the download's integrity by comparing its SHA-256 hash against `SHA256SUMS.txt` on the same
   page (e.g. with `certutil -hashfile orcad2kicad-<version>-win64.zip SHA256`).
2. Extract the zip to a folder of your choice (it's not an installer, so there's no registry
   entry, no Start Menu entry — just move or delete the folder when you're done).
3. Double-click `orcad2kicad.exe` to launch the GUI.
4. Windows SmartScreen/antivirus software may warn on first run — see Chapter 11
   "Troubleshooting."
5. To build it yourself (for developers), see "From source" below and `packaging/build_exe.cmd`.

### 3.2 Running from source (developers, or Linux/macOS users)

> The source is publicly available on GitHub (https://github.com/nerdvanainc/orcad2kicad) under the MIT license. The exe is
> Windows-only, but since it's pure Python using only the standard library, it can be run directly
> from source on Linux/macOS (only the kicad-cli path needs to be auto-detected or specified).

```bash
git clone https://github.com/nerdvanainc/orcad2kicad.git
cd orcad2kicad
python -m pip install --upgrade pip     # only the standard library is used, so no extra install is normally needed
```

PowerShell:
```powershell
$env:PYTHONPATH = 'src'
python -m orcad2kicad          # GUI
python -m orcad2kicad.cli -h   # CLI help
```

bash (including Git Bash):
```bash
PYTHONPATH=src python -m orcad2kicad
PYTHONPATH=src python -m orcad2kicad.cli -h
```

### 3.3 Building an exe from source (developers)

```
packaging\build_exe.cmd            REM builds both GUI (orcad2kicad.exe) + CLI/MCP (orcad2kicad-cli.exe)
packaging\build_exe.cmd --cli-only REM CLI/MCP exe only (faster, no tkinter)
```

- PyInstaller must be installed (`python -m pip install pyinstaller`).
- Must be run from the repository root (the script automatically resolves `REPO_ROOT` as the
  parent folder).
- Output goes to `dist\orcad2kicad.exe`, `dist\orcad2kicad-cli.exe` (working files go under
  `build\pyinstaller\`; both are gitignored).
- kicad-cli is not bundled into the build — the exe also auto-detects a system-installed kicad-cli
  at run time (PATH → environment variable `KICAD_CLI` → OS-standard install paths).

## 4. Preparing Input Files

Prepare one of the three input paths (Chapter 0). **The `.DSN` path in 4.1 is the recommended
one** — it needs no OrCAD Capture. If you're in an environment where OrCAD Capture can still export
EDIF, the EDIF path in 4.2 works equally well (both paths verify and output the same way; only the
EDIF path adds [1][2] verification). Sections 4.3 (netlist) and 4.4 (board) apply to both paths in
common.

### 4.1 OrCAD `.DSN` (recommended path) — OrCAD Capture not required

1. You only need the `.DSN` file for the project you're converting (no need to re-export from
   OrCAD Capture — just use the `.DSN` already sitting in your existing project folder).
2. What actually reads the `.DSN` is the native importer in KiCad nightly (10.99+) — because this
   feature isn't in a stable release yet. Preparation is a single click of
   **[Prepare KiCad nightly portable (no install)]** (or `--fetch-kicad-nightly`). See Chapter 0
   for the full reasoning/process.
3. Since the import result is, by default, restructured into the format stable KiCad 10.0 opens,
   all checking/editing afterward is done with stable KiCad 10.0 — the nightly build is only
   involved at the moment the `.DSN` is read.
4. In the GUI/CLI, you only need to specify the `.DSN` file path (the "OrCAD .DSN file" field in
   the GUI, `--dsn FILE` in the CLI).

### 4.2 EDIF 2.0.0 (alternative path) — OrCAD Capture required

If OrCAD Capture is installed and you plan to keep working with the KiCad stable release only, this
path works fine as-is. Because the EDIF reader parses the schematic directly, it gives you one
extra layer of confidence over the `.DSN` path — [1] EDIF-join and [2] geometry verification
(Chapter 7).

1. Open the project (.dsn) you want to convert in OrCAD Capture 17.x.
2. `File → Export Design...`
3. Choose **EDIF 2.0.0** as the export format (other EDIF versions have not been verified by the
   parser).
4. This produces a single `.EDF` file (both uppercase and lowercase extensions are recognized).
   This is the `--edf`/GUI "EDIF file" input.

### 4.3 PADS2000 netlist (optional, strongly recommended) — verification ground truth

1. In OrCAD Capture (or the OrCAD Layout integration menu), `Tools → Create Netlist...`
2. Select **PADS** (PADS2000 family, `.asc`) as the netlist format to generate.
3. Point `--netlist`/the GUI's "PADS netlist (.asc)" at this file, and the conversion result's net
   connectivity is automatically checked against it at the REF.PIN level for 100% match (the EDIF
   path checks [1][2][3] all; the `.DSN`/KiCad project path checks only [3] — see Chapter 7).
   Without it, conversion still works, but there's no way to confirm the nets are correct.

### 4.4 PADS Layout ASCII board (optional) — for footprint mapping + board comparison

To fill the schematic parts' Footprint field with the actual decal used on the real board, and to
be able to immediately use "Update PCB from Schematic" in the KiCad PCB Editor, you need the
actual routed PADS board.

1. Open the target board (.pcb) in PADS Layout.
2. `File → Export → ASCII...`
3. In the export options:
   - **Include all sections** (exporting only some of parts/nets/pads/silkscreen etc. makes the
     import incomplete).
   - Choose the **latest version** format.
   - Set the unit to **mm** (a different unit can shift coordinates in the kicad-cli import
     result).
   - **Make sure copper pour is included.** By default, the PADS ASCII export can leave out
     polygon fill information (`POUR_OUTLINES`), in which case a board imported via kicad-cli will
     show every pad that should be connected via a polygon (e.g. GND) as
     "unconnected," making DRC results untrustworthy (see Chapter 12 "Known Limitations").
4. Point `--board`/the GUI's "PADS layout ASCII or KiCad PCB" at the resulting `.asc` file. If you
   already have a `.kicad_pcb` imported into KiCad, you can point at that file directly instead
   (it's read as-is, without re-importing).

## 5. Using the GUI

Run with `python -m orcad2kicad` (from source) or `orcad2kicad.exe` (exe). The window is split
into an input/AI area at the top and a result-tabs (Notebook) area at the bottom.

**Language**: the top-right corner of the "Input" frame has a "Language: 한국어/English"
combo box. On first launch the language is picked automatically from the OS locale (Korean if
it starts with `ko*`, English otherwise), and whichever language you pick afterward is saved to
`settings.json` and restored on the next run. Choosing a different language redraws the whole
window (labels, tab names, table headers, dialogs, etc.) immediately, with no restart needed,
and keeps the last conversion result and any choices made in the board-diff table. Pipeline
progress messages in the Log tab are always English (same as the console), regardless of the
selected language.

[Screenshot: full main window — top "Input" frame ("Input type" radio buttons at top for
EDIF/OrCAD .DSN/KiCad project, below that three file fields for EDIF/`.DSN`/KiCad project (only the
selected type is enabled), netlist/board/output folder/project/kicad-cli path, the
[Prepare KiCad nightly portable (no install)] button with its gray hint text, net-name radio
buttons/PDF/strict checkboxes), below that an "AI Agent" frame, a "Run conversion" button and
progress bar, and below that 6 tabs (Log/Verification/Issues/Board Diff/Agent Suggestions/Result) —
shown with input type set to "OrCAD .DSN", only the `.DSN` field enabled, and the status bar
reading "Done (all verification PASS)" after a completed run]

### 5.1 Input area

The **"Input type"** radio buttons at the top (EDIF / OrCAD `.DSN` (KiCad nightly required) / KiCad
project (`.kicad_pro`)) enable only the file field matching your choice; the other two fields are
grayed out (exactly one of the three is passed to the pipeline — see Chapters 1 and 0).

| Item | Description |
|---|---|
| Input type | Choose EDIF / OrCAD `.DSN` / KiCad project (`.kicad_pro`) via radio button. Default is EDIF. Of the three file fields below, only the one matching the selected type is enabled (with its Browse button). |
| EDIF file | The `.EDF` made in section 4.2. Enabled only when "Input type: EDIF" — required at that point; leaving it empty makes the Run button warn. |
| OrCAD `.DSN` file | The `.DSN` from section 4.1. Enabled only when "Input type: OrCAD `.DSN`" (recommended path). If the nightly kicad-cli isn't available, running gives a clear error. |
| KiCad project (`.kicad_pro`) | A project already imported into KiCad. Enabled only when "Input type: KiCad project." |
| PADS netlist (.asc) | Section 4.3. If left empty, the [1][2] (EDIF input only)/[3] verification tabs just show a note that they're empty. |
| PADS layout ASCII or KiCad PCB | Section 4.4. The routed board file exported from PADS Layout via Export ASCII. Either `.asc` or `.kicad_pcb`. If left empty, [4] board comparison and footprint mapping are skipped. |
| Output folder | The folder the KiCad project will be written to (created if missing). Once you pick the input file (whichever of EDIF/`.DSN`/`.kicad_pro` is selected), if this field is empty it auto-fills with `<input file folder>\<name>_kicad` (the project name is also taken from the file name, and if a matching-named PADS netlist/board `.asc` in the same folder is recognized by its header, that field is filled too — fields that already have a value are left untouched). For EDIF input, clearing this field means files aren't written and only verification runs (equivalent to omitting `-o` on the console tool). `.DSN`/KiCad project input writes to the same default folder (`<input file folder>\<name>_kicad`) even if left blank — there is no "verification only" mode for these. |
| Project name | If left empty, EDIF input uses the design name; KiCad project/`.DSN` input uses the input `.kicad_pro`/`.DSN` file name as-is. |
| kicad-cli path | If left empty, the auto-detected path is filled in on startup. If multiple versions are installed, force one here (either stable or nightly can be specified — see the hint text below and Chapter 0 for which is needed when). |
| [Prepare KiCad nightly portable (no install)] button | Prepares the latest KiCad nightly with no installation, just archive extraction, and auto-fills the "kicad-cli path" field above once done (Chapter 0). The gray hint text beside it, **"The nightly (10.99+) is needed only for `.DSN` import. The result is saved in the stable KiCad 10.0 format,"** tells you when the nightly build is actually needed — if you only use EDIF or an already-imported KiCad project, you don't need to click this button. |
| Net names: auto/keep board names (keep)/KiCad style | "Auto" uses `keep` if a board is specified, `kicad` otherwise. `keep` leaves the PADS net names as global labels so they keep matching the board (recommended when board-linking). `kicad` mode lets KiCad assign its own names like `Net-(...)`. `.DSN`/KiCad project input always behaves as `keep` regardless of this choice, since the imported schematic already uses global labels (a note about this appears in the log). |
| PDF output | If checked, also produces a schematic PDF via kicad-cli. |
| Board diff strict | If checked, treats the run as a failure (exit code 1) when [4] board vs. schematic comparison fails or references differ. Off by default, showing information only and finishing as a success. |

Clicking "Run conversion" runs the pipeline in a separate thread (the window doesn't freeze), and
progress logs stream live into the "Log" tab. When it finishes, the status bar shows one of `Done
(all verification PASS)` / `Done (verification FAIL present)` / `Error (I/O or kicad-cli)`.

Input values are saved to `~/.orcad2kicad/settings.json` when the window is closed (or when
running) and restored the next time you launch (**the API key is never saved** — see Chapter 8).
The path can be changed with the environment variable `O2K_SETTINGS`.

### 5.2 Result tabs

- **Log**: progress messages emitted by the pipeline/agents (in English, same as the console).
  When the run finishes, the same **summary explanation (in Korean)** as the Result tab is appended
  at the bottom (scroll back to see it again).
- **Verification**: when a PADS netlist is supplied, shows [1] EDIF-join vs. PADS, [2] geometry
  vs. PADS, and (if kicad-cli is available) [3] kicad-cli netlist vs. PADS, each with a summary
  line + a per-net table. The Status column reads `OK`/`DIFF`/`ONLY_OURS`/`ONLY_REF` (meanings
  explained in Chapter 7). In `.DSN`/KiCad project input mode there is no EDIF, so [1][2] are
  absent — a gray note reading `[1][2] no EDIF — skipped` appears at the top of the tab.
- **Issues**: a list of issues detected during conversion (unsupported constructs found while
  parsing, writer warnings, board mapping notes, etc.). Typing into the input box above filters the
  list live by substring match (case-insensitive).
- **Board Diff**: shows the [4] board vs. schematic comparison as a table, and lets you pick a
  resolution per row (section 5.3 below).
- **Agent Suggestions**: a list of suggestions from the AI backend, with checkboxes (section 5.4
  below).
- **Result**: a **"Summary explanation"** box at the top, then a list of the files produced and
  their paths below.
  - The **Summary explanation** puts this run into plain language (`explain.py`) — what the input
    was, what [1][2] (if applicable)/[3]/the footprint-field sources/[4] each verified and what the
    outcome means, ERC violations broken down by type into **informational (no action needed) /
    worth checking / action needed**, which KiCad to use to open the output files, and finally one
    closing line: "Conclusion: no issues" or "Conclusion: items to check - ...". In `.DSN`/KiCad
    project input mode, it also explains here that [1][2] are absent, and — if `.DSN` was used —
    when the nightly build is actually needed. In `.DSN`/KiCad project input mode, the same
    `[1][2] no EDIF — skipped` note as the Verification tab also appears above this box.
  - The file list shows only what was actually produced: root schematic, symbol library
    (`orcad_import.kicad_sym`, etc.), `.kicad_pro`, board, PDF, ERC result JSON, kicad-cli netlist,
    and so on.
  - **"Open result folder"** opens the output folder in Explorer.
  - **"Open KiCad project"** opens the `.kicad_pro` — if the schematic format exceeds stable KiCad
    10.0's ceiling (20260306) (which happens if `--nightly-format` was given, keeping the original
    nightly format), the file association (the installed stable KiCad) won't open it, so this
    button automatically finds the portable nightly `kicad.exe` next to kicad-cli and launches it
    directly with that. If the portable nightly hasn't been prepared yet, it warns "KiCad nightly
    required" and points to the preparation button in the input frame. With the default (restructured, format 20260306), it just opens with the associated program, so this issue never comes up.

### 5.3 Resolving mismatches in the "Board Diff" tab

This tab is only populated when `--board` is given. Each row in the table is one of the following 5
kinds; selecting a row lets you choose a resolution from the combo box below.

[Screenshot: "Board Diff" tab — a table with Type/Target/Detail/Resolution columns showing rows
'board-only parts J19·J20', 'footprint name mismatch U7~U10', 'pins not on board pads H1~H4', with
row J19 selected and the combo box below set to "Add to schematic" + an "Apply and re-output"
button]

| Type (display name) | Meaning | Options | What choosing it means |
|---|---|---|---|
| Board-only parts | References that exist on the board but not in the schematic (e.g. J19, J20) | Ignore / Add to schematic | Choosing "Add to schematic" causes the next run to create a placeholder symbol for that reference (a minimal symbol with only pin names) on a separate page called `99-PCB-ONLY`. Use this when the part is real but the schematic hasn't been updated yet. Choosing "Ignore" leaves it appearing in the table on every subsequent run (to truly get rid of it, you have to remove the part from the board or reflect it in the schematic). |
| Schematic-only references | References that exist in the schematic but not among the board parts | Ignore / Hide from report | "Hide from report" stops showing this reference in future [4] reports (this doesn't actually delete or add the part — it only **hides it from reporting** — use it for things like mechanical parts that normally don't appear on the board). |
| Footprint name mismatch | Same reference, but the OrCAD Footprint field name differs from the board decal name (e.g. U7~U10 SO8 vs. SO8NB) | Board / OrCAD | "Board" (default) uses the board-side decal on the next output (usually correct, since it reflects the actually routed PCB). "OrCAD" prioritizes the schematic's Footprint field value — use this when the board is an older version, or the schematic reflects the latest design intent. |
| Pins not on board pads | A schematic pin number is missing from the board footprint's pads (e.g. pin 1 of H1~H4) | Ignore / Hide from report | Often a pin with no net (mounting holes, etc.) — since it has no effect on actual routing, "Hide from report" is usually the right choice. |
| Net difference | A net mismatch between the reference netlist/board (DIFF/net only on board/net only in reference) | Ignore (no other option) | This is informational only. It's a problem that has to be fixed in the actual schematic or board routing, and can't be resolved directly from this table — identify which net it is and fix the routing directly in KiCad/PADS. |

After choosing what you want for each row, pressing **"Apply and re-output"** converts the current
table selections into the four fields of `Resolutions` (`add_board_only_parts` = board-only
references to add to the schematic, `footprint_choice` = footprint source per reference,
`pin_type_overrides` = library pin electrical type, `ignore_refs` = references to hide from the
report) and re-runs the pipeline — i.e., the files in the output folder get overwritten. Three of
these four can be chosen from the table; `pin_type_overrides` only comes in via agent suggestions
(section 5.4) or the CLI's `--pin-type`.

"Apply and re-output" is **safe to click multiple times.** A part already resolved via "Add to
schematic" drops out of the next [4] report's "references only on board," but the row stays in the
table with its resolution still "Add to schematic" — so the decision persists on a second
re-output too (the placeholder page doesn't disappear), and you can change your mind and set that
row back to "Ignore."

Also, if there's even a single board-only placeholder part, the net-name mode is automatically
forced to `keep` (because a placeholder symbol only attaches to a net via a global label). At that
point, the "Net names" radio button above also switches to `keep`, and one log line,
`net names: pipeline used keep (selected kicad)`, is left in the Log tab — this is so the on-screen
selection never diverges from the actual result.

This decision does
**not** persist across restarting the GUI (only the input paths are saved to the settings file, not
`Resolutions` itself) — to keep using the same decisions, pass them directly through the CLI's
`--add-board-part` / `--footprint-choice` / (where applicable) via code.

### 5.4 Using AI suggestions in the "Agent Suggestions" tab

[Screenshot: "Agent Suggestions" tab — a 7-column table (Selected/Agent/Type/Target/Content/
Rationale/Confidence) mixing pin-type suggestions (checkbox `[ ]`) with net/board-diff suggestions
(checkbox `[v]`), with a "Review summary" box below filled with a Korean markdown summary, plus
"Generate suggestions" / "Apply selected and re-output" buttons]

1. First, choose a backend in the "AI Agent" area (Chapter 8), and confirm the status text next to
   it reads "Available."
2. Check the agents you want to run (pin type/net-board diff/review summary — all checked by
   default).
3. Pressing the "Generate suggestions" button (works the same whether pressed in the AI area or
   inside the Agent Suggestions tab) runs the agents against the most recent conversion result and
   fills the table with suggestions.
4. Clicking the "Selected" column toggles the checkbox. **Pin-type suggestions are unselected by
   default** (a pin's electrical type doesn't affect net connectivity, but filling it in wrong
   changes the flavor of ERC warnings — the intent is for a person to skim through and pick). Other
   kinds (board-only part addition, footprint choice) come pre-selected.
5. Pressing **"Apply selected and re-output"** reflects only the checked suggestions into
   `Resolutions`/pin-type overrides, then re-runs the pipeline. Suggestions that can't be applied
   (a pin type not in the library list, an empty reference, etc.) are silently dropped.
6. The "Review summary" box shows the Korean markdown summary (verification results, ERC, a
   round-up of remaining risk) produced by the review agent — reference text, not something you
   apply.

## 6. Using the CLI

Run with `python -m orcad2kicad.cli ...` from source (`src` needs to be in `PYTHONPATH`), or
`orcad2kicad-cli.exe ...` from the exe.

### 6.1 Full option table

Confirmed directly via `PYTHONPATH=src python -m orcad2kicad.cli --help` (as of 2026-09-09;
re-check as the version advances):

| Option | Meaning | Default |
|---|---|---|
| `edf` (positional) | Path to an OrCAD EDIF 2.0.0 export. Omit when using `--kicad-project`/`--dsn` instead (exactly one of the three must be given) | — |
| `--kicad-project PRO` | An existing KiCad project (`.kicad_pro`) to use as input (e.g. a `.DSN` imported directly in KiCad). Since there's no EDIF, [1][2] verification is skipped (see Chapter 0, "Converting Without OrCAD") | none |
| `--dsn FILE` | An OrCAD `.DSN` file. Converted via kicad-cli's native importer (**requires KiCad nightly 10.99+**), then proceeds the same as `--kicad-project` | none |
| `--netlist NETLIST` | Reference PADS2000 `.asc` netlist | none (given → runs [1][2] verification) |
| `--report REPORT` | Also save the report text to this file | none (stdout only) |
| `--explain` | Appends a summary explanation to the end of the report — what each verification checked and what the result means, ERC type meanings and whether action is needed, and which KiCad to open the result files with (in English). The GUI always shows the same content in Korean at the end of the Result tab and Log. | off |
| `--issues` | Print the full list of detected issues | off (only the summary line's count is shown) |
| `-o, --outdir OUTDIR` | Folder to write the KiCad project into | none (given → actually writes files) |
| `--project PROJECT` | Project name | EDIF design name |
| `--kicad-cli KICAD_CLI` | Path to the kicad-cli executable | auto-detected |
| `--pdf` | Also output a schematic PDF via kicad-cli | off |
| `--board BOARD` | PADS board (`.asc` is imported via kicad-cli; `.kicad_pcb` is read as-is) | none |
| `--net-names {kicad,keep}` | How net names are assigned | `keep` if `--board` is given, `kicad` otherwise |
| `--strict-board` | Exit code 1 if [4] board vs. schematic comparison FAILs or references differ | off (information only, no effect on exit code) |
| `--nightly-format` | `--kicad-project`/`--dsn` only. Leaves the nightly importer's output **as-is** instead of restructuring it into the stable KiCad 10.0 format (root sheet + sub-sheets, Phase 3-C-2) (opening that result and running [3]/ERC on it will again require the nightly kicad-cli) | off (default always restructures into the stable format) |
| `--add-board-part REF` | Add a board-only reference to the schematic as a placeholder symbol (repeatable) | none |
| `--footprint-choice REF=board\|orcad` | Choose the footprint source per reference (repeatable) | `board` for any reference not specified |
| `--pin-type SYMBOL:PIN=TYPE` | Override a library pin's electrical type (e.g. `STM32H723ZGT6:6=power_out`, repeatable) | none |
| `--fetch-kicad-nightly [DIR]` | **A separate action from conversion.** Downloads the latest KiCad nightly installer, **only extracts the archive** (the installer is never run), prepares the portable kicad-cli, prints its path, and exits. An archive tool is **not required** — if 7-Zip/Bandizip is missing, it fetches the 7-Zip console edition (about 6 MB) from the official site and, again, only extracts it (never installs it). Windows only. See Chapter 0 | `DIR` defaults to `%LOCALAPPDATA%\orcad2kicad\kicad-nightly` |
| `--kicad-nightly-installer FILE` | With `--fetch-kicad-nightly`: use an installer you've already downloaded (no network access) | none (downloads otherwise) |
| `--force-fetch` | With `--fetch-kicad-nightly`: delete and re-extract even if already prepared (update the nightly) | off |
| `--no-download-tools` | With `--fetch-kicad-nightly`: **don't** fetch the 7-Zip console edition when no archive tool is present — fail with guidance instead | off (fetches and extracts it if missing) |

`--add-board-part`/`--footprint-choice`/`--pin-type` are **only supported with `edf` input**. In
`--kicad-project`/`--dsn` input, they're ignored, with only one issue line left (see the new
"Limitations" section at the top).

### 6.2 Examples

**1) Verification only (writes no files, 1st/2nd)**

bash:
```bash
PYTHONPATH=src python -m orcad2kicad.cli MYBOARD.EDF \
  --netlist MYBOARD.asc --issues
```
PowerShell:
```powershell
$env:PYTHONPATH = 'src'
python -m orcad2kicad.cli MYBOARD.EDF `
  --netlist MYBOARD.asc --issues
```

**2) With KiCad output (1st/2nd/3rd verification + ERC + PDF)**

bash:
```bash
PYTHONPATH=src python -m orcad2kicad.cli MYBOARD.EDF \
  -o out/kicad --project MYBOARD \
  --netlist MYBOARD.asc --pdf --issues
```
PowerShell:
```powershell
python -m orcad2kicad.cli MYBOARD.EDF `
  -o out/kicad --project MYBOARD `
  --netlist MYBOARD.asc --pdf --issues
```

**3) PADS board linking (footprint mapping + [4] comparison)**

bash:
```bash
PYTHONPATH=src python -m orcad2kicad.cli MYBOARD.EDF \
  -o out/kicad --project MYBOARD \
  --netlist MYBOARD.asc \
  --board MYBOARD_board.asc --pdf --issues
```
PowerShell:
```powershell
python -m orcad2kicad.cli MYBOARD.EDF `
  -o out/kicad --project MYBOARD `
  --netlist MYBOARD.asc `
  --board MYBOARD_board.asc --pdf --issues
```

**4) Specifying mismatch resolutions directly from the CLI**

```bash
PYTHONPATH=src python -m orcad2kicad.cli MYBOARD.EDF \
  -o out/kicad --project MYBOARD \
  --netlist MYBOARD.asc \
  --board MYBOARD_board.asc \
  --add-board-part J19 --add-board-part J20 \
  --footprint-choice U7=orcad --strict-board
```

The exit code is 0 (all verification PASS, or none was run) / 1 (any of [1][2][3] FAILed, or [4]
FAILed with `--strict-board`) / 2 (couldn't read the input, couldn't write the output, or kicad-cli
itself failed to run).

## 7. Reading Verification Results

The converter compares "the net connectivity we built" against "the ground truth (reference)" in
up to 4 stages. All of them are based on REF.PIN sets (which parts' which pins are tied to the same
net), and **two nets with different names but the same REF.PIN set are treated as the same net**
(since many nets in the reference file have no name at all).

| Stage | What's compared | Needs kicad-cli? | Meaning |
|---|---|---|---|
| [1] EDIF join vs. PADS | Nets built by directly joining the EDIF connectivity data (pin-to-net connection graph) vs. `--netlist` | No | Whether the reader interpreted the EDIF correctly |
| [2] Geometry vs. PADS | Nets derived geometrically from the schematic drawing (wire/pin coordinates) vs. `--netlist` | No | Whether the coordinate/wire-connection handling is correct (re-confirmed via a path independent of the reader's result) |
| [3] kicad-cli netlist vs. PADS | The actual `.kicad_sch` written, exported as a netlist via kicad-cli, vs. `--netlist` | Yes | Whether the **final output** reads as the same nets in KiCad too (only this stage catches format-level bugs) |
| [4] Board vs. schematic | PCB nets read via `--board` vs. the baseline above (if `--netlist` isn't given, the schematic netlist extracted via kicad-cli is the baseline — the report title reads `board pad nets vs schematic netlist`) | Yes if `.asc`, no if `.kicad_pcb` | Whether the actual routed board matches the schematic (down to part/footprint/pin matching) |

Each stage's result falls into one of these statuses (the Status column in the GUI's Verification
tab, same text in the CLI report):

- **PASS / OK** — that net (or the whole stage) fully matches the reference.
- **DIFF** — a net paired up by name/set, but the REF.PINs differ (a pin that's only on our side
  isn't `missing` — `missing` means "in the reference but not ours," `extra` means the opposite —
  see the "reference only (missing)"/"ours only (extra)" column names in the GUI table).
- **ONLY_OURS** — a net that exists only in our output, not in the reference (either the reference
  netlist doesn't know about this net, or we split it incorrectly).
- **ONLY_REF** — a net that exists in the reference but not in our output (an entire connection is
  missing — the most serious category).

The last line of the report, `RESULT: PASS`/`RESULT: FAIL`, is PASS only if every net in that stage
is OK. Exit code 1 appears if any of [1][2][3] FAILs (only `--strict-board` makes [4] affect the
exit code — since schematic and board often reflect different points in time in the design, this
stays informational by default).

The **ERC summary line** (`erc violations: N ...`) is the count of KiCad ERC (Electrical Rules
Checker) results. Per-violation-type detail is in `erc.json` (path shown in the GUI's "Result"
tab). This number is independent of net-connectivity correctness — for example, if the footprint
field is empty (converted without a board), `footprint_link_issues` shows up once per part, but the
net connectivity itself can still be perfectly correct.

Measured on the sample board (9 pages, 134 nets, 187 parts):

| Run | ERC violations | Breakdown |
|---|---|---|
| Without `--board` | **295** | `footprint_link_issues=191`, `different_unit_footprint=2`, `pin_not_connected=63`, `unconnected_wire_endpoint=23`, `power_pin_not_driven=12`, `isolated_pin_label=2`, `pin_not_driven=2` |
| With `--board` | **102** | `footprint_link_issues` **0** (disappears once footprints are filled from the PADS library), the rest are the same as above |

In other words, most of the violations that appear without `--board` (191/295) come down to one
thing — "the footprint is empty" — and are unrelated to net connectivity. The remaining 102 are
things carried over as-is from the original schematic, like unused pins and unconnected wire
endpoints. Adding placeholder parts via `--add-board-part` adds a few more, proportional to that
symbol's unconnected pins.

## 8. AI Backend Setup

**AI is always optional.** Without a backend (the default, `none`), conversion, verification, and
KiCad output all work completely as-is. AI only produces three things — "pin type suggestions,"
"board/schematic diff resolution suggestions," and "result review summaries" — and whether to apply
them is always chosen by a person in the GUI table (section 5.4).

| Backend | How to set it up | Cost | Confidentiality |
|---|---|---|---|
| `none` (default) | Nothing to do | None | No data ever leaves the machine |
| `api` (calls the Anthropic API directly) | Set the environment variable `ANTHROPIC_API_KEY`, or paste it into the GUI's "API key" field (a value entered in the field is **never saved** — it disappears when the window is closed, and only the environment-variable default is filled in again on the next run). To use it directly from CLI/scripts: `agents.make_backend('api', api_key=..., model=...)`. | Billed to Anthropic based on token usage (roughly a schematic summary, issue list, pin list per call — a few KB per call even for a large design). | **Schematic design data (part names, pin names, net diffs, etc.) is sent to the Anthropic API.** If this is a confidential in-house design, check your data-export policy before using this backend. |
| `claude-cli` (local Claude Code CLI) | Requires Claude Code (`claude`) installed and logged in on the machine (reuses subscription auth, no API key needed). Internally calls `claude -p ... --output-format json`. | Consumed within your subscription plan (no separate API billing, though usage limits follow your account policy). | Design data is likewise sent to an Anthropic service (only the transport differs — CLI/subscription instead of API). |
| `codex-cli` (experimental) | Requires the OpenAI Codex CLI (`codex`) installed on the machine (calls `codex exec ...`). Not measured live on this machine (not installed) — behavior has only been verified at the code-review level. | Follows Codex CLI/account policy. | Design data is sent to an OpenAI service. |

In the GUI, choosing a backend immediately shows "Available: ..." or "Unavailable: reason" next to
it (e.g. `ANTHROPIC_API_KEY not set`, `claude CLI not found in PATH`). In CLI/MCP, the environment
variable `O2K_AGENT_BACKEND` (`none`/`api`/`claude-cli`/`codex-cli`) sets the default backend (see
the MCP `suggest` tool, Chapter 9).

> **Note**: the `api` backend hasn't been live-tested on this machine (no API key here) — the
> actual call path (HTTP request/response parsing) has only been verified via code review and
> error-handling paths (unit tests using `FakeBackend`). It's recommended to verify it once for
> real in an environment that has an API key.

## 9. Registering the MCP Server

An MCP (Model Context Protocol) server lets an external AI tool such as Claude Code call this
converter directly as a tool (stdio, JSON-RPC 2.0, implemented with only the standard library — no
SDK dependency).

### 9.1 Registering with Claude Code

```bash
# Directly from source (PYTHONPATH needs src for the orcad2kicad package to be visible)
claude mcp add orcad2kicad -e PYTHONPATH=<repo>/src -- python -m orcad2kicad.mcp_server

# If PYTHONPATH is already set in the shell
claude mcp add orcad2kicad -- python -m orcad2kicad.mcp_server

# If distributed as a single exe
claude mcp add orcad2kicad -- orcad2kicad-cli.exe --mcp
```

### 9.2 Registering directly via config JSON (example)

```json
{
  "mcpServers": {
    "orcad2kicad": {
      "command": "orcad2kicad-cli.exe",
      "args": ["--mcp"]
    }
  }
}
```

For a source-run version:
```json
{
  "mcpServers": {
    "orcad2kicad": {
      "command": "python",
      "args": ["-m", "orcad2kicad.mcp_server"],
      "env": { "PYTHONPATH": "<repo>/src" }
    }
  }
}
```

### 9.3 Runtime options

- `--root DIR` — a root directory the `read_file` tool is allowed to read from (repeatable).
  Can also be given via the environment variable `O2K_MCP_ROOT` (separated by `os.pathsep`, i.e.
  `;` on Windows). If not specified, only the output folder of the most recent `convert` call can
  be read.
- `--selftest` — prints the tool list as JSON and exits immediately (exit 0). For install
  verification/packaging smoke tests. Example:
  `PYTHONPATH=src python -m orcad2kicad.mcp_server --selftest`.

**The `outdir` passed to `convert` becomes an allowed root for `read_file` from then on.** In other
words, even without giving any `--root`, once you've run a conversion, the AI can immediately read
the `.kicad_sch`/`erc.json`/netlist inside that output folder (anything outside that folder is
still refused).

### 9.4 List of provided tools (`tools/list`, as observed)

| Tool | What it does | Writes files? |
|---|---|---|
| `convert` | Converts to a KiCad project + runs every verification that's possible. Input is exactly **one** of `edf` (OrCAD EDIF, all of [1]–[4]) / `dsn` (OrCAD `.DSN` — natively imported via nightly kicad-cli, then [3][4] without [1][2]) / `kicad_project` (an existing KiCad project, [3][4] without [1][2]) (any other combination is a ToolError). `outdir` is required; it also accepts all the CLI-corresponding options: `netlist`/`board`/`project`/`net_names`/`pdf`/`strict_board`/`kicad_cli`/`add_board_only_parts`/`footprint_choice`/`pin_type_overrides`/`ignore_refs`, etc. (`add_board_only_parts`/`footprint_choice`/`pin_type_overrides` only apply with `edf` input). | Yes (tens of seconds; `.DSN` import can take longer) |
| `verify` | Reads only the EDIF and runs just [1][2] verification against `--netlist` (no files written, a few seconds) | No |
| `board_diff` | Produces just the [4] comparison report from EDIF + board alone | No (a `.asc` board is briefly imported via kicad-cli into a temp folder) |
| `list_issues` | The issue list left by the most recent convert/verify/board_diff run in this session | No |
| `read_file` | Reads a text file inside the conversion output folder (or `--root`) — `.kicad_sch`, `erc.json`, netlists, etc. `..` path escapes are refused. | No |
| `find_kicad_cli` | Finds and returns the path to kicad-cli (argument → `KICAD_CLI` → PATH → OS-standard paths) | No |
| `prepare_kicad_nightly` | Prepares the nightly kicad-cli **without installing it** (downloads the official installer → extracts the archive → cleans up → verifies it runs). Arguments `root`/`installer`/`force`/`download_tools`, all optional. Never runs an installer program (neither KiCad's nor 7-Zip's). If no archive tool is present, fetches the 7-Zip console edition and only extracts it (`download_tools: false` fails instead) | Yes (first call takes a few minutes, about 234 MB downloaded; instant afterward) |
| `suggest` | Runs the AI agents (pin type/net diff/review), returns only suggestions (doesn't apply them). Backend from the argument or `O2K_AGENT_BACKEND` environment variable. | No (though if `outdir` is given, it first runs a full conversion to gather board/ERC data) |

Note: the pipeline runs synchronously, so the server doesn't read the next request until one
`convert` call finishes (multiple concurrent converts cannot be sent — see Chapter 12). Watch the
stderr log for progress.

## 10. Finishing Up in KiCad

Automated verification only confirms that **net connectivity matches the reference**. Visual checks
like symbol shape, rotation, and text overlap, and how "Update PCB from Schematic" actually behaves
on the real PCB, still need to be checked by a person in the KiCad GUI.

**Opening and checking the result works the same way regardless of input path.** The result from
`.DSN`/KiCad-project input is, by default (when `--nightly-format` wasn't given, Chapter 0), stored
in the stable KiCad 10.0 format (version 20260306) — a root sheet (`<project>.kicad_sch`) plus a
sub-sheet per page (`P01_...`, `P02_...`, ... — restructured from what were originally multiple
top-level sheets made by the nightly importer), so just like the EDIF path's result, **double-
clicking opens it in stable KiCad 10.0 for editing/ERC/PCB update right away** — the nightly build
isn't needed again. Page-by-page comparison, the ERC ignore list, and the Update PCB procedure all
apply the same way to `.DSN`/KiCad-project path results too (since they share the same
multi-sheet hierarchy and the same `PADS:<decal>`-filled footprints).

1. **Visual schematic comparison + ERC** — open the `.kicad_pro` and compare it page by page
   against the original schematic. Most ERC results caused by importer quirks/KiCad conventions —
   off-grid endpoints (`endpoint_off_grid`), wire endpoints without an unconnected-flag marker
   (`unconnected_wire_endpoint`), missing `PWR_FLAG` (see the explanations in Chapters 7 and 12) —
   can be ignored; check actual connectivity on that page for any unfamiliar kind of warning.
2. **PCB Update PCB from Schematic + DRC** — if you converted with `--board`, in Pcbnew's Update
   PCB from Schematic (F8) dialog, make sure you **turn off "Delete footprints with no symbols"**
   (leaving it on deletes board-only parts). Check the preview of what will be added/removed/
   changed before applying it. Running DRC afterward may show a large number of unconnected
   warnings because copper pour was missing from the PADS ASCII export (see sections 4.4 and 12) —
   these are not actual routing errors but an export-option issue, so be sure to distinguish them.

## 11. Troubleshooting

| Symptom | Cause/how to check | Fix |
|---|---|---|
| A "kicad-cli not found" message, with [3] verification/ERC/PDF/board import all skipped | kicad-cli isn't on PATH or in a standard install location | Install KiCad, or specify it directly via `--kicad-cli PATH` (CLI)/the "kicad-cli path" field (GUI)/the `KICAD_CLI` environment variable. The `find_kicad_cli` MCP tool or the GUI's auto-fill on startup can confirm the actually detected path. |
| EDIF parse error (`error: cannot read ...` or exits with an exception) | (1) OrCAD exported something other than EDIF 2.0.0, (2) the file is truncated/corrupted, (3) an EDIF construct the parser doesn't handle (see the carried-over issue list in the document) | Re-export following the steps in section 4.2. Use `tools/inspect_edif.py <file> "<cell name>"` to dump a specific cell's structure and see where it gets stuck. If reproducible, file it as an issue (with specifics on which cell/construct). |
| Net mismatch (DIFF/ONLY_OURS/ONLY_REF in [1]/[2]/[3]) | The schematic and PADS netlist are from different design versions, or it's a genuine conversion bug | Narrow down the cause using the table in Chapter 7: **ONLY_REF** (in the reference but missing from ours) is the most serious — an entire net is missing, so start by checking the EDIF reader's issue list (`--issues`). For **DIFF**, narrow down which REF.PIN differs (the `missing`/`extra` columns) and cross-check that part/net directly in EDIF. **ONLY_OURS** usually means the netlist is stale or we split something incorrectly — if re-exporting the reference netlist still doesn't match, file it as a bug. |
| GUI doesn't launch (`error: tkinter is not available ...`) | Python was installed without tkinter (some minimal installs/Linux distros) | Use a standard CPython install (the official Windows installer includes it by default), or run the exe (`orcad2kicad.exe`, which bundles tkinter). |
| GUI doesn't launch (`error: cannot start GUI: ...`) | An exception while creating widgets (rare, e.g. a corrupted settings file) | Delete `~/.orcad2kicad/settings.json` or point `O2K_SETTINGS` at a different path and retry. If reproducible, file it as an issue with the full error message. |
| Windows SmartScreen ("Windows protected your PC") or antivirus quarantine/deletion when running the exe | A PyInstaller single-file exe is an unsigned new executable, which commonly triggers this (a distribution-method quirk, not malware) | SmartScreen: click "More info" → "Run anyway." Antivirus: if it's a false positive, add it to the exception list. If your organization's policy requires signing, consider signing the exe with a code-signing certificate (currently unsigned). If you don't trust it, run from source as described in section 3.2. |
| Exit code is 1 even without `--strict-board` | One of [1][2][3] FAILed (unrelated to strict-board) | See the "Net mismatch" row above. |
| The MCP server appears unresponsive/stuck | `convert` runs synchronously, so it doesn't read the next request until it finishes (can take tens of seconds for a large design — this is normal), or it's genuinely stuck | Check the stderr log (is progress still being printed?). If the log hasn't grown for several minutes, the kicad-cli subprocess may be stuck — kill the process and restart. |
| Double-clicking the `.kicad_pro` from a `.DSN`/`--kicad-project` result directly in stable KiCad 10 GUI fails to recognize the schematic format and refuses to open it (an error to the effect that the file was made by a newer version of KiCad) | Only happens when `--nightly-format` was given, keeping the nightly importer's original format (e.g. version 20260830, above the ceiling of 20260306) — doesn't happen with the default (restructured) behavior | Re-run without the flag (defaults) to restructure into the stable format, or, if you want to keep that result in nightly format, open it with the GUI's "Open KiCad project" button (which automatically uses the portable nightly `kicad.exe`) or by pointing `--kicad-cli` directly at the nightly (see Chapter 0; the CLI log's "note: schematic format ... open with: ..." line tells you the same thing). |
| Rebuilding with `packaging\build_exe.cmd` fails to overwrite the exe file (a permission/access-denied-type error) | A previously built `dist\orcad2kicad.exe`/`orcad2kicad-cli.exe` is still running, or Explorer/another program has it open and locked (Windows can't overwrite a running exe) | Close all running `orcad2kicad*.exe` windows/processes, then rebuild. If you're not sure which process has it locked, search Task Manager for `orcad2kicad` and end it. |

## 12. Known Limitations

An updated list as of the introduction of the `--dsn`/`--kicad-project` input modes.

- **The `--dsn`/`--kicad-project` input modes don't support placeholder parts, footprint choice,
  or pin-type overrides** (see Chapter 0, "Converting Without OrCAD") — these are only supported
  with `edf` input.
- **412 remaining ERC violations from the nightly import result** (`endpoint_off_grid` 122,
  `unconnected_wire_endpoint` 177, `pin_not_connected` 63, `same_local_global_label` 26, etc.) are
  left untouched — they're the result of the KiCad importer itself, and root-cause analysis is a
  future task.
- **A footprint that only exists on the back (B.Cu) is flipped from its back-side instance into a
  front-side definition when added to the library** (shown in the issue list as `… derived from
  back-side <REF> (flipped to front)`). Pad/silkscreen coordinates and layers are restored to
  KiCad convention, so it's usable directly with Update PCB, but special pads (e.g. a chamfered-
  corner pad's corner designation) have no reviewed cases yet. Check it in the KiCad footprint
  editor if it looks off.
- **Missing pad numbers for parts like H1~H4** — some parts from the PADS board import come in
  with an empty pad-number string, producing a warning that it can't be matched to the schematic
  pin number (no net on that pin, so it doesn't affect routing).
- **Board-only-part auto-add is a human decision** — parts like J19/J20 are never automatically
  added to the schematic (a person must specify it explicitly via the GUI "Board Diff" tab or
  `--add-board-part`).
- **Many DRC unconnected warnings when the PADS ASCII export lacks copper pour** (161 in the
  sample) — not a routing error, but caused by the export missing the polygon definition itself
  (`POUR_OUTLINES 0`). Re-export with copper pour included as described in section 4.4, or fill it
  in directly in KiCad, for DRC to be trustworthy.
- ~~Undecoded `%<decimal>%` byte escapes in OrCAD free text/symbol graphic text~~ →
  **Resolved in Phase 3-B (the manual's description was outdated).** `decode_orcad_text` gathers
  consecutive `%NN%` sequences and decodes them as cp949 (falling back to latin-1), converting
  CR/LF back into line breaks — no escapes remain in the sample schematic. Added 2026-09-09: C0
  control characters other than tab/newline (e.g. `%0%` NUL) are dropped instead of kept, since
  leaving them in would break the `.kicad_sch`.
- **Mirrored EDIF display orientations (MX/MY/MXR90/MYR90) text direction unverified** — only the
  rotation component is reflected, and mirroring is deliberately ignored, but there's no case of
  this in the sample, so it hasn't been confirmed against actual rendering.
- **The text-size factor 0.6 is an empirical value** — obtained by matching the original cap-height
  ratio measured from PDF output (about 8.2 EDIF units); there's room to re-tune it, but this
  hasn't been touched in this phase.
- **Automatic net-label placement in `net_names=keep` mode** — labels are placed at wire endpoints
  (near component pins), which can visually overlap parts (a visual issue only, with no effect on
  connectivity). Improving label placement is a future task.
- **Instance-level `INVISIBLEPIN` overrides aren't reflected** — only cell (library)-level hidden
  values are reflected; per-instance exceptions aren't read.
- **GUI result storage is by reference** — the worker thread hands the last `PipelineResult` to the
  Tk thread via a queue, which then holds a reference to it. (**No lock is used** — an earlier
  version's description of "serialized via a lock" was inaccurate. The result object is only ever
  read and written on the Tk thread; the worker thread only puts it into a `queue.Queue`, and
  `after(100,...)` polling (`drain_queue`) pulls it out and applies it. Re-running while a run is in
  progress is blocked by the `running` flag.) The remaining limitation is only lifetime — converting
  a large design repeatedly in a row can briefly accumulate memory since the previous result
  reference isn't released immediately.
- **The MCP `convert` tool can only run one at a time** (synchronous execution) — if multiple
  conversions are requested at once, later ones wait for the earlier one to finish.
- **The placeholder page (`99-PCB-ONLY`) uses a fixed A4 grid layout** — if there are a great many
  board-only parts, they can overlap or get cut off on a single page (no automatic layout
  adjustment).
- ~~Adding a reference already in the schematic to `add_board_only_parts` doesn't prevent duplicate
  addition~~ → **Corrected and fixed on 2026-09-09.** Duplicate placement was never actually
  performed — such a reference was always skipped, leaving only the issue
  `placeholder part <REF>: already in the schematic; skipped`. However, there was a bug where even
  the skipped reference's net list was still augmented for [3]/[4] baseline purposes, which could
  make verification FAIL incorrectly; this is now filtered out. It's safe to pass an already-present
  reference to `--add-board-part` (only the message above appears in the issue list).
- ~~Duplicate `augment_reference` import in `agents.py`~~ → **This was a misattributed description.**
  The duplicate was actually in `pipeline.py`, and was merged into one place on 2026-09-09.
- **`run_agents`'s pin-type suggestions are unselected by default** — note that when scripting
  automatic bulk-apply, nothing gets applied unless you explicitly set `selected=True`.
- **The `ApiBackend` (Anthropic API) live path is unverified** — there's no API key on this
  machine, so the actual HTTP round-trip hasn't been confirmed (unit tests cover it via
  `FakeBackend`). Recommended to verify once in an environment with an API key.
- **exe (especially GUI) window rendering is unverified in a headless environment** — packaging
  itself (build success, file sizes, `--selftest` behavior) has been confirmed, but whether the
  window actually renders correctly needs to be checked once by hand on a PC with a real display.
- **Possible `sanitize_fp_name` name collisions** (carried over from Phase 3-A) — if two different
  footprint IDs become the same filename after forbidden-character substitution, the later one
  silently overwrites the earlier one. Not reproduced in this sample (35 footprints).
- **Regression testing hasn't been expanded to other OrCAD projects** — all verification so far is
  based on a single sample board. These items may actually surface on other designs, so be sure to
  do a visual check in KiCad following the Chapter 10 procedure the first time you use it on a new
  one.

## 13. Appendix

### 13.1 Output folder structure (`-o out/kicad` example, with `--board`+`--pdf` both given)

```
out/kicad/
├── MYBOARD.kicad_pro                      # KiCad project file (double-click to open)
├── MYBOARD.kicad_sch                      # root schematic (includes sub-sheet symbols)
├── MYBOARD.kicad_prl                      # project-local settings (generated by kicad-cli)
├── 00-MCU.kicad_sch                       # sub-sheets (pages) — named after the design's pages
├── 10-LIMIT SENSORS.kicad_sch
├── ... (one per design page)
├── 99-PCB-ONLY.kicad_sch                  # only appears if board-only parts were added (section 5.3)
├── orcad.kicad_sym                        # converted symbol library
├── sym-lib-table                          # registers the above library with the project
├── MYBOARD.kicad_pcb                      # only appears with --board (board import result)
├── PADS.pretty/                           # only appears with --board (footprints extracted from the board, .kicad_mod)
├── fp-lib-table                           # registers the above footprint library with the project
├── pads_import/                           # intermediate kicad-cli import output when --board is a .asc
├── erc.json                               # kicad-cli ERC results (violation list, by type)
├── kicad_netlist.txt                      # netlist extracted by kicad-cli (input to 3rd-stage verification)
└── MYBOARD.pdf                            # only appears with --pdf (schematic PDF)
```

`out/` (relative to the repo root) is gitignored and never committed — to see results for the first
time on another PC, regenerate them with the commands in section 6.2.

### 13.2 Output folder structure (`--dsn`/`--kicad-project` path, with `--board`+`--pdf` both given)

`.DSN`/KiCad project input produces mostly the same kinds of files as the EDIF path, but the symbol
library is extracted from what was embedded in the schematic (`orcad_import.kicad_sym`), sub-sheet
names use the `P01_`/`P02_` prefix, and there's an additional `.DSN` import log:

```
out/dsn/
├── MYBOARD.kicad_pro                      # KiCad project file (double-click to open)
├── MYBOARD.kicad_sch                      # root schematic (result of the Chapter 0 "restructured into stable KiCad 10 format" step, includes sub-sheet symbols)
├── P01_<first page name>.kicad_sch        # the nightly importer's first sheet, renamed as it's restructured into a sub-sheet
├── P02_<page name>.kicad_sch              # subsequent pages (sequence prefix added during restructuring)
├── ... (one per design page)
├── orcad_import.kicad_sym                 # library extracted from the symbol definitions embedded in the schematic
├── sym-lib-table                          # registers the above library with the project
├── MYBOARD.kicad_pcb                      # only appears with --board (board import result, imported with the stable kicad-cli)
├── PADS.pretty/                           # only appears with --board (footprints extracted from the board, .kicad_mod)
├── fp-lib-table                           # registers the above footprint library with the project
├── pads_import/                           # intermediate kicad-cli import output when --board is a .asc
├── erc.json                               # kicad-cli ERC results (violation list, by type)
├── kicad_netlist.txt                      # netlist extracted by kicad-cli (input to [3] if --netlist was given, otherwise the baseline for [4])
├── dsn_import.log                         # raw output of the kicad-cli import (only appears with --dsn input; may contain non-ASCII text depending on locale)
└── MYBOARD.pdf                            # only appears with --pdf (schematic PDF)
```

`--kicad-project` input (using an already-imported project as-is) differs only in that
`dsn_import.log` is absent. With `--nightly-format` given, restructuring is skipped, so sub-sheet
names keep whatever the nightly importer originally assigned instead of the `P01_`/`P02_` prefix.

### 13.3 Glossary

| Term | Meaning |
|---|---|
| EDIF | Electronic Design Interchange Format. The standard format OrCAD Capture uses to export a schematic as text (this tool only handles version 2.0.0). |
| REF.PIN | The "reference.pin number" form (e.g. `R1.2`). The smallest unit for net comparison. |
| Net | A set of pins that are all electrically at the same potential. |
| Verification stages 1/2/3/4 | See the table in Chapter 7. |
| ERC | Electrical Rules Checker. A KiCad feature that checks a schematic for electrical rule violations (unconnected pins, undriven power, etc.). |
| DRC | Design Rules Checker. A KiCad feature that checks a PCB for physical rule violations (clearance, unconnected items, etc.). |
| Copper pour | A large area of copper on the PCB (usually a GND/power plane). Defined as a polygon — if it's missing from an export, every pad meant to connect through that area shows up as unconnected (sections 4.4, 12). |
| Footprint / decal | The physical land pattern of a part. KiCad calls it a "footprint," PADS calls it a "decal" — this tool treats them as the same concept. |
| `Resolutions` | The internal data structure holding a user's decisions about board/schematic mismatches (the 4 fields in the section 5.3 table + pin-type overrides). |
| `PipelineOptions` / `PipelineResult` | The input/output types of `pipeline.run_pipeline()`. CLI options, GUI widget values, and MCP tool arguments are all converted to and from these two types. |
| Suggestion | A single suggestion an AI agent produces (whether it's applied is decided by a person, via the `selected` field). |
| Backend | The target an agent delegates the actual LLM call to (`none`/`api`/`claude-cli`/`codex-cli`, Chapter 8). |
| MCP | Model Context Protocol. The standard protocol that lets an external AI tool call this program's functionality as a "tool" (Chapter 9). |

## Contact

Development inquiries: Nerdvana Inc. (www.nerdvana.co.kr)
