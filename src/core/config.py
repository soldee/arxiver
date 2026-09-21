from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import BaseModel
from zoneinfo import ZoneInfo

class APISettings(BaseModel):
    HOST: str = "0.0.0.0"
    PORT: int = 8000

class ETLSettings(BaseModel):
    ARXIV_OAIMPH_URL: str

    EMBEDDING_MODEL_DIM: int
    MODEL_BATCH_SIZE: int
    PYTORCH_DEVICE: str

    LOG_LEVEL: str
    TIMEZONE: ZoneInfo

class Settings(BaseSettings):
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    POSTGRES_HOST: str
    POSTGRES_PORT: int

    POSTGRES_ARXIV_TABLE: str
    POSTGRES_RESUMABLES_TABLE: str

    EMBEDDING_MODEL_NAME: str

    api: APISettings = APISettings()
    etl: ETLSettings

    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', env_nested_delimiter='__', extra='ignore')

    @property
    def DB_URL(self) -> str:
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

cfg = Settings()
