from pydantic import BaseModel


class DirectUploadInitRequest(BaseModel):
    provider: str
    model: str
    conversation_id: str
    filename: str
    mimetype: str
    size: int


class DirectUploadCompleteRequest(BaseModel):
    file_id: str
