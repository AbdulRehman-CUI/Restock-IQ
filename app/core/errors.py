from dataclasses import asdict, dataclass


@dataclass
class Issue:
    file: str
    row: int | None
    field: str
    code: str
    message: str


class Issues:
    def __init__(self, limit=100):
        self.items: list[Issue] = []
        self.total = 0
        self.limit = limit

    def add(self, file, row, field, code, message):
        self.total += 1
        if len(self.items) < self.limit:
            self.items.append(Issue(file, row, field, code, message))

    def payload(self):
        return {"error_count": self.total, "errors": [asdict(i) for i in self.items],
                "truncated": self.total > len(self.items)}


class DatasetValidationError(Exception):
    def __init__(self, issues: Issues):
        self.issues = issues
        super().__init__(f"Dataset has {issues.total} validation errors")


class NotFound(Exception):
    pass


class JobConflict(Exception):
    pass


class StateConflict(Exception):
    """A valid request cannot use the current dataset/model/artifact state."""


class ArtifactError(StateConflict):
    pass
