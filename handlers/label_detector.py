"""Sensitivity Label Detector for Microsoft Information Protection (MIP) labels.

Detects MSIP sensitivity labels in:
- DOCX/XLSX/PPTX: via docProps/custom.xml (MSIP_Label_{guid}_* properties)
- PDF: via XMP metadata or document info dict (MSIP_Label_* keys)

Labels are hierarchical:
  Public < General < Confidential < Highly Confidential

Protection actions:
- READ: Always allowed (label is reported in doc_info)
- UPDATE: Allowed but label must be PRESERVED; warn if encrypted
- ARCHIVE: Blocked for Confidential+ unless confirm=true AND reason provided
- DELETE: Blocked for Confidential+ always (must archive instead)

Detection failures are FAIL-CLOSED: if we cannot determine label status on a
format that supports labels, destructive operations are blocked.
"""

import os
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class DetectionStatus(Enum):
    """Result status of label detection."""
    NO_LABEL = "no_label"          # File has no label (confirmed)
    LABEL_FOUND = "label_found"    # Label successfully detected
    DETECTION_ERROR = "error"      # Could not determine label status
    NOT_APPLICABLE = "n/a"         # File format does not support labels


# Sensitivity hierarchy (higher = more restrictive)
SENSITIVITY_LEVELS = {
    "public": 0,
    "general": 1,
    "internal": 1,
    "confidential": 2,
    "highly confidential": 3,
    "secret": 3,
}

# Operations and their minimum blocking level
# Operations at or above this level require extra confirmation
OPERATION_GATES = {
    "update": None,      # Never blocked, but label must be preserved
    "archive": 2,        # Confidential+ requires reason
    "delete": 2,         # Confidential+ blocked entirely (must archive)
}


@dataclass
class SensitivityLabel:
    """Represents a detected MIP sensitivity label."""
    label_id: str = ""
    label_name: str = ""
    site_id: str = ""
    owner: str = ""
    method: str = ""          # Standard or Privileged
    set_date: str = ""
    content_bits: str = ""    # Protection content bits
    is_encrypted: bool = False
    sensitivity_level: int = 0  # 0=Public, 1=General, 2=Confidential, 3=Highly Confidential
    raw_properties: dict = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        return self.label_name or "Unknown Label"

    @property
    def is_protected(self) -> bool:
        """True if the label implies content protection (encryption/RMS)."""
        return self.is_encrypted or self.content_bits not in ("", "0")

    def to_dict(self) -> dict:
        return {
            "label_id": self.label_id,
            "label_name": self.label_name,
            "sensitivity_level": self.sensitivity_level,
            "sensitivity_text": self._level_text(),
            "is_encrypted": self.is_encrypted,
            "is_protected": self.is_protected,
            "owner": self.owner,
            "method": self.method,
            "set_date": self.set_date,
        }

    def _level_text(self) -> str:
        for name, level in SENSITIVITY_LEVELS.items():
            if level == self.sensitivity_level:
                return name.title()
        return "Unknown"


@dataclass
class DetectionResult:
    """Result of label detection — distinguishes no-label from detection-error."""
    status: DetectionStatus
    label: Optional[SensitivityLabel] = None
    error_message: str = ""

    @property
    def is_safe_for_destructive_ops(self) -> bool:
        """True only if we CONFIRMED no label or label is below gate level."""
        return self.status in (DetectionStatus.NO_LABEL, DetectionStatus.NOT_APPLICABLE)


def detect_label(path: str) -> Optional[SensitivityLabel]:
    """Detect sensitivity label on a file. Returns None if no label found.

    LEGACY API — use detect_label_safe() for fail-closed behavior.
    """
    result = detect_label_safe(path)
    return result.label


def detect_label_safe(path: str) -> DetectionResult:
    """Detect sensitivity label with full status reporting.

    Returns DetectionResult with status indicating whether detection succeeded,
    failed, or is not applicable for this file format.
    """
    ext = os.path.splitext(path)[1].lower()

    if ext in (".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm"):
        return _detect_office_label_safe(path)
    elif ext == ".pdf":
        return _detect_pdf_label_safe(path)
    else:
        return DetectionResult(status=DetectionStatus.NOT_APPLICABLE)


def _detect_office_label(path: str) -> Optional[SensitivityLabel]:
    """Detect MIP label in Office documents via docProps/custom.xml."""
    result = _detect_office_label_safe(path)
    return result.label


def _detect_office_label_safe(path: str) -> DetectionResult:
    """Detect MIP label in Office documents — fail-closed on errors."""
    try:
        with zipfile.ZipFile(path, "r") as z:
            if "docProps/custom.xml" not in z.namelist():
                return DetectionResult(status=DetectionStatus.NO_LABEL)

            content = z.read("docProps/custom.xml").decode("utf-8")
            label = _parse_custom_xml(content)
            if label:
                return DetectionResult(status=DetectionStatus.LABEL_FOUND, label=label)
            return DetectionResult(status=DetectionStatus.NO_LABEL)
    except zipfile.BadZipFile as e:
        return DetectionResult(
            status=DetectionStatus.DETECTION_ERROR,
            error_message=f"Cannot read document structure (bad zip): {e}"
        )
    except Exception as e:
        return DetectionResult(
            status=DetectionStatus.DETECTION_ERROR,
            error_message=f"Label detection failed: {type(e).__name__}: {e}"
        )


def _parse_custom_xml(xml_content: str) -> Optional[SensitivityLabel]:
    """Parse MSIP_Label_* properties from custom.xml."""
    # Namespace handling
    ns = {
        "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
        "custom": "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties",
    }

    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return None

    # Extract all MSIP_Label properties
    msip_props = {}
    for prop in root.findall("custom:property", ns):
        name = prop.get("name", "")
        if name.startswith("MSIP_Label_"):
            # Get the value (usually in vt:lpwstr)
            value_elem = prop.find("vt:lpwstr", ns)
            if value_elem is not None and value_elem.text:
                msip_props[name] = value_elem.text

    if not msip_props:
        return None

    # Parse the label GUID and properties
    # Format: MSIP_Label_{guid}_{property}
    label = SensitivityLabel(raw_properties=msip_props)

    for key, value in msip_props.items():
        parts = key.split("_")
        if len(parts) < 4:
            continue

        # Extract GUID (between MSIP_Label_ and the property name)
        # MSIP_Label_{guid}_Name, MSIP_Label_{guid}_SiteId, etc.
        prop_name = parts[-1]

        if prop_name == "Name":
            label.label_name = value
            label.label_id = "_".join(parts[2:-1])  # The GUID portion
        elif prop_name == "SiteId":
            label.site_id = value
        elif prop_name == "Owner":
            label.owner = value
        elif prop_name == "Method":
            label.method = value
        elif prop_name == "SetDate":
            label.set_date = value
        elif prop_name == "ContentBits":
            label.content_bits = value
        elif prop_name == "Enabled" and value.lower() == "false":
            return None  # Label is disabled

    # Determine sensitivity level from label name
    if label.label_name:
        label.sensitivity_level = _classify_sensitivity(label.label_name)

    # Check encryption
    label.is_encrypted = label.content_bits not in ("", "0")

    return label if label.label_name else None


def _detect_pdf_label(path: str) -> Optional[SensitivityLabel]:
    """Detect MIP label in PDF via document info dict or XMP metadata."""
    result = _detect_pdf_label_safe(path)
    return result.label


def _detect_pdf_label_safe(path: str) -> DetectionResult:
    """Detect MIP label in PDF — fail-closed on errors."""
    try:
        from PyPDF2 import PdfReader

        reader = PdfReader(path)
        meta = reader.metadata

        if not meta:
            return DetectionResult(status=DetectionStatus.NO_LABEL)

        # Check document info dict for MSIP keys
        msip_props = {}
        for key, value in meta.items():
            key_str = str(key)
            if "MSIP" in key_str or "msip" in key_str:
                msip_props[key_str.lstrip("/")] = str(value)

        if msip_props:
            label = _parse_pdf_msip(msip_props)
            if label:
                return DetectionResult(status=DetectionStatus.LABEL_FOUND, label=label)
            return DetectionResult(status=DetectionStatus.NO_LABEL)

        # Check XMP metadata
        if hasattr(reader, "xmp_metadata") and reader.xmp_metadata:
            xmp = reader.xmp_metadata
            if hasattr(xmp, "custom_properties"):
                for key, value in xmp.custom_properties.items():
                    if "MSIP" in str(key):
                        msip_props[str(key)] = str(value)
                if msip_props:
                    label = _parse_pdf_msip(msip_props)
                    if label:
                        return DetectionResult(status=DetectionStatus.LABEL_FOUND, label=label)

        return DetectionResult(status=DetectionStatus.NO_LABEL)
    except Exception as e:
        return DetectionResult(
            status=DetectionStatus.DETECTION_ERROR,
            error_message=f"PDF label detection failed: {type(e).__name__}: {e}"
        )


def _parse_pdf_msip(props: dict) -> Optional[SensitivityLabel]:
    """Parse MSIP properties from PDF metadata."""
    label = SensitivityLabel(raw_properties=props)

    for key, value in props.items():
        key_lower = key.lower()
        if "name" in key_lower and "msip" in key_lower:
            label.label_name = value
        elif "siteid" in key_lower:
            label.site_id = value
        elif "owner" in key_lower:
            label.owner = value
        elif "method" in key_lower:
            label.method = value
        elif "setdate" in key_lower:
            label.set_date = value
        elif "contentbits" in key_lower:
            label.content_bits = value

    if label.label_name:
        label.sensitivity_level = _classify_sensitivity(label.label_name)
        label.is_encrypted = label.content_bits not in ("", "0")
        return label

    return None


def _classify_sensitivity(label_name: str) -> int:
    """Classify sensitivity level from label name string."""
    name_lower = label_name.lower().strip()

    # Check exact matches first
    if name_lower in SENSITIVITY_LEVELS:
        return SENSITIVITY_LEVELS[name_lower]

    # Check partial matches (e.g., "Confidential - All Employees")
    if "highly confidential" in name_lower or "highly_confidential" in name_lower:
        return 3
    elif "secret" in name_lower:
        return 3
    elif "confidential" in name_lower:
        return 2
    elif "internal" in name_lower:
        return 1
    elif "general" in name_lower:
        return 1
    elif "public" in name_lower:
        return 0

    # Unknown label — treat as confidential (safe default)
    return 2


def check_operation_allowed(label: Optional[SensitivityLabel], operation: str, confirm: bool = False, reason: str = "",
                            detection_result: Optional[DetectionResult] = None) -> tuple[bool, str]:
    """Check if an operation is allowed given the document's sensitivity label.

    Uses fail-closed semantics: if detection_result indicates an error,
    destructive operations (archive, delete) are blocked.

    Returns:
        (allowed: bool, message: str)
    """
    # Fail-closed: if detection errored, block destructive ops
    if detection_result and detection_result.status == DetectionStatus.DETECTION_ERROR:
        if operation in ("delete", "archive"):
            return False, (
                f"🚫 BLOCKED: Cannot {operation} — sensitivity label detection failed. "
                f"Reason: {detection_result.error_message}. "
                f"Fix the document or verify it manually before retrying."
            )
        # For update, warn but allow
        return True, (
            f"⚠️ WARNING: Label detection failed ({detection_result.error_message}). "
            f"Cannot verify label preservation."
        )

    if label is None:
        return True, ""

    gate_level = OPERATION_GATES.get(operation)

    if gate_level is None:
        # Operation is never blocked by labels (e.g., update)
        if label.is_encrypted:
            return True, f"⚠️ WARNING: Document has encrypted label '{label.display_name}'. Edits will preserve the label metadata but cannot re-encrypt content."
        return True, ""

    if label.sensitivity_level < gate_level:
        return True, ""

    # Label is at or above the gate level
    if operation == "delete":
        return False, f"🚫 BLOCKED: Cannot delete document with '{label.display_name}' sensitivity label (level {label.sensitivity_level}). Use doc_archive instead to maintain audit trail."

    if operation == "archive":
        if not confirm:
            return False, f"🚫 BLOCKED: Document has '{label.display_name}' sensitivity label. Archive requires confirm=true AND a reason for audit trail."
        if not reason or reason == "No reason provided":
            return False, f"🚫 BLOCKED: Document has '{label.display_name}' sensitivity label. A reason is REQUIRED for archiving labeled documents."
        return True, f"⚠️ Archiving labeled document '{label.display_name}' with reason: {reason}"

    return True, ""
