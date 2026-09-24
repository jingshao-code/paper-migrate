# assets/templates

Unmodified copies of **official** conference style packages, one folder per venue+year, so a
migration can run offline. Each zip is recorded in `../venues.yaml` under
`venues.<id>.official.template_zip` / `template_zip_sha256` together with the URL it was
downloaded from and the date. `scripts/verify_template.py --zip` checks the hash before
extracting, so a tampered or outdated copy is refused; `scripts/profile_template.py --dir`
reads what the package itself requires.

| folder | source | note |
|---|---|---|
| `iclr2027/iclr-2027-style-files.zip` | https://media.iclr.cc/Conferences/ICLR2027/iclr-2027-style-files.zip (2026-09-23) | same files as github.com/ICLR/Master-Template |
| `neurips2026/Formatting_Instructions_For_NeurIPS_2026.zip` | https://media.neurips.cc/Conferences/NeurIPS2026/Formatting_Instructions_For_NeurIPS_2026.zip (2026-09-23) | neurips_2026.sty + sample + checklist.tex |
| (ACL) not bundled | `git clone https://github.com/acl-org/acl-style-files` (commit recorded in venues.yaml) | the repository is the official channel |
| (AAAI) not bundled | https://aaai.org/authorkit27/ | distributed behind AAAI's own page; supply it yourself |

Rules of the folder:

* A bundled zip is a convenience copy, not the source of truth. The agent must still confirm the
  official page has not published a newer package (`verified_on` in `venues.yaml`) and prefer a
  template the user downloaded today when one is supplied.
* Never edit a zip in place. A new version gets a new folder (`iclr2028/`) and a new manifest entry.
* Only packages whose publisher allows redistribution are stored here.
* Rules (page limits, required statements, captions, anonymity) live in `venues.yaml`, never here.
