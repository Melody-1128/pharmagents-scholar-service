class ScholarError(Exception):
    pass


class ProviderTimeout(ScholarError):
    pass


class ProviderRateLimited(ScholarError):
    pass


class ProviderUnavailable(ScholarError):
    pass


class PaperNotFound(ScholarError):
    pass


class FullTextNotAvailable(ScholarError):
    pass


class ParseFailed(ScholarError):
    pass


class LicenseRestricted(ScholarError):
    pass


class InvalidIdentifier(ScholarError):
    pass
