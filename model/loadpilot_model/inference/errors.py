"""Input errors shared by inference and its HTTP adapter."""


class PredictionInputError(ValueError):
    def __init__(self, code: str, errors: list[dict]):
        super().__init__(code)
        self.code = code
        self.errors = errors
