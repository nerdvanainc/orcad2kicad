# orcad2kicad Notice

Made by / copyright: © 2026 Nerdvana Inc., https://www.nerdvana.co.kr

This document collects the notices about the scope of use, trademarks and responsibility for orcad2kicad. The license itself is [LICENSE](LICENSE) (MIT).

## OrCAD, trademarks, and scope of responsibility

- **This software does not read or convert OrCAD `.DSN` files itself.** Parsing of `.DSN` (a proprietary
  Cadence binary format) is done entirely by KiCad's own OrCAD importer (kicad-cli, nightly 10.99+) as
  distributed by the KiCad project; orcad2kicad only runs that kicad-cli and then cleans up and verifies its
  output (KiCad files). orcad2kicad contains no code that interprets the `.DSN` format. Consequently the
  fidelity of a `.DSN` import (omissions, distortions) is determined by KiCad's importer, and nightly builds
  are unstable builds of the KiCad project.
- **No OrCAD installation, license, library, DLL, or any other Cadence/OrCAD file is required or
  included.** This project has not reverse-engineered any Cadence file format. The only formats it reads are
  EDIF 2.0.0 (an EIA open standard, as a text file the user exports from OrCAD), PADS ASCII (a documented
  text export produced by the user), IPC-D-356 (an IPC standard) and KiCad S-expressions (an open format).
- **Rights to the input and responsibility for the output rest with the user.** You must hold the rights to
  the design files you convert and to the symbol/library graphics they contain (including what ends up in
  the extracted `orcad_import.kicad_sym`), and how the converted result is used is your responsibility.
- **Verification is not a guarantee.** The tool's "verification" is a comparison against the reference
  netlist you supply; it does not certify the correctness of the design or its fitness for manufacturing.
  Final review of the schematic and board remains your responsibility (see the no-warranty clause of the
  license).
- **About the name.** "orcad2kicad" merely describes the file formats the tool handles (files exported from
  OrCAD → a KiCad project) and implies no affiliation or endorsement. This project does not use the logos
  of Cadence, Siemens, KiCad or Raspberry Pi.
- **Trademarks.** OrCAD, Allegro and Cadence are trademarks of Cadence Design Systems, Inc. PADS is a
  trademark of Siemens Industry Software Inc. KiCad is a trademark of the KiCad project. IPC-D-356 is a
  standard of IPC. Raspberry Pi is a trademark of Raspberry Pi Ltd. This project is not affiliated with or
  endorsed by any of them.

## Network and data

- No telemetry. The only network access is (1) downloading the KiCad nightly build or a 7-Zip console build
  when you explicitly request it, and (2) calls to an AI backend you have configured and enabled yourself.
- When an AI backend is enabled, parts of your design data — net names, pin names, references, footprint
  names — are sent to the selected service, and that service's terms and privacy policy apply. For
  confidential designs, check your organization's policy first. With the default (no backend) nothing is sent.
- KiCad nightly and 7-Zip downloads come from each project's official distribution servers; their integrity
  and safety are the responsibility of those distributors, and it is up to you to confirm that such downloads
  are permitted by your organization's policies. Neither is redistributed inside orcad2kicad; each is governed
  by its own license (GPLv3, LGPL) — see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Source of the example screenshots

The example circuit shown in the README and website screenshots is a conversion of the publicly released
Raspberry Pi Compute Module IO Board V3 design files (`RPI-CMIO-V3_0-PUBLIC.DSN`, © 2015 Raspberry Pi
(Trading) Ltd). The design files are not included in this repository; Raspberry Pi Ltd is not affiliated with
and does not endorse this tool.
