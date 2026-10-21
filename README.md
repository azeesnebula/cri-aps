# C.R.I Pumps — Production Planning

Dedicated advanced planning and scheduling workspace for **C.R.I Pumps**, built on frePPLe. The supplied `cri-logo.gif` is the source brand asset.

## Run

Open PowerShell in this folder, with Docker Desktop running:

```powershell
docker compose up -d --build
docker compose logs -f web
```

Open **http://localhost:8081** once the web service is healthy. A fresh frePPLe installation uses `admin` / `admin`; set your own password after signing in. Startup creates an empty C.R.I database, without loading demo companies or the previous customer's workbooks.

Optional configuration is documented in `.env.example`. Copy it to `.env` to set `CRI_PORT` or `CRI_DB_PASSWORD` before first startup.

```powershell
docker compose stop                 # stop; retain company data
docker compose up -d                # restart
docker compose up -d --build web    # deploy source/UI changes
docker compose exec web frepplectl check
```

The Compose project `cri-pumps` owns its network and database volume. Its database is private to that network; the web service uses port 8081. This folder can be moved to another location and the same commands used there.

## Project layout

| Path | Purpose |
|---|---|
| `cri_ui/` | C.R.I Pumps Django application, planning screens and tests |
| `cri_ui/static/cri_ui/` | Brand theme, manifest and supplied logo |
| `cri_ui/templates/admin/` | Shared page branding and sign-in screen |
| `freppledb/` | Vendored frePPLe application source, including existing workbook fixes |
| `src/`, `include/`, `test/`, `doc/` | Planning-engine source, upstream tests and documentation |
| `Dockerfile`, `compose.yaml`, `docker/` | Reproducible isolated runtime using the prebuilt planning engine |
| `djangosettings.py` | C.R.I application registration, database and theme configuration |

The historical `sieger_ui` **template/static namespaces, CSS selectors and URL names** are retained internally for compatibility with the existing planning screens. The installed Python app is `cri_ui`; all company-facing branding comes from the C.R.I theme. These internal names are not another customer workspace.

## Brand system

- Supplied red/charcoal/white logo, displayed without recoloring or stretching.
- Bright logo red for identity; a deeper accessible red for buttons and links.
- Cool white/gray surfaces, charcoal text, bold Arial/Helvetica headings, readable system sans-serif body text and tabular figures.
- Matching light, dark and system appearance modes, keyboard focus and reduced-motion support.
- One shared token system applies across planning, reports, grids, forms and authentication.

## Company data and integration

### Pump-factory planning scenario

The local C.R.I instance contains the **19 October 2026 planning scenario**: 10 shipments, 20 order lines and 670 pumps. Input workbooks, schedule exports, sources, assumptions and validation results are in [`scenarios/2026-10-19/`](scenarios/2026-10-19/README.md). This is demonstration data, not C.R.I's actual factory or customer records.

### Real company inputs

Load C.R.I products, BOMs, machines, routings, calendars and orders using **Admin → Execute** or the reference-data screens. The inherited planning-workbook inspector expects the documented numbered Infor-LN tab format; a different C.R.I ERP format needs an explicit mapping. Existing production logic and assumptions should be validated against C.R.I's actual manufacturing process before using a plan operationally.

Previous-company workbooks, exports, screenshots, loader scripts and operational runbooks remain in the parent workspace as historical material. They are not inputs to this deployment. The parent `.git` retains the existing history and staged work.

## Checks

```powershell
docker compose exec web frepplectl check
docker compose exec -e PYTHONPATH=/etc/frepple/apps web /usr/share/frepple/venv/bin/python -m unittest discover -s /etc/frepple/apps/cri_ui/tests -v
```

See `COPYING` and `AUTHORS` for upstream licenses and attribution. The supported runtime here is Docker: building the C++ engine from source requires the upstream build/packaging toolchain.
