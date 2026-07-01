"""Data models for the compatibility matrix.

These are used to vaildate the YAML files (e.g., column.yaml
and the conformance results).
"""
import math
import re
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]+$")
_HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")


def _validate_id_slug(value: str) -> str:
    if not _ID_PATTERN.match(value):
        raise ValueError(
            f"id '{value}' must contain only letters, numbers, '.', '_', '-'"
        )
    return value


def _validate_hex_color(value: str) -> str:
    if not _HEX_COLOR_PATTERN.match(value):
        raise ValueError(f"color '{value}' must be a 6-digit hex color like '#1c4a87'")
    return value


class ColumnDef(BaseModel):
    """One leaf (test-type) column, nested under a version in columns.yaml."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=80)
    type: Literal["boolean", "percentage"]

    @field_validator("id")
    @classmethod
    def id_is_safe_slug(cls, value: str) -> str:
        return _validate_id_slug(value)


class VersionGroup(BaseModel):
    """One spec version and its nested test-type columns, as defined in
    columns.yaml - e.g. v0.6 grouping the attributes/zarr/transforms tests
    run against that version."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=80)
    tests: list[ColumnDef] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def id_is_safe_slug(cls, value: str) -> str:
        return _validate_id_slug(value)

    @field_validator("tests")
    @classmethod
    def test_ids_unique_within_group(cls, tests: list[ColumnDef]) -> list[ColumnDef]:
        seen = set()
        for test in tests:
            if test.id in seen:
                raise ValueError(f"duplicate test id '{test.id}' within one version group")
            seen.add(test.id)
        return tests


class ColumnsFile(BaseModel):
    """The full contents of columns.yaml."""

    model_config = ConfigDict(extra="forbid")

    columns: list[VersionGroup] = Field(min_length=1)

    @field_validator("columns")
    @classmethod
    def ids_are_unique(cls, columns: list[VersionGroup]) -> list[VersionGroup]:
        seen_versions = set()
        seen_leaf_ids = set()
        for group in columns:
            if group.id in seen_versions:
                raise ValueError(f"duplicate version id '{group.id}'")
            seen_versions.add(group.id)
            for test in group.tests:
                # Leaf ids must be unique across the whole file, not just
                # within their own group - they're the flat keys tool YAML
                # files use under `values:`.
                if test.id in seen_leaf_ids:
                    raise ValueError(f"duplicate column id '{test.id}'")
                seen_leaf_ids.add(test.id)
        return columns


class SiteConfig(BaseModel):
    """The full contents of config.yaml.

    Every field - and the file itself - is optional; anything omitted
    falls back to the default below, which reproduces the site's
    original hardcoded styling. Colors are 6-digit hex strings.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="OME-Zarr conformance matrix", min_length=1, max_length=100)
    table_header_color: str = "#1c4a87"
    background_color: str = "#ffffff"
    text_font_color: str = "#5b5f5a"
    table_header_font_color: str = "#ffffff"
    table_font_color: str = "#1c1f1e"

    @field_validator(
        "table_header_color",
        "background_color",
        "text_font_color",
        "table_header_font_color",
        "table_font_color",
    )
    @classmethod
    def color_is_hex(cls, value: str) -> str:
        return _validate_hex_color(value)


class RemoteSourcesFile(BaseModel):
    """The full contents of conformance_results/remote-sources.yaml."""

    model_config = ConfigDict(extra="forbid")

    sources: list[HttpUrl] = Field(default_factory=list)

    @field_validator("sources")
    @classmethod
    def https_only(cls, sources: list[HttpUrl]) -> list[HttpUrl]:
        for url in sources:
            if url.scheme != "https":
                raise ValueError(
                    f"remote source '{url}' must use https, not '{url.scheme}'"
                )
        return sources


# A cell value: True/False for boolean columns, 0-100 for percentage
# columns, or None/omitted for "not tested". Free text is not permitted
# here - notes belong in a separate, explicitly textual field if one is
# added later - so a compromised remote source can't smuggle arbitrary
# strings into a field that's meant to drive color logic.
CellValue = Union[bool, float, int, None]


class ConformanceResult(BaseModel):
    """One tool/library's conformance result, as defined in a
    conformance_results/local/*.yaml file or fetched from a URL listed in
    conformance_results/remote-sources.yaml."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    homepage: Optional[HttpUrl] = None
    values: dict[str, CellValue] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def id_is_safe_slug(cls, value: str) -> str:
        return _validate_id_slug(value)

    @field_validator("values")
    @classmethod
    def validate_values(cls, values: dict) -> dict:
        for key, value in values.items():
            if not _ID_PATTERN.match(key):
                raise ValueError(f"value key '{key}' is not a valid column id")
            if value is None or isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                # check the numeric values work as a percentage
                if isinstance(value, float) and (
                    math.isnan(value) or math.isinf(value)
                ):
                    raise ValueError(f"value for '{key}' must be a finite number")
                if not (0 <= value <= 100):
                    raise ValueError(
                        f"numeric value for '{key}' must be between 0 and 100, "
                        f"got {value}"
                    )
                continue
            raise ValueError(
                f"value for '{key}' must be a boolean, a number 0-100, or "
                f"omitted; got {type(value).__name__}"
            )
        return values
