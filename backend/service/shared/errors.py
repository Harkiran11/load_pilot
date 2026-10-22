class ProductError(Exception):
    def __init__(self, status_code: int, detail: str, *, evaluation: dict | None = None, feedback: dict | None = None):
        self.status_code = status_code
        self.detail = detail
        self.evaluation = evaluation
        self.feedback = feedback
        super().__init__(detail)
