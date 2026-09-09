# orcad2kicad Quick Start (essentials only)

Provided by Nerdvana Inc. — https://www.nerdvana.co.kr · Downloads/docs: https://github.com/nerdvanainc/orcad2kicad

The shortest path from a single OrCAD `.DSN` to a fully verified stable KiCad 10.0 project. See the
full manual for details on each topic.

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

For more detail, see `User_Manual.en.md` in the same folder (Chapter 0: Converting without OrCAD,
Chapter 5: GUI, Chapter 6: CLI, Chapter 7: Reading verification results).

## Contact

Development inquiries: Nerdvana Inc. (www.nerdvana.co.kr)
