"""Registry of official sources for the demo.

Each entry is a real, public URL. We cache the file locally under
fixtures/ so the demo can run without depending on flaky network
fetches, but every record we store still points back at the original
public URL as its source of truth.
"""
import os
from dataclasses import dataclass

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FIXTURES_DIR = os.path.join(PROJECT_ROOT, "fixtures")


@dataclass
class SourceSpec:
    key: str                # short identifier
    url: str                # canonical public URL
    title: str
    jurisdiction: str
    department: str
    source_type: str        # 'pdf' | 'html'
    parser: str             # name of parser function
    local_filename: str     # cached file name in fixtures/
    notes: str = ""

    @property
    def local_path(self) -> str:
        return os.path.join(FIXTURES_DIR, self.local_filename)


SOURCES: list[SourceSpec] = [
    SourceSpec(
        key="calimesa_master_fee_2025",
        url="https://www.calimesa.gov/DocumentCenter/View/1856/Calimesa-Master-Fee-Schedule-Effective-090125",
        title="City of Calimesa Master Fee Schedule (Effective Sept 1, 2025)",
        jurisdiction="City of Calimesa",
        department="Multiple",
        source_type="pdf",
        parser="parse_calimesa_master",
        local_filename="calimesa_master_fee_schedule_2025-09-01.pdf",
        notes="Linked from https://www.calimesa.gov/260/Development-Impact-Fee-Transparency",
    ),
    SourceSpec(
        key="rivco_eh_community_events_2024",
        url="https://rivcoeh.org/sites/g/files/aldnop361/files/2024-06/Temporary-Food-Facilities-Permit-Fees-for-Community-Events%207-24.pdf",
        title="Riverside County Environmental Health - Permit Fees for Community Events (Rev 7/2024)",
        jurisdiction="Riverside County",
        department="Environmental Health",
        source_type="pdf",
        parser="parse_rivco_eh_community_events",
        local_filename="rivco_eh_community_events_2024-07.pdf",
    ),
    SourceSpec(
        key="rivco_dif_ord_659_14",
        url="https://rivcocob.org/sites/g/files/aldnop311/files/2024-12/659.14.pdf",
        title="Riverside County Development Impact Fee Ordinance 659.14 (Year 2024)",
        jurisdiction="Riverside County (unincorporated)",
        department="Transportation and Land Management Agency",
        source_type="pdf",
        parser="parse_rivco_dif",
        local_filename="rivco_dif_ord_659-14.pdf",
        notes=(
            "Establishes per-square-foot DIF for residential, commercial, office, "
            "industrial, surface mining, wineries, and warehouse across all "
            "Riverside County area plans. Includes Fire Protection component."
        ),
    ),
    SourceSpec(
        key="emwd_consolidated_rates_2026",
        url="https://content.emwd.org/sites/default/files/2026-02/Consolidated%20Schedule%20of%20Rates%20Updated%201.22.26.rev1_.pdf",
        title="EMWD Consolidated Schedule of Rates, Fees, and Charges (Updated 1/22/2026)",
        jurisdiction="Riverside County",
        department="Eastern Municipal Water District",
        source_type="pdf",
        parser="parse_emwd_rates",
        local_filename="emwd_rates_2026-01.pdf",
        notes=(
            "EMWD serves much of Riverside County (Hemet, San Jacinto, Moreno Valley, "
            "Menifee, Temecula, Murrieta, Perris, Romoland). Calimesa is NOT in EMWD's "
            "service area — it is served by Yucaipa Valley Water District. "
            "See yvwd_rates source for Calimesa water."
        ),
    ),
    SourceSpec(
        key="calimesa_eh_pointer",
        url="https://rivcoeh.org/",
        title="City of Calimesa — Environmental Health Cross-Reference",
        jurisdiction="City of Calimesa",
        department="Environmental Health (via Riverside County DEH)",
        source_type="pointer",
        parser="parse_pointer_only",
        local_filename="_pointer_calimesa_eh.txt",
        notes=(
            "Calimesa does not run its own Environmental Health program. "
            "Restaurant, food, well, septic, and pool permits for Calimesa "
            "businesses are issued by Riverside County DEH. See the "
            "'rivco_eh_community_events_2024' source for the actual fee "
            "amounts that apply to Calimesa businesses."
        ),
    ),
    SourceSpec(
        key="yvwd_rates_blocker",
        url="https://www.yvwd.us/",
        title="Yucaipa Valley Water District — Calimesa Water Provider (BLOCKER)",
        jurisdiction="City of Calimesa",
        department="Yucaipa Valley Water District",
        source_type="pointer",
        parser="parse_pointer_only",
        local_filename="_pointer_yvwd.txt",
        notes=(
            "BLOCKER: Calimesa's potable water and sewer service is provided by "
            "Yucaipa Valley Water District (YVWD). YVWD does not publish a "
            "current consolidated rate PDF at a stable public URL (verified "
            "April 2026). Their rates are set by board ordinance and only "
            "appear inside meeting packets on documents.yvwd.dst.ca.us. "
            "To complete this gap a human needs to identify the latest rate "
            "ordinance and add it as a parser source."
        ),
    ),
    SourceSpec(
        key="calimesa_dif_2017",
        url="https://www.calimesa.gov/DocumentCenter/View/908",
        title="City of Calimesa Development Impact Fee Schedule (Resolution 2017-11)",
        jurisdiction="City of Calimesa",
        department="Development",
        source_type="pdf",
        parser="parse_calimesa_dif",
        # The original PDF is image-only (no extractable text). We OCR it
        # offline with `ocrmypdf calimesa_dif_schedule_2017.pdf <_ocr.pdf>`
        # and parse the OCR output. Every numeric value extracted from the
        # OCR layer is flagged needs_review so a human verifies against the
        # original image before the number is trusted.
        local_filename="calimesa_dif_schedule_2017_ocr.pdf",
        notes=(
            "Original public PDF is an image scan (no extractable text). "
            "Demo runs ocrmypdf locally to produce a text layer; numbers "
            "are extracted from OCR output and flagged needs_review."
        ),
    ),
]


def get_source(key: str) -> SourceSpec:
    for s in SOURCES:
        if s.key == key:
            return s
    raise KeyError(key)
