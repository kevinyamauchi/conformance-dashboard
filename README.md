# OME-Zarr 

[!NOTE]
This is a work-in-progress and the results are mock results for testing.

This generates a dashboard webpage to communicate the current state of spec support of IO and validator libraries across the OME-Zarr ecosystem. The webpage is a static site generated from YAML files reporting conformance testing results of tools and served with Github pages.

## How to add your tool to the table

All of the conformance tests are self-reported by the tool developers. If you would like your tool to be included, you need to run the coformance test and generate a ConformanceResult YAML file (described below). You then need to either add the YAML file or the URL from which the YAML file can be fetched to this repo. 

```yaml
id: my-library
name: My Library
version: "1.4.2"
homepage: "https://github.com/me/my-library"
values:
  v0.4-attributes: 100
  v0.4-zarr: 100
  v0.5-attributes: 80
  v0.5-zarr: 90
  v0.6-attributes: 40
  v0.6-zarr: 0
  # v0.6-transforms omitted: not tested yet, renders as "–"
```

- `id`, `name`, and `version` are required; `homepage` is optional but
  must be a valid `http`/`https` URL if present. `version` is the
  library's own release version (whatever was tested to produce these
  results) — not a spec version, which is what `values` covers.
- `values` keys must match a test set `id` nested under a version in
  `columns.yaml` (e.g. `v0.6-transforms`, not `v0.6`). Omit a key
  entirely if that combination hasn't been tested — it renders as unknown
  ("–") rather than 0%.


### Adding your conformance test result to the repo

You can simply make a PR adding your ConformanceResult YAML file to `conformance_results/local/`. Note that you must choose a unique id value that is not shared by any others. In your PR, the `ci.yml` Github Actions workflow will build the site to confirm your ConformanceResult YAML file can be parsed. Upon merging to main the site will rebuild and your tool will be visible.

### Adding a remote conformance test result

If you would like the web site builder to fetch the conformance result from a URL, you can make a PR adding the URL to the repo. This could be useful if you are running conformance tests and generating the ConformanceResult YAML file on CI. You can add the URL to `conformance_results/remote-sources.yaml`. In your PR, the `ci.yml` Github Actions workflow will build the site to confirm your ConformanceResult YAML file can be parsed. Upon merging to main the site will rebuild and your tool will be visible.

## How to build the website

The build script contains PEP 723 metadata and can be run with uv:

```bash
uv run --script scripts/build_site.py
```

This produces two outputs:
- index.html: this is the website
- data/conformance_results.json: the table of conformance testing results seralized to a JSON file.

## How to edit the website

## config.yaml

This sets the site-wide appearance such as font colors and the page title.

## Text

You can change the main text below the site title by editing the main_text.md. You can change the footer text by editing footer.md. Both of these files are converted from markdown to HTML with the [markdown](https://github.com/Python-Markdown/markdown) library.

## JINJA template

The index.html page is generated using a JINJA template. If you would like to make adjustments to the layout or other parts of the site not available through the config or markdown files, you can edit the JINJA template at: `templates/index.html.j2` and the CSS at style.css.


## Github actions workflows

We use Github actions to build and deploy the website.

- **`build.yml`** is a reusable workflow that installs
  `uv`, runs `uv run --script scripts/build_site.py`, and uploads
  `data/conformance_results.json` + `index.html` as a build artifact.
- **`ci.yml`** calls `build.yml` on every pull request to confirm that the website builds without errors. It does not publish the website.
- **`deploy.yml`** also calls `build.yml`, then assembles the downloaded
  `data/conformance_results.json` + `index.html` together with the static `style.css`
  into a `_site/` directory and publishes it straight to GitHub Pages via  GitHub's
  Actions-based Pages deployment (
  `actions/upload-pages-artifact` + `actions/deploy-pages`). It runs on push to `main`, on an hourly chron job, and on manual dispatch.


## Adding or changing table columns

`columns.yaml` has two levels: a list of spec **versions**, each with a list
of **tests** that were run against that version. The table reflects this directly —
each version gets one top-level column spanning its tests, e.g. `v0.6`
spans `attributes` / `zarr` / `transforms` underneath it. Not every version
needs the same tests (e.g., `v0.4` and `v0.5` currently only have
`attributes`/`zarr`, while `v0.6` adds `transforms`).

A version needs `id` and `label`, plus a `tests` list. Each test needs
`id` (the flat identifier tool YAML files reference under `values:`, e.g.
`v0.6-transforms`), `label`, and `type`:

| type         | value format        | rendering                                  |
|--------------|----------------------|---------------------------------------------|
| `boolean`    | `true` / `false`     | green "Y" / red "N" chip (unset -> gray "–") |
| `percentage` | number `0`–`100`     | color gradient chip with a fill bar          |

To add a new spec version, add a new version block to `columns.yaml`. To
add a test to an existing version, add it to that version's `tests` list.
Either way, existing tool YAML files just won't have a value for the new
test id until their maintainers add one — it renders as untested ("–")
until then.
