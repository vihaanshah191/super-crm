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
