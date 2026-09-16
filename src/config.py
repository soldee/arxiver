from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str
    POSTGRES_HOST: str
    POSTGRES_PORT: int

    POSTGRES_ARXIV_TABLE: str
    POSTGRES_RESUMABLES_TABLE: str

    EMBEDDING_MODEL_NAME: str
    EMBEDDING_MODEL_DIM: int

    ARXIV_OAIMPH_URL: str

    LOG_LEVEL: str

    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    @property
    def DB_URL(self) -> str:
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

cfg = Settings()
