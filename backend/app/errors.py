MARKET_AUTH_ERROR_CODE = "market_service_authentication_failed"
MARKET_AUTH_ERROR_MESSAGE = "Market service authentication failed. No strategy was activated."


class AppError(Exception):
    def __init__(self, status: int, message: str, code: str = "request_failed") -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


class DeltaOrderRejected(AppError):
    """The exchange explicitly rejected the submitted order."""
