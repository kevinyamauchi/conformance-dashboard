#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pyyaml>=6",
#     "pydantic>=2",
#     "jinja2>=3",
#     "markdown>=3",
# ]
# ///
"""Build the OME-Zarr conformance dashboard webpage.

Pipeline:
  1. Load and validate columns.yaml (ColumnsFile).
  2. Load config.yaml (SiteConfig) and render the main_text.md/footer.md
     files to HTML. All three are optional. a missing config.yaml
     falls back to default styling. Missing main_text.md/footer.md
     files just omit the block.
  3. Load and validate the remote tools from
     conformance_results/remote-sources.yaml (RemoteSourcesFile), then
     fetch each URL and validate the result (ConformanceResult). A
     failure to fetch or parse/validate a single URL is logged via
     stderr and skipped, but two different remote sources reporting the
     same id is an error that stops the build.
  4. Load and validate local tools from conformance_results/local/*.yaml
     (ConformanceResult). Failures here raise an error and stop the
     build.
  5. Merge remote and local results (merge_tools). If multiple results share
     a tool id, this raises an error and stops the build.
  6. Write data/conformance_results.json. The merged table is serialized
     to a JSON file so the results can be analyzed programmatically.
  7. Render index.html from templates/index.html.j2.

Run manually with: uv run --script scripts/build_site.py

This also runs automatically via .github/workflows/build.yml, called
from ci.yml (validation) and deploy.yml (publishing).
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import markdown
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import ValidationError

from models import ColumnsFile, ConformanceResult, RemoteSourcesFile, SiteConfig, VersionGroup

ROOT = Path(__file__).resolve().parent.parent
COLUMNS_FILE = ROOT / "columns.yaml"
CONFIG_FILE = ROOT / "config.yaml"
MAIN_TEXT_FILE = ROOT / "main_text.md"
FOOTER_FILE = ROOT / "footer.md"
LOCAL_TOOLS_DIR = ROOT / "conformance_results" / "local"
REMOTE_SOURCES_FILE = ROOT / "conformance_results" / "remote-sources.yaml"
DATA_OUTPUT_FILE = ROOT / "data" / "conformance_results.json"
TEMPLATE_DIR = ROOT / "templates"
HTML_OUTPUT_FILE = ROOT / "index.html"

FETCH_TIMEOUT_SECONDS = 15
MAX_REMOTE_RESPONSE_BYTES = 1_000_000  # 1 MB; a tool entry has no reason to be larger


def load_columns(columns_file: Path = COLUMNS_FILE) -> list[VersionGroup]:
    """Load and validate columns.yaml.

    Parameters
    ----------
    columns_file : Path
        Path to the columns YAML file.

    Returns
    -------
    list[VersionGroup]
        The spec versions defined in `columns_file`, in file order, each
        with its nested test-type columns.
    """
    with open(columns_file) as f:
        raw = yaml.safe_load(f)
    return ColumnsFile.model_validate(raw).columns


def load_config(config_file: Path = CONFIG_FILE) -> SiteConfig:
    """Load and validate config.yaml, if present.

    Parameters
    ----------
    config_file : Path
        Path to the site config YAML file.

    Returns
    -------
    SiteConfig
        The site's title/color configuration. If `config_file` doesn't
        exist, this is SiteConfig's all-default instance.
    """
    if not config_file.exists():
        return SiteConfig()
    with open(config_file) as f:
        raw = yaml.safe_load(f)
    return SiteConfig.model_validate(raw or {})


def render_markdown_file(path: Path) -> str | None:
    """Render a local Markdown file to HTML.

    Trusted the same way conformance_results/local/*.yaml is: only
    someone with repo write access can change these files, so the
    rendered HTML is passed to the template with the `safe` filter
    instead of being escaped.

    Parameters
    ----------
    path : Path
        Path to the Markdown file to render.

    Returns
    -------
    str or None
        The rendered HTML, or None if `path` doesn't exist.
    """
    if not path.exists():
        return None
    return markdown.markdown(path.read_text())


def fetch_url_safely(
    url: str,
    timeout_seconds: int = FETCH_TIMEOUT_SECONDS,
    max_response_bytes: int = MAX_REMOTE_RESPONSE_BYTES,
) -> bytes:
    """Fetch a URL with basic SSRF/resource-exhaustion guards.

    Refuses to follow a redirect to a different host than requested, so
    a URL approved by PR review can't later 302 somewhere else. Caps the
    response body size before it ever reaches the YAML parser, to bound
    "YAML bomb" style resource exhaustion.

    Parameters
    ----------
    url : str
        The URL to fetch.
    timeout_seconds : int
        How long to wait for the request to complete before raising
        TimeoutError.
    max_response_bytes : int
        The largest response body to accept.

    Returns
    -------
    bytes
        The response body.

    Raises
    ------
    ValueError
        If the response redirects to a different host than `url`, or if
        the response body exceeds `max_response_bytes`.
    urllib.error.URLError
        If the request itself fails (DNS, connection, HTTP error, etc.).
    TimeoutError
        If the request doesn't complete within `timeout_seconds`.
    """
    requested_host = urllib.parse.urlparse(url).hostname
    request = urllib.request.Request(url, headers={"User-Agent": "compat-matrix-build/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        final_host = urllib.parse.urlparse(response.geturl()).hostname
        if final_host != requested_host:
            raise ValueError(
                f"redirected from host '{requested_host}' to '{final_host}'; refusing to follow"
            )
        body = response.read(max_response_bytes + 1)
        if len(body) > max_response_bytes:
            raise ValueError(f"response exceeds {max_response_bytes} byte limit")
        return body


def load_remote_tools(
    remote_sources_file: Path = REMOTE_SOURCES_FILE,
    timeout_seconds: int = FETCH_TIMEOUT_SECONDS,
    max_response_bytes: int = MAX_REMOTE_RESPONSE_BYTES,
) -> dict[str, ConformanceResult]:
    """Load and validate all tools listed in conformance_results/remote-sources.yaml.

    Each URL is fetched, safe_load()'d, and validated independently. A
    URL that fails to fetch, fails to parse, or fails validation is
    skipped - a WARNING is printed to stderr, but the build continues.
    Two different remote sources claiming the same id raises an error.

    Parameters
    ----------
    remote_sources_file : Path
        Path to the YAML file listing remote source URLs.
    timeout_seconds : int
        Forwarded to `fetch_url_safely` for each URL.
    max_response_bytes : int
        Forwarded to `fetch_url_safely` for each URL.

    Returns
    -------
    dict[str, ConformanceResult]
        Successfully loaded remote results, keyed by id. Empty if
        `remote_sources_file` doesn't exist.

    Raises
    ------
    ValueError
        If two different remote sources report the same id.
    """
    tools: dict[str, ConformanceResult] = {}
    if not remote_sources_file.exists():
        return tools

    with open(remote_sources_file) as f:
        raw = yaml.safe_load(f)
    remote_sources = RemoteSourcesFile.model_validate(raw or {"sources": []})

    for url in remote_sources.sources:
        url_str = str(url)
        try:
            body = fetch_url_safely(url_str, timeout_seconds, max_response_bytes)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            print(f"WARNING: failed to fetch {url_str}: {exc}", file=sys.stderr)
            continue

        try:
            raw_tool = yaml.safe_load(body)
            tool = ConformanceResult.model_validate(raw_tool)
        except (yaml.YAMLError, ValidationError) as exc:
            print(f"WARNING: skipping invalid data from {url_str}: {exc}", file=sys.stderr)
            continue

        if tool.id in tools:
            raise ValueError(
                f"id '{tool.id}' from {url_str} is already claimed by another "
                f"remote source in conformance_results/remote-sources.yaml; "
                f"remote source ids must be unique"
            )

        tools[tool.id] = tool

    return tools


def load_local_tools(
    local_tools_dir: Path = LOCAL_TOOLS_DIR,
) -> dict[str, ConformanceResult]:
    """Load and validate all tools in conformance_results/local/*.yaml.

    Unlike `load_remote_tools`, a validation failure here raises and
    stops the build rather than being skipped - this is the site
    maintainer's own content, so a mistake should be caught immediately.

    Parameters
    ----------
    local_tools_dir : Path
        Directory containing one YAML file per locally-maintained tool.

    Returns
    -------
    dict[str, ConformanceResult]
        Loaded local results, keyed by id. Empty if `local_tools_dir`
        doesn't exist.
    """
    tools: dict[str, ConformanceResult] = {}
    if not local_tools_dir.exists():
        return tools

    for path in sorted(local_tools_dir.glob("*.yaml")):
        with open(path) as f:
            raw_tool = yaml.safe_load(f)
        tool = ConformanceResult.model_validate(raw_tool)
        tools[tool.id] = tool

    return tools


def merge_tools(
    remote_tools: dict[str, ConformanceResult], local_tools: dict[str, ConformanceResult]
) -> list[ConformanceResult]:
    """Merge remote and local conformance results into one sorted list.

    Parameters
    ----------
    remote_tools : dict[str, ConformanceResult]
        Results from `load_remote_tools`, keyed by id.
    local_tools : dict[str, ConformanceResult]
        Results from `load_local_tools`, keyed by id.

    Returns
    -------
    list[ConformanceResult]
        All results from both sources, sorted by name (case-insensitive).

    Raises
    ------
    ValueError
        If one or more ids are defined by both `remote_tools` and
        `local_tools`.
    """
    overlapping_ids = set(remote_tools) & set(local_tools)
    if overlapping_ids:
        raise ValueError(
            f"id(s) {sorted(overlapping_ids)} are defined by both a local file "
            f"in conformance_results/local/ and a remote source in "
            f"conformance_results/remote-sources.yaml; remove the duplicate "
            f"from one side"
        )
    combined = {**remote_tools, **local_tools}
    return sorted(combined.values(), key=lambda t: t.name.lower())


def render_boolean_cell(value: bool | None) -> dict:
    """Render a boolean cell value to its display representation.

    Parameters
    ----------
    value : bool or None
        True/False for a pass/fail result, or None if untested.

    Returns
    -------
    dict
        A chip cell descriptor for the template: green "Y" for True, red
        "N" for False, gray "-" for None.
    """
    if value is True:
        return {"kind": "chip", "css_class": "chip--yes", "text": "Y"}
    if value is False:
        return {"kind": "chip", "css_class": "chip--no", "text": "N"}
    return {"kind": "chip", "css_class": "chip--unknown", "text": "–"}


def render_percentage_cell(value: float | None) -> dict:
    """Render a percentage cell value to its display representation.

    Parameters
    ----------
    value : float or None
        A number from 0-100, or None if untested.

    Returns
    -------
    dict
        A gauge cell descriptor for the template if `value` is not None
        (clamped to 0-100, with a red-to-green hue and a rounded percent
        label), otherwise a gray "-" chip cell descriptor.
    """
    if value is None:
        return {"kind": "chip", "css_class": "chip--unknown", "text": "–"}
    pct = max(0.0, min(100.0, float(value)))
    hue = 10 + (pct / 100) * 130  # 10 = red, 140 = green
    return {
        "kind": "gauge",
        "bg": f"hsl({hue:.0f}, 45%, 91%)",
        "accent": f"hsl({hue:.0f}, 55%, 36%)",
        "pct": round(pct),
        "text": f"{round(pct)}%",
    }


CELL_RENDERERS = {
    "boolean": render_boolean_cell,
    "percentage": render_percentage_cell,
}


def build_dataset(column_groups: list[VersionGroup], tools: list[ConformanceResult]) -> dict:
    """Assemble the data/conformance_results.json dataset.

    Also checks each tool's `values` keys against the known column ids
    and prints a WARNING to stderr for any that don't match a column
    defined in columns.yaml. Any values that don't match
    are ignored rather than failing the build,

    Parameters
    ----------
    column_groups : list[VersionGroup]
        The spec versions and their nested test columns, from
        `load_columns`.
    tools : list[ConformanceResult]
        The merged, sorted conformance results, from `merge_tools`.

    Returns
    -------
    dict
        The full dataset written to data/conformance_results.json:
        `generated_at`, the column hierarchy, and the tool results.
    """
    column_ids = {test.id for group in column_groups for test in group.tests}
    for tool in tools:
        for version_entry in tool.versions:
            unknown_keys = set(version_entry.values) - column_ids
            if unknown_keys:
                print(
                    f"WARNING: tool '{tool.id}' version '{version_entry.version}' has "
                    f"values for undefined column(s) {unknown_keys}; they will be ignored",
                    file=sys.stderr,
                )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "columns": [g.model_dump(mode="json") for g in column_groups],
        "tools": [t.model_dump(mode="json") for t in tools],
    }


def write_data_json(dataset: dict, output_file: Path = DATA_OUTPUT_FILE) -> None:
    """Write the dataset to data/conformance_results.json as indented JSON.

    Parameters
    ----------
    dataset : dict
        The dataset built by `build_dataset`.
    output_file : Path
        Path to write the JSON to. Its parent directory is created if it
        doesn't already exist.
    """
    output_file.parent.mkdir(exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(dataset, f, indent=2)
        f.write("\n")
    print(f"Wrote {len(dataset['tools'])} tool(s) to {output_file}")


def render_html(
    column_groups: list[VersionGroup],
    tools: list[ConformanceResult],
    generated_at: str,
    config: SiteConfig,
    main_text_html: str | None,
    footer_html: str | None,
    template_dir: Path = TEMPLATE_DIR,
    output_file: Path = HTML_OUTPUT_FILE,
) -> None:
    """Render index.html from templates/index.html.j2.

    Parameters
    ----------
    column_groups : list[VersionGroup]
        The spec versions and their nested test columns, from
        `load_columns`.
    tools : list[ConformanceResult]
        The merged, sorted conformance results, from `merge_tools`.
    generated_at : str
        An ISO 8601 timestamp string, shown in the page footer.
    config : SiteConfig
        The site's title/color configuration, from `load_config`.
    main_text_html : str or None
        Rendered HTML for the text under the title, from
        `render_markdown_file`, or None to omit that block.
    footer_html : str or None
        Rendered HTML for the footer text, from `render_markdown_file`,
        or None to omit that block.
    template_dir : Path
        Directory containing index.html.j2.
    output_file : Path
        Path to write the rendered HTML to.
    """
    # Flattened in the same version-then-test order the header groups are
    # rendered in, so each row's cells line up under the right column.
    # group_end marks the last test in each version, so the template can
    # draw a divider between version groups in both the header and body.
    flat_columns = [
        (test, index == len(group.tests) - 1)
        for group in column_groups
        for index, test in enumerate(group.tests)
    ]

    # One group per tool, each rendered as its own <tbody> so that hovering
    # any of its version rows can highlight the rowspan'd name/homepage
    # cell via a pure-CSS `tbody:hover` rule - a rowspan cell is only ever
    # a descendant of the row it's declared in, so a single shared <tbody>
    # can't do this with a `tr:hover` selector alone.
    tool_groups = []
    for tool in tools:
        version_rows = []
        for version_entry in tool.versions:
            cells = []
            for column, group_end in flat_columns:
                value = version_entry.values.get(column.id)
                cell = CELL_RENDERERS[column.type](value)
                cell["group_end"] = group_end
                cells.append(cell)
            version_rows.append({"version": version_entry.version, "cells": cells})
        tool_groups.append(
            {
                "name": tool.name,
                "homepage": str(tool.homepage) if tool.homepage else None,
                "rows": version_rows,
            }
        )

    # autoescape is enabled explicitly (not left to extension-sniffing)
    # because remote-sourced tool names/URLs flow into this template -
    # this is the control that stops a crafted "name" field from
    # becoming stored HTML/JS in the deployed page.
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(enabled_extensions=(), default=True),
    )
    template = env.get_template("index.html.j2")
    html = template.render(
        column_groups=column_groups,
        tool_groups=tool_groups,
        generated_at=datetime.fromisoformat(generated_at),
        config=config,
        main_text_html=main_text_html,
        footer_html=footer_html,
    )

    output_file.write_text(html)
    print(f"Wrote {output_file}")


def main() -> None:
    """Run the full build pipeline; see the module docstring for the steps."""
    column_groups = load_columns()
    config = load_config()
    main_text_html = render_markdown_file(MAIN_TEXT_FILE)
    footer_html = render_markdown_file(FOOTER_FILE)

    remote_tools = load_remote_tools()
    local_tools = load_local_tools()
    tools = merge_tools(remote_tools, local_tools)

    dataset = build_dataset(column_groups, tools)
    write_data_json(dataset)
    render_html(
        column_groups, tools, dataset["generated_at"], config, main_text_html, footer_html
    )


if __name__ == "__main__":
    main()
