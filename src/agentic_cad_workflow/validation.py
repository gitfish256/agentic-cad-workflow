from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .runner import ShapeProperties

VALIDATION_PROFILES = ("printable", "reference", "assembly")
DEFAULT_MAX_PRINT_DIM_MM = 300.0


@dataclass
class ValidationReport:
    valid: bool
    score: float
    checks: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    profile: str = "printable"
    scope: str = "geometry_heuristic"
    limitations: list[str] = field(default_factory=list)

    def add(
        self,
        name: str,
        passed: bool,
        message: str,
        weight: float = 1.0,
        value: Any = None,
        severity: str | None = None,
    ) -> None:
        self.checks.append(
            {
                "name": name,
                "passed": bool(passed),
                "severity": severity or ("pass" if passed else "error"),
                "message": message,
                "weight": weight,
                "value": value,
            }
        )

    def finalize(self) -> ValidationReport:
        total = sum(float(c["weight"]) for c in self.checks) or 1.0
        got = sum(float(c["weight"]) for c in self.checks if c["passed"])
        self.score = got / total
        critical = {"geometry_valid", "solid", "positive_volume"}
        if self.profile == "printable":
            critical.add("stl_printable_size")
            critical.add("print_bed_base")
        self.valid = self.score >= 0.84 and all(c["passed"] for c in self.checks if c["name"] in critical)
        failed = [c["name"] for c in self.checks if not c["passed"]]
        advisories = [c["name"] for c in self.checks if c.get("severity") == "warning"]
        if failed:
            self.summary = "failed checks: " + ", ".join(failed)
        elif advisories:
            self.summary = "validation passed with advisories: " + ", ".join(advisories)
        else:
            self.summary = "all validation checks passed"
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "score": self.score,
            "summary": self.summary,
            "profile": self.profile,
            "scope": self.scope,
            "limitations": self.limitations,
            "checks": self.checks,
        }


def validate_prompt_shape(
    prompt: str,
    props: ShapeProperties,
    profile: str = "printable",
    max_print_dim_mm: float | None = None,
) -> ValidationReport:
    """Geometry-heuristic validation for host-agent revision loops.

    The validation is intentionally machine-readable: an agent can inspect failed checks,
    adjust CadQuery code, rerun rendering, and stop only after the report is valid.

    Profiles:
    - "printable" (default): solid, positive volume, and print-bed size are all fatal.
    - "reference"/"assembly": solid and positive volume stay fatal, but exceeding the
      print bed is reported as a non-fatal advisory (full-size references and multi-part
      assemblies legitimately exceed a single bed).
    """
    if profile not in VALIDATION_PROFILES:
        raise ValueError(f"unknown validation profile {profile!r}; expected one of {VALIDATION_PROFILES}")
    max_dim = DEFAULT_MAX_PRINT_DIM_MM if max_print_dim_mm is None else float(max_print_dim_mm)
    if max_dim <= 0:
        raise ValueError(f"max_print_dim_mm must be > 0, got {max_dim}")

    report = ValidationReport(valid=False, score=0.0, profile=profile)
    report.limitations.append(
        "This is a geometry heuristic: it does not automatically verify local wall thickness, "
        "hole/clearance sizes, overhangs, support requirements, material behaviour, or STL mesh manifoldness."
    )
    bbox = props.bounding_box
    volume = props.volume
    topology = props.topology or {}

    solid_count = topology.get("solids", 0)
    geometry_valid = props.valid is True
    report.add(
        "geometry_valid",
        geometry_valid,
        "BREP validity was not available" if props.valid is None else f"BREP isValid = {props.valid}",
        weight=2.0,
        value=props.valid,
    )
    report.add("solid", solid_count >= 1, f"solid count = {solid_count}", weight=2.0, value=solid_count)
    report.add("positive_volume", bool(volume and volume > 0), f"volume = {volume}", weight=2.0, value=volume)

    dims = [bbox.get("xlen", 0), bbox.get("ylen", 0), bbox.get("zlen", 0)]
    min_dim = min(dims)
    fits_bed = min_dim >= 0.8 and max(dims) <= max_dim
    if profile == "printable":
        report.add(
            "stl_printable_size",
            fits_bed,
            f"overall bounding-box dimensions are {dims} mm; min should be >=0.8mm and max <={max_dim:g}mm for this profile "
            "(this does not measure local wall or feature thickness)"
            + (
                ""
                if fits_bed
                else " — keep the complete model and validate it with the reference profile; the user can split it "
                "in slicer/CAD software, or use split_artifact if they ask for help splitting"
            ),
            weight=1.0,
            value=dims,
        )
    else:
        report.add(
            "print_bed_size_advisory",
            True,
            (
                f"bounding-box dimensions are {dims} mm; "
                + (
                    f"fits a {max_dim:g}mm bed"
                    if fits_bed
                    else f"exceeds a {max_dim:g}mm bed — expected for a {profile} model, split into segments before printing"
                )
            ),
            weight=1.0,
            value={"dims": dims, "fits_bed": fits_bed, "max_print_dim_mm": max_dim},
            severity="pass" if fits_bed else "warning",
        )
    on_bed = abs(bbox.get("zmin", 0)) <= 1e-5
    if profile == "printable":
        report.add("print_bed_base", on_bed, f"zmin = {bbox.get('zmin')}", weight=1.0, value=bbox.get("zmin"))
    else:
        # Reference/assembly models are not printed as-is; base placement is informational.
        report.add(
            "print_bed_base_advisory",
            True,
            f"zmin = {bbox.get('zmin')}" + ("" if on_bed else f" — below Z=0, fine for a {profile} model"),
            weight=1.0,
            value={"zmin": bbox.get("zmin"), "on_bed": on_bed},
            severity="pass" if on_bed else "warning",
        )

    return report.finalize()
