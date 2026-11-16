from pydantic import BaseModel, Field


class MetricInput(BaseModel):
    code: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=2, max_length=160)
    description: str = Field(default='', max_length=500)
    expression: str = Field(min_length=1, max_length=300)
    note: str = Field(default='', max_length=500)


class MetricDraftInput(BaseModel):
    expression: str = Field(min_length=1, max_length=300)
    note: str = Field(default='', max_length=500)
    expected_revision: int | None = None


class MetricPublishInput(BaseModel):
    note: str = Field(default='', max_length=500)
    expected_revision: int | None = None


class MetricTrialInput(BaseModel):
    dataset_id: int
    expression: str | None = Field(default=None, min_length=1, max_length=300)
    group_by: str = Field(default='channel')
    zero_policy: str = Field(default='reject')


class MetricStatusInput(BaseModel):
    status: str = Field(pattern='^(active|disabled)$')
