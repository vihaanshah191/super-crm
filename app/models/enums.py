import enum


class SourceType(str, enum.Enum):
    WEBSITE = "website"
    GOVERNMENT_DATASET = "government_dataset"
    DIRECTORY = "directory"
    MARKETPLACE = "marketplace"
    PUBLIC_FILING = "public_filing"
    # A paid/API-based third-party reseller of registry data (e.g. FileSure,
    # api.filesure.in) -- distinct from GOVERNMENT_DATASET because it's not
    # an official government distribution channel, even though the
    # underlying data it relays originates from one (MCA). See
    # docs/filesure_data_access.md.
    REGISTRY_DATA_PROVIDER = "registry_data_provider"
    # A user/admin-supplied CSV or JSON file with a self-declared field
    # mapping (see app/source_adapters/custom_file_adapter.py) -- not a
    # network collection mechanism at all, so it's exempt from the
    # scraping-specific compliance questions (robots, WAF, CAPTCHA) other
    # source types raise, but every observation it produces is OBSERVED at
    # a below-registry confidence weight since the data's authenticity is
    # never independently verified.
    USER_FILE = "user_file"


class SourceAccessMethod(str, enum.Enum):
    """How a source's data is actually obtained -- distinct from SourceType
    (what kind of thing the source is). E.g. REGISTRY_DATA_PROVIDER sources
    are typically OFFICIAL_API; WEBSITE sources are typically
    SCRAPED_PUBLIC_PAGE; USER_FILE sources are USER_UPLOADED_FILE. See
    docs/multi_source_architecture.md Section G."""

    OFFICIAL_API = "official_api"
    SCRAPED_PUBLIC_PAGE = "scraped_public_page"
    GOVERNMENT_OPEN_DATA = "government_open_data"
    USER_UPLOADED_FILE = "user_uploaded_file"
    UNKNOWN = "unknown"


class SourceComplianceStatus(str, enum.Enum):
    """A source's real, human-reviewed access status -- distinct from
    Source.collection_enabled, which only says whether collection is
    switched on right now. A source can be REQUIRES_LICENSE or
    NOT_AVAILABLE and collection_enabled=False for entirely different
    reasons; this field records *why*, so the two states (not-yet-reviewed
    vs. reviewed-and-blocked) are never conflated. See
    docs/multi_source_architecture.md Section I for how this was assigned
    to Google/LinkedIn/Facebook/Justdial specifically -- none of the four
    are ACTIVE."""

    ACTIVE = "active"  # a permitted mechanism is confirmed; collection_enabled may be True
    UNDER_REVIEW = "under_review"  # default; not yet confirmed either way
    REQUIRES_LICENSE = "requires_license"  # a permitted mechanism exists but needs a license/paid access Super CRM doesn't have yet
    NOT_AVAILABLE = "not_available"  # no permitted automated mechanism exists at all; no adapter should be built


class VerificationType(str, enum.Enum):
    VERIFIED = "verified"
    OBSERVED = "observed"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class CompanyCategory(str, enum.Enum):
    MANUFACTURER = "manufacturer"
    DISTRIBUTOR = "distributor"
    SERVICE_PROVIDER = "service_provider"
    RETAILER = "retailer"
    UNKNOWN = "unknown"


class MatchStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    AUTO_MATCHED = "auto_matched"


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"
