import enum


class SourceType(str, enum.Enum):
    WEBSITE = "website"
    GOVERNMENT_DATASET = "government_dataset"
    DIRECTORY = "directory"
    MARKETPLACE = "marketplace"
    PUBLIC_FILING = "public_filing"


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
