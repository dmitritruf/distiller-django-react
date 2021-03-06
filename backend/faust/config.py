from typing import List, Optional

from pydantic import AnyHttpUrl, BaseSettings


class Settings(BaseSettings):
    API_URL: AnyHttpUrl = "http://localhost:8000/api/v1"
    API_KEY_NAME: str
    API_KEY: str
    KAFKA_URL: str

    SFAPI_CLIENT_ID: str
    SFAPI_PRIVATE_KEY: str
    SFAPI_GRANT_TYPE: str
    SFAPI_USER: str

    ACQUISITION_USER: str
    JOB_COUNT_SCRIPT_PATH: str
    JOB_NCEMHUB_RAW_DATA_PATH: str
    JOB_NCEMHUB_COUNT_DATA_PATH: str
    JOB_SCRIPT_DIRECTORY: str
    JOB_BBCP_NUMBER_OF_STREAMS: int
    JOB_QOS: str
    JOB_QOS_FILTER: str
    JOB_BBCP_EXECUTABLE_PATH: str
    JOB_MACHINE_OVERRIDES_PATH: Optional[str]

    HAADF_IMAGE_UPLOAD_DIR: str
    HAADF_IMAGE_UPLOAD_DIR_EXPIRATION_HOURS: int
    HAADF_NCEMHUB_DM4_DATA_PATH: str

    CUSTODIAN_USER: str
    CUSTODIAN_PRIVATE_KEY: str
    CUSTODIAN_VALID_HOSTS: List[str] = []

    class Config:
        case_sensitive = True
        env_file = ".env"


settings = Settings()
