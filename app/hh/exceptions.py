class HHError(Exception):
    pass


class HHTransportError(HHError):
    pass


class HHAPIError(HHError):
    def __init__(self, status_code: int, error_types: list[str]):
        super().__init__(f"HTTP {status_code}: {', '.join(error_types) or 'no details'}")
        self.status_code = status_code


class HHUnexpectedResponse(HHError):
    pass


class HHNotConnected(HHError):
    pass
