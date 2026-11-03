class LLMError(Exception):
    pass


class LLMTransportError(LLMError):
    pass


class LLMUnexpectedResponse(LLMError):
    pass
