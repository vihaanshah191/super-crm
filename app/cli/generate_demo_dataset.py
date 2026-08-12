"""Generate a synthetic demo dataset of Indian companies for client demos.

NOT REAL DATA. Every company name, CIN, phone number, website, and address
here is synthesized from a fixed random seed -- any resemblance to a real
company is coincidental. This script only produces a CSV; it never writes
to the database itself (see app/cli/seed_demo.py, which imports the CSV
through the existing CustomFileAdapter pipeline as a clearly-named,
OBSERVED, below-registry-confidence source -- "Super CRM Demo Dataset" --
so demo companies are never presented as verified real-world facts).

Deliberately varied so the DEFINITE/POSSIBLE/UNKNOWN match-strength system
(app.search.filter_compiler) has real data to demonstrate against: each row
gets one of five employee/revenue data shapes --
  - exact employees + exact revenue (-> DEFINITE against most filters)
  - exact employees, revenue unknown (-> UNKNOWN on a revenue filter)
  - employees unknown, exact revenue (-> UNKNOWN on an employee filter)
  - estimated employee range + estimated revenue range (-> POSSIBLE against
    a boundary filter, DEFINITE against one the whole range clears)
  - both unknown (a company that's barely more than a name and a location)
-- see _EMPLOYEE_REVENUE_SHAPES below for the exact weights.

    python -m app.cli.generate_demo_dataset --count 750 --seed 42 \\
        --out data/demo/super_crm_demo_dataset.csv
"""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

DEFAULT_OUT_PATH = Path(__file__).parent.parent.parent / "data" / "demo" / "super_crm_demo_dataset.csv"
DEFAULT_COUNT = 750
DEFAULT_SEED = 42

# Source-column headers -> canonical field, in the exact shape
# custom_field_mapping.validate_field_mapping()/CustomFileAdapter expect.
# Deliberately uses human-authored-looking headers (not canonical names) so
# the demo also shows the field-mapping step, not just the import result.
FIELD_MAPPING: dict[str, str] = {
    "Company Name": "legal_name",
    "CIN": "cin",
    "GSTIN": "gstin",
    "State": "state",
    "City": "city",
    "Postal Code": "postal_code",
    "Country Code": "country_code",
    "Industry": "industry",
    "Sub Industry": "sub_industry",
    "Company Type": "company_type",
    "Employees": "employee_count",
    "Employee Range Min": "employee_range_min",
    "Employee Range Max": "employee_range_max",
    "Annual Revenue (INR)": "annual_revenue_inr",
    "Revenue Range Min (INR)": "revenue_range_min_inr",
    "Revenue Range Max (INR)": "revenue_range_max_inr",
    "Revenue Year": "revenue_year",
    "Website": "website",
    "Phone": "public_phone",
    "Products": "products",
    "Services": "services",
    "Export Status": "export_status",
    "Incorporation Date": "incorporation_date",
}

# state name -> (MCA-style state code used inside a synthetic CIN, [cities])
STATES: dict[str, tuple[str, list[str]]] = {
    "Maharashtra": ("MH", ["Mumbai", "Pune", "Nagpur", "Nashik", "Aurangabad", "Thane"]),
    "Gujarat": ("GJ", ["Ahmedabad", "Surat", "Vadodara", "Rajkot"]),
    "Karnataka": ("KA", ["Bengaluru", "Mysuru", "Hubballi", "Mangaluru"]),
    "Tamil Nadu": ("TN", ["Chennai", "Coimbatore", "Madurai", "Tiruppur"]),
    "Delhi": ("DL", ["New Delhi", "Dwarka", "Rohini"]),
    "Telangana": ("TG", ["Hyderabad", "Warangal", "Secunderabad"]),
    "Uttar Pradesh": ("UP", ["Noida", "Lucknow", "Kanpur", "Ghaziabad"]),
    "Haryana": ("HR", ["Gurugram", "Faridabad", "Panipat"]),
    "West Bengal": ("WB", ["Kolkata", "Howrah", "Durgapur"]),
    "Punjab": ("PB", ["Ludhiana", "Amritsar", "Jalandhar"]),
    "Rajasthan": ("RJ", ["Jaipur", "Udaipur", "Jodhpur"]),
    "Madhya Pradesh": ("MP", ["Indore", "Bhopal", "Gwalior"]),
}
# Weighted so the flagship demo query (Manufacturing, Maharashtra) has a
# rich, realistic-looking result set rather than a handful of coincidental
# matches -- not uniform across all states.
STATE_WEIGHTS: dict[str, int] = {
    "Maharashtra": 22, "Gujarat": 12, "Karnataka": 12, "Tamil Nadu": 10,
    "Delhi": 8, "Telangana": 8, "Uttar Pradesh": 7, "Haryana": 6,
    "West Bengal": 5, "Punjab": 4, "Rajasthan": 4, "Madhya Pradesh": 2,
}

# industry -> (MCA-style 5-digit sector code prefix, [sub-industries], [products], [services])
INDUSTRIES: dict[str, tuple[str, list[str], list[str], list[str]]] = {
    "Manufacturing": ("25200", ["Industrial Components", "Machine Parts", "Metal Fabrication"],
                       ["Precision Components", "Metal Assemblies", "Sheet Metal Parts"],
                       ["Custom Fabrication", "Assembly Services", "Quality Inspection"]),
    "Chemicals": ("20110", ["Specialty Chemicals", "Industrial Solvents", "Polymers"],
                  ["Industrial Solvents", "Specialty Polymers", "Coating Chemicals"],
                  ["Formulation Services", "Bulk Supply", "Regulatory Documentation"]),
    "Pharmaceuticals": ("21001", ["Generic Drugs", "Active Pharma Ingredients", "Formulations"],
                        ["Generic Tablets", "APIs", "Oral Suspensions"],
                        ["Contract Manufacturing", "Regulatory Support", "Packaging Services"]),
    "Automotive": ("29100", ["Auto Components", "OEM Parts", "Tyres & Rubber"],
                   ["Auto Components", "OEM Parts", "Rubber Moulded Parts"],
                   ["Aftermarket Support", "Fleet Servicing", "Component Testing"]),
    "Textiles": ("13100", ["Cotton Yarn", "Woven Fabric", "Garments"],
                 ["Cotton Yarn", "Woven Fabric", "Knitted Garments"],
                 ["Dyeing & Finishing", "Contract Weaving", "Export Packaging"]),
    "Food Processing": ("10750", ["Packaged Foods", "Spices", "Dairy Products"],
                        ["Packaged Snacks", "Ground Spices", "Dairy Products"],
                        ["Co-packing", "Cold Chain Logistics", "Private Labeling"]),
    "Electronics": ("26100", ["PCB Assemblies", "Consumer Electronics", "Sensors"],
                    ["PCB Assemblies", "Consumer Electronics", "Industrial Sensors"],
                    ["Design Services", "Contract Assembly", "Testing & Certification"]),
    "Industrial Equipment": ("28100", ["Pumps & Compressors", "Conveyor Systems", "Machine Tools"],
                              ["Industrial Pumps", "Air Compressors", "Conveyor Systems"],
                              ["Installation", "Maintenance Contracts", "Spare Parts Supply"]),
    "Logistics": ("52290", ["Freight Services", "Warehousing", "Fleet Management"],
                  ["Freight Forwarding", "Warehousing Space", "Fleet Leasing"],
                  ["Last-mile Delivery", "Customs Clearance", "Inventory Management"]),
    "IT Services": ("62011", ["Custom Software", "Cloud Migration", "Data Analytics"],
                    ["Custom Software Platforms", "Analytics Dashboards", "Mobile Apps"],
                    ["Staff Augmentation", "Managed Services", "Cloud Migration"]),
    "Construction": ("41000", ["Residential Projects", "Commercial Buildings", "Infrastructure"],
                     ["Residential Complexes", "Commercial Buildings", "Road Infrastructure"],
                     ["Project Management", "Turnkey Contracting", "Site Supervision"]),
    "Wholesale": ("46900", ["Bulk Electronics", "FMCG Distribution", "Hardware Supplies"],
                  ["Bulk Electronics", "FMCG Products", "Hardware Supplies"],
                  ["B2B Distribution", "Inventory Management", "Bulk Procurement"]),
}
INDUSTRY_WEIGHTS: dict[str, int] = {
    "Manufacturing": 20, "Chemicals": 9, "Pharmaceuticals": 8, "Automotive": 9,
    "Textiles": 8, "Food Processing": 8, "Electronics": 8, "Industrial Equipment": 8,
    "Logistics": 7, "IT Services": 7, "Construction": 5, "Wholesale": 3,
}

_NAME_SUFFIXES = ["Industries", "Enterprises", "Manufacturing Co", "Exports", "Technologies",
                   "Solutions", "Engineering Works", "Group", "Traders", "Corporation",
                   "Textiles", "Electronics", "Systems", "Ventures", "Holdings", "Works",
                   "Logistics", "Overseas", "International", "Fabricators"]
# Two DISTINCT words drawn from this pool, not a shared prefix + a trailing
# sequence number -- entity resolution's fuzzy-name-similarity signal
# (rapidfuzz token_sort_ratio, see app/ingestion/entity_resolution/fuzzy.py)
# scores "Shree Industries 42" vs "Shree Industries 137" as highly similar
# (only the numeric token differs), which was landing ~20% of a first
# generated batch in the human-review queue instead of becoming a company --
# a real name needs enough entropy that two DIFFERENT synthetic companies
# don't read as a probable duplicate of each other.
_NAME_WORDS = [
    "Shree", "Om", "Bharat", "National", "United", "Sunrise", "Prime", "Apex", "Vishwa", "Sai",
    "Global", "Metro", "Royal", "Star", "Elite", "Classic", "Silver", "Golden", "Divine", "Trinity",
    "Continental", "Century", "Horizon", "Pioneer", "Legacy", "Everest", "Highland", "Meridian",
    "Zenith", "Summit", "Crown", "Diamond", "Platinum", "Titan", "Phoenix", "Falcon", "Eagle",
    "Tiger", "Lotus", "Ganges", "Indus", "Himalaya", "Deccan", "Konkan", "Malabar", "Coromandel",
    "Vindhya", "Aravalli", "Nilgiri", "Ashoka", "Maurya", "Chola", "Vijay", "Anand", "Kiran",
]

COMPANY_TYPES = ["Private Limited Company", "Public Limited Company", "Limited Liability Partnership"]


@dataclass
class _EmployeeRevenue:
    employee_count: int | None = None
    employee_range_min: int | None = None
    employee_range_max: int | None = None
    annual_revenue_inr: float | None = None
    revenue_range_min_inr: float | None = None
    revenue_range_max_inr: float | None = None
    revenue_year: int | None = None


_CRORE = 10_000_000

# (shape name, weight) -- see module docstring for what each shape demonstrates.
_EMPLOYEE_REVENUE_SHAPES: list[tuple[str, int]] = [
    ("both_exact", 40),
    ("employees_unknown", 15),
    ("revenue_unknown", 15),
    ("both_range", 20),
    ("both_unknown", 10),
]


def _weighted_choice(rng: random.Random, weights: dict[str, int]) -> str:
    keys = list(weights.keys())
    return rng.choices(keys, weights=[weights[k] for k in keys], k=1)[0]


def _employee_revenue(rng: random.Random, shape: str) -> _EmployeeRevenue:
    # Revenue loosely scaled off employee count (a few crore per employee,
    # with noise) so the two numbers look like they belong to the same
    # company rather than being drawn independently.
    base_employees = round(rng.triangular(8, 800, 60))
    revenue_per_employee_lakh = rng.triangular(2, 25, 8)  # lakh INR per employee
    base_revenue = base_employees * revenue_per_employee_lakh * 100_000

    if shape == "both_exact":
        return _EmployeeRevenue(
            employee_count=base_employees,
            annual_revenue_inr=round(base_revenue, -5),
            revenue_year=rng.choice([2023, 2024, 2025]),
        )
    if shape == "employees_unknown":
        return _EmployeeRevenue(annual_revenue_inr=round(base_revenue, -5), revenue_year=rng.choice([2023, 2024, 2025]))
    if shape == "revenue_unknown":
        return _EmployeeRevenue(employee_count=base_employees)
    if shape == "both_range":
        emp_width = round(base_employees * rng.uniform(0.25, 0.5)) or 5
        rev_width = base_revenue * rng.uniform(0.25, 0.5)
        return _EmployeeRevenue(
            employee_range_min=max(1, base_employees - emp_width),
            employee_range_max=base_employees + emp_width,
            revenue_range_min_inr=round(max(0, base_revenue - rev_width), -5),
            revenue_range_max_inr=round(base_revenue + rev_width, -5),
            revenue_year=rng.choice([2023, 2024, 2025]),
        )
    # both_unknown
    return _EmployeeRevenue()


def _synthetic_cin(rng: random.Random, state_code: str, sector_code: str, inc_year: int, seq: int) -> str:
    ownership = rng.choice(["PTC", "PLC"])
    listing = "U"  # unlisted -- every demo company is synthetic, never claim listed status
    return f"{listing}{sector_code}{state_code}{inc_year}{ownership}{seq:06d}"


def _company_name(rng: random.Random) -> str:
    word1, word2 = rng.sample(_NAME_WORDS, k=2)
    return f"{word1} {word2} {rng.choice(_NAME_SUFFIXES)}"


def _incorporation_date(rng: random.Random) -> date:
    year = rng.randint(1998, 2023)
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    return date(year, month, day)


def generate_rows(count: int = DEFAULT_COUNT, seed: int = DEFAULT_SEED) -> list[dict[str, str]]:
    """Pure generation -- no I/O. Deterministic for a given (count, seed)
    so the demo dataset is reproducible across machines/runs."""
    rng = random.Random(seed)
    rows: list[dict[str, str]] = []

    for seq in range(1, count + 1):
        state = _weighted_choice(rng, STATE_WEIGHTS)
        state_code, cities = STATES[state]
        city = rng.choice(cities)

        industry = _weighted_choice(rng, INDUSTRY_WEIGHTS)
        sector_code, sub_industries, products, services = INDUSTRIES[industry]
        sub_industry = rng.choice(sub_industries)

        inc_date = _incorporation_date(rng)
        cin = _synthetic_cin(rng, state_code, sector_code, inc_date.year, seq)
        name = _company_name(rng)

        shape = _weighted_choice(rng, dict(_EMPLOYEE_REVENUE_SHAPES))
        er = _employee_revenue(rng, shape)

        domain = name.lower().replace(" ", "").replace(".", "")[:24] + ".example"

        row = {
            "Company Name": name,
            "CIN": cin,
            "GSTIN": f"{rng.randint(10, 37):02d}ABCDE{rng.randint(1000, 9999)}F1Z{rng.randint(1, 9)}",
            "State": state,
            "City": city,
            "Postal Code": f"{rng.randint(100000, 999999)}",
            "Country Code": "IN",
            "Industry": industry,
            "Sub Industry": sub_industry,
            "Company Type": rng.choice(COMPANY_TYPES),
            "Employees": "" if er.employee_count is None else str(er.employee_count),
            "Employee Range Min": "" if er.employee_range_min is None else str(er.employee_range_min),
            "Employee Range Max": "" if er.employee_range_max is None else str(er.employee_range_max),
            "Annual Revenue (INR)": "" if er.annual_revenue_inr is None else str(int(er.annual_revenue_inr)),
            "Revenue Range Min (INR)": "" if er.revenue_range_min_inr is None else str(int(er.revenue_range_min_inr)),
            "Revenue Range Max (INR)": "" if er.revenue_range_max_inr is None else str(int(er.revenue_range_max_inr)),
            "Revenue Year": "" if er.revenue_year is None else str(er.revenue_year),
            "Website": f"https://{domain}",
            "Phone": f"+91 {rng.randint(70000, 99999)} {rng.randint(10000, 99999)}",
            "Products": ", ".join(rng.sample(products, k=min(2, len(products)))),
            "Services": ", ".join(rng.sample(services, k=min(2, len(services)))),
            "Export Status": rng.choice(["true", "false"]),
            "Incorporation Date": inc_date.isoformat(),
        }
        rows.append(row)

    return rows


def write_csv(rows: list[dict[str, str]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(FIELD_MAPPING.keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help=f"Number of companies to generate (default {DEFAULT_COUNT})")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help=f"Random seed for reproducibility (default {DEFAULT_SEED})")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH, help=f"Output CSV path (default {DEFAULT_OUT_PATH})")
    args = parser.parse_args()

    rows = generate_rows(count=args.count, seed=args.seed)
    write_csv(rows, args.out)
    print(f"Wrote {len(rows)} synthetic demo companies to {args.out}")
    print("NOT REAL DATA -- see this script's module docstring.")


if __name__ == "__main__":
    main()
